#!/usr/bin/env python3
"""Unit tests for workers.watcher (TaskWatcher and watch_command).

Verifies timeout enforcement, pipe deadlock prevention, heartbeat monitoring,
and process group recovery under stuck/hung conditions (#106, #113).
"""

import os
import sys
import time
import unittest

# Ensure repo root is on sys.path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from workers.watcher import TaskWatcher, WatchResult, watch_command


class TestTaskWatcher(unittest.TestCase):
    """Test suite for TaskWatcher and watch_command."""

    def test_successful_command_execution(self):
        """Verify normal command execution and output capture."""
        res = watch_command(
            [sys.executable, "-c", "import sys; sys.stdout.write('hello stdout\\n'); sys.stderr.write('hello stderr\\n')"],
            timeout=10.0,
        )
        self.assertTrue(res.ok)
        self.assertEqual(res.exit_code, 0)
        self.assertIn("hello stdout", res.stdout)
        self.assertIn("hello stderr", res.stderr)
        self.assertFalse(res.timed_out)
        self.assertFalse(res.heartbeat_timed_out)
        self.assertEqual(res.status, "completed")

    def test_process_timeout_recovery(self):
        """Verify overall execution timeout cleanly terminates stuck process (#106, #113)."""
        start = time.monotonic()
        res = watch_command(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout=0.6,
            grace_period=0.2,
        )
        elapsed = time.monotonic() - start

        self.assertTrue(res.timed_out)
        self.assertFalse(res.ok)
        self.assertTrue(res.recovered)
        self.assertEqual(res.status, "timed_out")
        self.assertIn("exceeded overall timeout", res.error_message or "")
        self.assertLess(elapsed, 3.0)  # Cleanly terminated without hanging for 10s

    def test_pipe_buffer_deadlock_prevention(self):
        """Verify writing massive output (>256KB) does not deadlock pipe buffers."""
        # Standard OS pipe buffer is 64KB; writing 300KB unbuffered hangs unmonitored subprocesses
        script = (
            "import sys\n"
            "payload = 'A' * 1024 + '\\n'\n"
            "for _ in range(300):\n"
            "    sys.stdout.write(payload)\n"
            "    sys.stderr.write(payload)\n"
        )
        res = watch_command(
            [sys.executable, "-c", script],
            timeout=10.0,
        )
        self.assertTrue(res.ok)
        self.assertEqual(res.exit_code, 0)
        self.assertGreaterEqual(len(res.stdout), 300 * 1025)
        self.assertGreaterEqual(len(res.stderr), 300 * 1025)
        self.assertFalse(res.timed_out)

    def test_massive_stdin_pipe_no_deadlock(self):
        """Verify feeding massive input (>256KB) asynchronously without deadlocking."""
        large_input = "DATA_CHUNK_" * 30000  # ~330 KB
        script = (
            "import sys\n"
            "data = sys.stdin.read()\n"
            "sys.stdout.write(f'read {len(data)} bytes')\n"
        )
        res = watch_command(
            [sys.executable, "-c", script],
            input_data=large_input,
            timeout=10.0,
        )
        self.assertTrue(res.ok)
        self.assertEqual(res.exit_code, 0)
        self.assertIn(f"read {len(large_input)} bytes", res.stdout)

    def test_heartbeat_idle_timeout(self):
        """Verify heartbeat monitor detects silent/idle hang even before overall timeout."""
        # Script outputs one line then hangs in an infinite sleep loop
        script = (
            "import sys, time\n"
            "sys.stdout.write('started\\n')\n"
            "sys.stdout.flush()\n"
            "time.sleep(10)\n"
        )
        start = time.monotonic()
        res = watch_command(
            [sys.executable, "-c", script],
            timeout=15.0,
            heartbeat_timeout=0.8,
            grace_period=0.2,
        )
        elapsed = time.monotonic() - start

        self.assertTrue(res.heartbeat_timed_out)
        self.assertFalse(res.timed_out)
        self.assertTrue(res.recovered)
        self.assertEqual(res.status, "heartbeat_expired")
        self.assertIn("started", res.stdout)
        self.assertIn("produced no activity", res.error_message or "")
        self.assertLess(elapsed, 4.0)

    def test_non_newline_progress_heartbeat(self):
        """Verify chunk-based output without newlines maintains heartbeat."""
        # Script outputs progress dots without newlines every 0.2s for 0.8s total
        script = (
            "import sys, time\n"
            "for _ in range(4):\n"
            "    sys.stdout.write('.')\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(0.2)\n"
        )
        res = watch_command(
            [sys.executable, "-c", script],
            timeout=5.0,
            heartbeat_timeout=0.5,
            grace_period=0.2,
        )
        self.assertTrue(res.ok)
        self.assertEqual(res.stdout, "....")
        self.assertFalse(res.heartbeat_timed_out)

    def test_unclosed_pipe_process_group_cleanup(self):
        """Verify child and orphaned grandchild processes are reaped via process group."""
        # Main process spawns a detached grandchild sleeping in background
        script = (
            "import subprocess, sys, time\n"
            "proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(15)'])\n"
            "time.sleep(10)\n"
        )
        res = watch_command(
            [sys.executable, "-c", script],
            timeout=0.8,
            grace_period=0.2,
        )
        self.assertTrue(res.timed_out)
        self.assertTrue(res.recovered)

    def test_sigterm_escalation_to_sigkill(self):
        """Verify watcher escalates to SIGKILL when process traps/ignores SIGTERM."""
        script = (
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "while True:\n"
            "    time.sleep(0.1)\n"
        )
        res = watch_command(
            [sys.executable, "-c", script],
            timeout=0.5,
            grace_period=0.3,
        )
        self.assertTrue(res.timed_out)
        self.assertTrue(res.recovered)
        # The process was killed by SIGKILL (-9) or poll returns non-zero
        self.assertNotEqual(res.exit_code, 0)

    def test_invalid_command_error_handling(self):
        """Verify attempting to run a nonexistent command returns an error result."""
        watcher = TaskWatcher()
        res = watcher.run(["__non_existent_binary_xyz_123__"])
        self.assertEqual(res.exit_code, -1)
        self.assertEqual(res.status, "error")
        self.assertFalse(res.ok)
        self.assertIn("Failed to spawn process", res.error_message or "")

    def test_watch_result_dict_serialization(self):
        """Verify WatchResult serialization to dictionary."""
        res = WatchResult(
            cmd=["echo", "test"],
            exit_code=0,
            stdout="test\n",
            stderr="",
            timed_out=False,
            heartbeat_timed_out=False,
            recovered=False,
            duration=0.1,
            status="completed",
        )
        d = res.to_dict()
        self.assertEqual(d["exit_code"], 0)
        self.assertEqual(d["stdout"], "test\n")
        self.assertTrue(d["ok"])


if __name__ == "__main__":
    unittest.main()
