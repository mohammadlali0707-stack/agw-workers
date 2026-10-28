#!/usr/bin/env python3
"""Task Watcher and Process Monitor.

Provides robust process execution, timeout enforcement, pipe deadlock prevention,
heartbeat liveness monitoring, and clean process group recovery.

Designed to eliminate stuck watch tasks caused by:
1. Missing execution timeout (#106, #113)
2. Pipe buffer deadlocks when processes produce large unbuffered output or stdin
3. Hangs/deadlocks in subtasks lacking activity/heartbeat
4. Orphan processes keeping pipes or files open after parent termination
"""

import dataclasses
import os
import signal
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Union


@dataclasses.dataclass
class WatchResult:
    """Structured result of a watched process execution."""

    cmd: Union[List[str], str]
    exit_code: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool = False
    heartbeat_timed_out: bool = False
    recovered: bool = False
    duration: float = 0.0
    status: str = "completed"
    error_message: Optional[str] = None

    @property
    def ok(self) -> bool:
        """Returns True if the process completed normally with exit code 0."""
        return self.exit_code == 0 and not self.timed_out and not self.heartbeat_timed_out

    def to_dict(self) -> Dict[str, Any]:
        """Convert the result to a serializable dictionary."""
        d = dataclasses.asdict(self)
        d["ok"] = self.ok
        return d


class TaskWatcher:
    """Manages process execution with timeout, heartbeat, and pipe deadlock prevention."""

    def __init__(
        self,
        default_timeout: Optional[float] = 300.0,
        default_heartbeat_timeout: Optional[float] = None,
        grace_period: float = 2.0,
    ):
        """Initialize the TaskWatcher.

        Args:
            default_timeout: Maximum overall runtime in seconds (None for unlimited).
            default_heartbeat_timeout: Maximum seconds without output/heartbeat before declaring stuck.
            grace_period: Seconds to wait after SIGTERM before escalating to SIGKILL.
        """
        self.default_timeout = default_timeout
        self.default_heartbeat_timeout = default_heartbeat_timeout
        self.grace_period = grace_period

    def run(
        self,
        cmd: Union[List[str], str],
        timeout: Optional[float] = None,
        heartbeat_timeout: Optional[float] = None,
        grace_period: Optional[float] = None,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        input_data: Optional[Union[str, bytes]] = None,
        shell: bool = False,
        on_output: Optional[Callable[[str, bool], None]] = None,
    ) -> WatchResult:
        """Execute command in a new process group with monitoring and recovery.

        Args:
            cmd: Command list or string.
            timeout: Execution timeout in seconds (falls back to default_timeout).
            heartbeat_timeout: Max idle seconds between output chunks (falls back to default).
            grace_period: Seconds between SIGTERM and SIGKILL during termination.
            cwd: Working directory.
            env: Environment dictionary.
            input_data: Optional string or bytes to feed to stdin.
            shell: Whether to execute via shell.
            on_output: Optional callback ``on_output(text, is_stderr)`` invoked for each chunk.

        Returns:
            WatchResult object detailing outcome, captured output, and recovery status.
        """
        effective_timeout = timeout if timeout is not None else self.default_timeout
        effective_heartbeat = (
            heartbeat_timeout if heartbeat_timeout is not None else self.default_heartbeat_timeout
        )
        effective_grace = grace_period if grace_period is not None else self.grace_period

        start_time = time.monotonic()
        timed_out = False
        heartbeat_timed_out = False
        recovered = False
        status = "completed"
        error_message = None

        # Prepare pipes
        stdin_pipe = subprocess.PIPE if input_data is not None else subprocess.DEVNULL
        stdout_chunks: List[str] = []
        stderr_chunks: List[str] = []
        last_activity = [time.monotonic()]
        lock = threading.Lock()

        # Start process in a new process group/session so all children can be reaped
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=stdin_pipe,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                env=env,
                shell=shell,
                start_new_session=True,
                bufsize=0,
            )
        except Exception as exc:
            duration = time.monotonic() - start_time
            return WatchResult(
                cmd=cmd,
                exit_code=-1,
                stdout="",
                stderr="",
                timed_out=False,
                heartbeat_timed_out=False,
                recovered=False,
                duration=duration,
                status="error",
                error_message=f"Failed to spawn process: {exc}",
            )

        # Cache process group ID immediately (with start_new_session, pgid == proc.pid)
        pgid = proc.pid

        # Asynchronously write stdin in a background thread to prevent buffer deadlock
        t_in = None
        if input_data is not None and proc.stdin:
            raw_input = input_data.encode("utf-8") if isinstance(input_data, str) else input_data

            def stdin_writer(stream, data: bytes):
                try:
                    stream.write(data)
                    stream.flush()
                except (BrokenPipeError, OSError):
                    pass
                finally:
                    try:
                        stream.close()
                    except Exception:
                        pass

            t_in = threading.Thread(target=stdin_writer, args=(proc.stdin, raw_input), daemon=True)
            t_in.start()

        # Asynchronous chunk-based pipe readers to prevent buffer deadlocks
        def reader(stream, chunks: List[str], is_stderr: bool):
            try:
                # Use chunk-based read (4096 bytes) instead of readline to avoid stalling on partial lines
                for chunk in iter(lambda: stream.read(4096), b""):
                    decoded = chunk.decode("utf-8", errors="replace")
                    with lock:
                        chunks.append(decoded)
                        last_activity[0] = time.monotonic()
                    if on_output:
                        try:
                            on_output(decoded, is_stderr)
                        except Exception:
                            pass
            except Exception:
                pass
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

        t_out = threading.Thread(target=reader, args=(proc.stdout, stdout_chunks, False), daemon=True)
        t_err = threading.Thread(target=reader, args=(proc.stderr, stderr_chunks, True), daemon=True)
        t_out.start()
        t_err.start()

        # Monitor loop
        poll_interval = 0.05
        while True:
            ret = proc.poll()
            if ret is not None:
                break

            now = time.monotonic()
            elapsed = now - start_time

            # Check overall timeout
            if effective_timeout is not None and elapsed > effective_timeout:
                timed_out = True
                status = "timed_out"
                error_message = f"Process exceeded overall timeout of {effective_timeout}s"
                break

            # Check heartbeat/idle timeout
            with lock:
                idle = now - last_activity[0]
            if effective_heartbeat is not None and idle > effective_heartbeat:
                heartbeat_timed_out = True
                status = "heartbeat_expired"
                error_message = (
                    f"Process produced no activity for {idle:.1f}s (limit: {effective_heartbeat}s)"
                )
                break

            time.sleep(poll_interval)

        # Handle process termination and recovery if timed out or heartbeat expired
        if timed_out or heartbeat_timed_out:
            recovered = self._kill_process_group(pgid, proc, effective_grace)

        # Wait for readers to drain remaining pipe data, closing streams explicitly if stalled
        t_out.join(timeout=1.5)
        t_err.join(timeout=1.5)
        if t_in and t_in.is_alive():
            t_in.join(timeout=0.5)

        # Explicitly close pipe descriptors from parent side in case orphans hold them
        for stream in (proc.stdout, proc.stderr, proc.stdin):
            if stream and not getattr(stream, "closed", True):
                try:
                    stream.close()
                except Exception:
                    pass

        # Ensure leader process is reaped
        try:
            exit_code = proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            self._kill_process_group(pgid, proc, grace_period=0.5, force=True)
            exit_code = proc.poll()

        # Clean up any lingering background orphan processes in the group
        if self._is_group_alive(pgid):
            self._kill_process_group(pgid, proc, grace_period=0.5, force=True)
            recovered = True

        duration = time.monotonic() - start_time
        stdout_str = "".join(stdout_chunks)
        stderr_str = "".join(stderr_chunks)

        return WatchResult(
            cmd=cmd,
            exit_code=exit_code,
            stdout=stdout_str,
            stderr=stderr_str,
            timed_out=timed_out,
            heartbeat_timed_out=heartbeat_timed_out,
            recovered=recovered,
            duration=duration,
            status=status,
            error_message=error_message,
        )

    def _is_group_alive(self, pgid: int) -> bool:
        """Check whether any process in the process group is still alive."""
        try:
            os.killpg(pgid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False
        except OSError:
            return False

    def _kill_process_group(
        self, pgid: int, proc: subprocess.Popen, grace_period: float, force: bool = False
    ) -> bool:
        """Kill entire process group with SIGTERM escalation to SIGKILL, checking group liveness."""
        if not self._is_group_alive(pgid):
            return True

        if not force:
            try:
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                return True

            deadline = time.monotonic() + grace_period
            while time.monotonic() < deadline:
                if not self._is_group_alive(pgid):
                    return True
                time.sleep(0.05)

        # Escalate to SIGKILL for the entire process group
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass

        try:
            proc.wait(timeout=1.0)
        except Exception:
            pass

        return True


def watch_command(
    cmd: Union[List[str], str],
    timeout: Optional[float] = 300.0,
    heartbeat_timeout: Optional[float] = None,
    grace_period: float = 2.0,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    input_data: Optional[Union[str, bytes]] = None,
    shell: bool = False,
    on_output: Optional[Callable[[str, bool], None]] = None,
) -> WatchResult:
    """Convenience helper to run and monitor a command under TaskWatcher.

    Args:
        cmd: Command or argv list.
        timeout: Maximum run time in seconds.
        heartbeat_timeout: Maximum idle seconds between output chunks.
        grace_period: Seconds between SIGTERM and SIGKILL escalation.
        cwd: Directory to run within.
        env: Environment variables.
        input_data: Standard input payload.
        shell: Execute inside shell.
        on_output: Stream callback.

    Returns:
        WatchResult details.
    """
    watcher = TaskWatcher(
        default_timeout=timeout,
        default_heartbeat_timeout=heartbeat_timeout,
        grace_period=grace_period,
    )
    return watcher.run(
        cmd=cmd,
        timeout=timeout,
        heartbeat_timeout=heartbeat_timeout,
        grace_period=grace_period,
        cwd=cwd,
        env=env,
        input_data=input_data,
        shell=shell,
        on_output=on_output,
    )
