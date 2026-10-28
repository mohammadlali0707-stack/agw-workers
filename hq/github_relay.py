#!/usr/bin/env python3
"""hq/github_relay.py

VPS-side bidirectional synchronization daemon bridging the local LeadDesk
Cluster Chat SQLite database (/opt/hq/cluster_chat.db) and local SSE/HTTP
endpoints with the GitHub repository relay queue (relay_queue/).

Supports:
- Dynamic SQLite schema initialization and adaptation for existing tables
- GitHub REST API client using pure urllib (zero external dependencies)
- Conditional GET with ETag caching (HTTP 304 consumes 0 rate limit units)
- Safe concurrency handling with 409 Conflict automatic SHA refresh retry
- End-to-end AES-256-GCM symmetric encryption via relay_protocol
- Regex secret scrubbing before state leaves the host
- Asynchronous /web command detection and workflow dispatching
- Attachment chunking and synchronization
- CLI modes: --status, --once, --daemon with graceful signal handling
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import signal
import sqlite3
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

# ==============================================================================
# Dynamic sys.path Setup & Protocol Import
# ==============================================================================

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent if (_HERE.parent / "cluster_chat").exists() else _HERE

for _path in ["/opt/hq", str(_REPO_ROOT), str(_HERE)]:
    if _path not in sys.path:
        sys.path.insert(0, _path)

try:
    from cluster_chat import relay_protocol as rp
except ImportError:
    try:
        import relay_protocol as rp  # type: ignore
    except ImportError as _err:
        raise ImportError(
            f"Failed to import relay_protocol from cluster_chat or sys.path: {_err}"
        ) from _err

# Configure structured logging
logger = logging.getLogger("github_relay")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [github-relay] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _handler.setFormatter(_formatter)
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)


# ==============================================================================
# Configuration Defaults
# ==============================================================================

DEFAULT_REPO = "mohammadlali0707-stack/agw-workers"
DEFAULT_BRANCH = "main"
DEFAULT_DB_PATH = "/opt/hq/cluster_chat.db"
DEFAULT_POLL_INTERVAL = 8.0
DEFAULT_UPLOAD_DIR = "/opt/hq/data/cluster-chat/uploads"
VALID_ROOMS: tuple[str, ...] = ("main", "dm-argon", "workers")


class RelayConfig:
    """Holds runtime configuration derived from environment variables and CLI flags."""

    def __init__(
        self,
        token: str | None = None,
        repo: str | None = None,
        branch: str | None = None,
        db_path: str | None = None,
        api_url: str | None = None,
        shared_key: str | None = None,
        poll_interval: float | None = None,
        upload_dir: str | None = None,
    ):
        self.token: str = (
            token
            if token is not None
            else os.getenv("GITHUB_TOKEN", "").strip()
        )
        self.repo: str = (
            repo
            if repo is not None
            else os.getenv("GITHUB_REPO", DEFAULT_REPO).strip()
        )
        self.branch: str = (
            branch
            if branch is not None
            else os.getenv("GITHUB_BRANCH", DEFAULT_BRANCH).strip()
        )
        self.db_path: str = (
            db_path
            if db_path is not None
            else os.getenv("CLUSTER_CHAT_DB", DEFAULT_DB_PATH).strip()
        )
        self.api_url: str = (
            api_url
            if api_url is not None
            else os.getenv("CLUSTER_CHAT_API_URL", "").strip()
        )
        self.shared_key: str = (
            shared_key
            if shared_key is not None
            else os.getenv("RELAY_SHARED_KEY", "").strip()
        )
        raw_interval = (
            poll_interval
            if poll_interval is not None
            else os.getenv("RELAY_POLL_INTERVAL", str(DEFAULT_POLL_INTERVAL))
        )
        try:
            self.poll_interval: float = max(1.0, float(raw_interval))
        except (ValueError, TypeError):
            self.poll_interval = DEFAULT_POLL_INTERVAL

        self.upload_dir: str = (
            upload_dir
            if upload_dir is not None
            else os.getenv("CLUSTER_CHAT_UPLOAD_DIR", DEFAULT_UPLOAD_DIR).strip()
        )

    def sanitized_dict(self) -> dict[str, Any]:
        """Return non-sensitive config dictionary for diagnostics."""
        return {
            "repo": self.repo,
            "branch": self.branch,
            "db_path": self.db_path,
            "api_url": self.api_url or "(none)",
            "encryption_enabled": bool(self.shared_key),
            "poll_interval_sec": self.poll_interval,
            "has_token": bool(self.token),
            "upload_dir": self.upload_dir,
        }


# ==============================================================================
# SQLite Schema Initialization & Dynamic Adaptation
# ==============================================================================

def init_db(conn: sqlite3.Connection) -> None:
    """Initialize local SQLite tables and adapt dynamically to existing schemas.

    Ensures:
      1. `messages` table exists with compatible columns:
         id, msg_id, room, sender, body (or text), attachments_json, created_at, synced_to_github
      2. `relay_sync_state` table exists for deduplication and idempotency
      3. `relay_etag_cache` table exists for conditional GitHub REST API queries
    """
    cursor = conn.cursor()

    # 1. Inspect existing tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
    messages_table_exists = cursor.fetchone() is not None

    if not messages_table_exists:
        cursor.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                msg_id TEXT UNIQUE,
                room TEXT NOT NULL,
                sender TEXT NOT NULL,
                body TEXT NOT NULL,
                attachments_json TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                synced_to_github INTEGER DEFAULT 0
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_msg_id ON messages(msg_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_room ON messages(room)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at)")
    else:
        # Dynamic inspection of existing messages table columns
        cursor.execute("PRAGMA table_info(messages)")
        cols_info = cursor.fetchall()
        existing_cols = {col[1].lower(): col[1] for col in cols_info}

        if "msg_id" not in existing_cols:
            cursor.execute("ALTER TABLE messages ADD COLUMN msg_id TEXT")
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_msg_id ON messages(msg_id)")

        if "body" not in existing_cols:
            cursor.execute("ALTER TABLE messages ADD COLUMN body TEXT DEFAULT ''")
            if "text" in existing_cols:
                cursor.execute("UPDATE messages SET body = text WHERE (body IS NULL OR body = '') AND text IS NOT NULL")

        if "attachments_json" not in existing_cols:
            cursor.execute("ALTER TABLE messages ADD COLUMN attachments_json TEXT DEFAULT '[]'")

        if "synced_to_github" not in existing_cols:
            cursor.execute("ALTER TABLE messages ADD COLUMN synced_to_github INTEGER DEFAULT 0")

        if "room" not in existing_cols:
            cursor.execute("ALTER TABLE messages ADD COLUMN room TEXT DEFAULT 'main'")

        if "sender" not in existing_cols:
            cursor.execute("ALTER TABLE messages ADD COLUMN sender TEXT DEFAULT 'system'")

        if "created_at" not in existing_cols:
            cursor.execute("ALTER TABLE messages ADD COLUMN created_at TEXT DEFAULT ''")

    # 2. Dedup table: relay_sync_state
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS relay_sync_state (
            msg_id TEXT PRIMARY KEY,
            room TEXT NOT NULL,
            direction TEXT NOT NULL CHECK(direction IN ('inbound', 'outbound')),
            github_path TEXT DEFAULT '',
            synced_at TEXT NOT NULL
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_relay_sync_state_room ON relay_sync_state(room)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_relay_sync_state_synced_at ON relay_sync_state(synced_at)")

    # 3. ETag cache table: relay_etag_cache
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS relay_etag_cache (
            endpoint TEXT PRIMARY KEY,
            etag TEXT NOT NULL,
            cached_json TEXT DEFAULT '',
            updated_at TEXT NOT NULL
        )
        """
    )

    conn.commit()


# ==============================================================================
# GitHub REST API Queue Client (Zero-Dependency urllib)
# ==============================================================================

class GitHubQueueClient:
    """Zero-dependency GitHub Contents and Workflow Dispatches API client.

    Provides conditional GET with ETag caching, atomic PUT with 409 Conflict
    SHA refresh and exponential retry, and asynchronous workflow dispatch.
    """

    def __init__(self, token: str, repo: str, branch: str = "main", conn: sqlite3.Connection | None = None):
        self.token = token.strip()
        self.repo = repo.strip()
        self.branch = branch.strip()
        self.conn = conn
        self.base_url = f"https://api.github.com/repos/{self.repo}"
        self.last_rate_limit_remaining: int | None = None
        self.last_rate_limit_reset: int | None = None

    def _headers(self, custom_headers: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "cluster-chat-vps-relay/1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if custom_headers:
            headers.update(custom_headers)
        return headers

    def _track_rate_limits(self, resp_headers: Any) -> None:
        """Inspect and record GitHub API rate limit response headers."""
        if not resp_headers:
            return
        rem = resp_headers.get("x-ratelimit-remaining")
        reset = resp_headers.get("x-ratelimit-reset")
        if rem is not None:
            try:
                self.last_rate_limit_remaining = int(rem)
            except (ValueError, TypeError):
                pass
        if reset is not None:
            try:
                self.last_rate_limit_reset = int(reset)
            except (ValueError, TypeError):
                pass

        if self.last_rate_limit_remaining is not None and self.last_rate_limit_remaining < 20:
            logger.warning(
                f"GitHub API quota low: {self.last_rate_limit_remaining} requests remaining"
            )

    def get_contents(
        self,
        path: str,
        use_cache: bool = True,
    ) -> tuple[int, Any, dict[str, str]]:
        """Fetch repository contents for path using conditional ETag caching.

        Returns:
          (status_code, parsed_json_or_none, headers_dict)
          status_code: 200 (fresh), 304 (not modified, returns cached), 404 (not found)
        """
        clean_path = path.lstrip("/")
        encoded_path = urllib.parse.quote(clean_path, safe="/")
        url = f"{self.base_url}/contents/{encoded_path}?ref={urllib.parse.quote(self.branch)}"

        cached_etag = None
        cached_json = None
        if use_cache and self.conn:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT etag, cached_json FROM relay_etag_cache WHERE endpoint = ?",
                (url,),
            )
            row = cursor.fetchone()
            if row:
                cached_etag, cached_json = row[0], row[1]

        req_headers = self._headers()
        if cached_etag and use_cache:
            req_headers["If-None-Match"] = cached_etag

        req = urllib.request.Request(url, headers=req_headers, method="GET")

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                status = resp.status
                self._track_rate_limits(resp.headers)
                raw_bytes = resp.read()
                raw_text = raw_bytes.decode("utf-8")
                etag = resp.headers.get("ETag")
                parsed = json.loads(raw_text) if raw_text else None

                if self.conn and etag:
                    cursor = self.conn.cursor()
                    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    cursor.execute(
                        """
                        INSERT INTO relay_etag_cache (endpoint, etag, cached_json, updated_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(endpoint) DO UPDATE SET
                            etag = excluded.etag,
                            cached_json = excluded.cached_json,
                            updated_at = excluded.updated_at
                        """,
                        (url, etag, raw_text, now_iso),
                    )
                    self.conn.commit()

                return status, parsed, dict(resp.headers)

        except urllib.error.HTTPError as err:
            self._track_rate_limits(err.headers)
            if err.code == 304:
                # 304 Not Modified: return cached data
                parsed = None
                if cached_json:
                    try:
                        parsed = json.loads(cached_json)
                    except Exception:
                        parsed = None
                if self.conn:
                    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    self.conn.cursor().execute(
                        "UPDATE relay_etag_cache SET updated_at = ? WHERE endpoint = ?",
                        (now_iso, url),
                    )
                    self.conn.commit()
                return 304, parsed, dict(err.headers)

            if err.code == 404:
                return 404, None, dict(err.headers)

            # Secondary rate limiting or quota exhaustion
            if err.code in (403, 429):
                logger.warning(f"GitHub API rate limit or permission error ({err.code}): {err.reason}")

            raise

        except urllib.error.URLError as err:
            logger.warning(f"Network error querying GitHub {url}: {err.reason}")
            raise

    def get_file_content(self, item_dict: dict[str, Any]) -> str:
        """Extract and decode UTF-8 text from a GitHub contents file item."""
        if not isinstance(item_dict, dict):
            raise ValueError("Expected dictionary for file item")

        # 1. If base64 content is embedded in response
        if item_dict.get("encoding") == "base64" and item_dict.get("content"):
            raw_b64 = item_dict["content"].replace("\n", "").replace("\r", "")
            return base64.b64decode(raw_b64).decode("utf-8")

        # 2. Download from download_url if present
        download_url = item_dict.get("download_url")
        if download_url:
            req = urllib.request.Request(
                download_url,
                headers=self._headers(),
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=25) as resp:
                return resp.read().decode("utf-8")

        # 3. Fallback to querying item['path'] directly
        if item_dict.get("path"):
            status, fresh_item, _ = self.get_contents(item_dict["path"], use_cache=False)
            if status == 200 and isinstance(fresh_item, dict) and fresh_item.get("content"):
                raw_b64 = fresh_item["content"].replace("\n", "").replace("\r", "")
                return base64.b64decode(raw_b64).decode("utf-8")

        raise ValueError(f"Unable to extract content from GitHub item {item_dict.get('name')}")

    def put_file(
        self,
        path: str,
        content_bytes: bytes,
        commit_message: str,
        sha: str | None = None,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        """Commit a file to GitHub repository using PUT /repos/{owner}/{repo}/contents/{path}.

        Handles concurrent commit conflicts (HTTP 409) automatically by fetching
        the latest remote file SHA and retrying up to max_retries times.
        """
        clean_path = path.lstrip("/")
        encoded_path = urllib.parse.quote(clean_path, safe="/")
        url = f"{self.base_url}/contents/{encoded_path}"

        current_sha = sha
        current_attempt = 0

        while current_attempt <= max_retries:
            b64_content = base64.b64encode(content_bytes).decode("ascii")
            payload: dict[str, Any] = {
                "message": rp.scrub_secrets(commit_message),
                "content": b64_content,
                "branch": self.branch,
            }
            if current_sha:
                payload["sha"] = current_sha

            req_body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=req_body,
                headers=self._headers({"Content-Type": "application/json"}),
                method="PUT",
            )

            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    self._track_rate_limits(resp.headers)
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    return resp_data

            except urllib.error.HTTPError as err:
                self._track_rate_limits(err.headers)
                if err.code == 409 and current_attempt < max_retries:
                    # 409 Conflict: another committer updated the file or SHA changed.
                    current_attempt += 1
                    logger.info(
                        f"HTTP 409 Conflict on {path}. Refreshing remote SHA (attempt {current_attempt}/{max_retries})..."
                    )
                    time.sleep(0.5 * current_attempt)
                    status, existing_item, _ = self.get_contents(clean_path, use_cache=False)
                    if status == 200 and isinstance(existing_item, dict):
                        current_sha = existing_item.get("sha")
                    else:
                        current_sha = None
                    continue

                logger.error(
                    f"GitHub PUT {path} failed ({err.code}): {err.reason}"
                )
                raise

        raise RuntimeError(f"Exceeded max retries committing {path} due to persistent 409 Conflict")

    def delete_file(
        self,
        path: str,
        sha: str,
        commit_message: str,
    ) -> bool:
        """Delete a file from GitHub repository using DELETE /repos/{owner}/{repo}/contents/{path}."""
        clean_path = path.lstrip("/")
        encoded_path = urllib.parse.quote(clean_path, safe="/")
        url = f"{self.base_url}/contents/{encoded_path}"

        payload = {
            "message": rp.scrub_secrets(commit_message),
            "sha": sha,
            "branch": self.branch,
        }
        req_body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=req_body,
            headers=self._headers({"Content-Type": "application/json"}),
            method="DELETE",
        )

        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                self._track_rate_limits(resp.headers)
                return resp.status in (200, 204)
        except urllib.error.HTTPError as err:
            self._track_rate_limits(err.headers)
            if err.code == 404:
                # Already deleted
                return True
            logger.warning(f"GitHub DELETE {path} failed ({err.code}): {err.reason}")
            return False

    def dispatch_web_bridge(self, request_payload: dict[str, Any]) -> bool:
        """Trigger the web-bridge.yml workflow via GitHub Actions workflow dispatches API."""
        url = f"{self.base_url}/actions/workflows/web-bridge.yml/dispatches"
        req_inputs = {
            "url": str(request_payload.get("url", "")),
            "room": str(request_payload.get("room", "main")),
            "mode": str(request_payload.get("mode", "markdown")),
            "max_bytes": str(request_payload.get("max_bytes", rp.DEFAULT_WEB_MAX_BYTES_TEXT)),
            "request_id": str(request_payload.get("request_id", "")),
        }
        body = json.dumps({"ref": self.branch, "inputs": req_inputs}).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=body,
            headers=self._headers({"Content-Type": "application/json"}),
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                self._track_rate_limits(resp.headers)
                logger.info(f"Dispatched web-bridge.yml for request {req_inputs['request_id']}")
                return resp.status in (200, 204)
        except urllib.error.HTTPError as err:
            self._track_rate_limits(err.headers)
            logger.warning(
                f"Failed to dispatch web-bridge.yml ({err.code}): {err.reason}. "
                "Queue file will remain for runner pickup."
            )
            return False


# ==============================================================================
# Inbound Sync: GitHub Queue -> VPS SQLite & Cluster Chat API
# ==============================================================================

def sync_inbound_from_github(
    client: GitHubQueueClient,
    conn: sqlite3.Connection,
    config: RelayConfig,
) -> int:
    """Download, decrypt, deduplicate, and ingest pending inbound messages.

    Processes:
      1. `relay_queue/inbox/msg_*.json` (authored by web UI / Mohammad)
      2. `relay_queue/outbox/web_*.json` (results published by web-bridge runner)

    Actions:
      - Validates envelope & decrypts payload if encrypted.
      - Checks `relay_sync_state` by `msg_id` for deduplication.
      - Scrubs secrets and inserts into SQLite `messages`.
      - Optionally broadcasts to `CLUSTER_CHAT_API_URL` for live SSE listeners.
      - Dispatches `web-bridge.yml` workflow if message is `/web <url>`.
      - Deletes processed files from `relay_queue/inbox/`.

    Returns:
      Number of newly ingested messages.
    """
    total_ingested = 0

    # 1. Fetch inbox directory listing
    status_inbox, inbox_items, _ = client.get_contents("relay_queue/inbox", use_cache=True)
    files_to_process: list[tuple[dict[str, Any], bool]] = []

    if status_inbox == 200 and isinstance(inbox_items, list):
        for item in inbox_items:
            if isinstance(item, dict) and item.get("type") == "file":
                name = item.get("name", "")
                if name.startswith("msg_") and name.endswith(".json"):
                    files_to_process.append((item, True))  # True = is_inbox (delete after sync)

    # 2. Fetch outbox directory listing for web-bridge runner results (web_*.json)
    status_outbox, outbox_items, _ = client.get_contents("relay_queue/outbox", use_cache=True)
    if status_outbox == 200 and isinstance(outbox_items, list):
        for item in outbox_items:
            if isinstance(item, dict) and item.get("type") == "file":
                name = item.get("name", "")
                if name.startswith("web_") and name.endswith(".json"):
                    files_to_process.append((item, False))  # False = do not delete outbox runner results

    cursor = conn.cursor()

    for item, is_inbox in files_to_process:
        file_name = item.get("name", "")
        file_path = item.get("path", f"relay_queue/inbox/{file_name}")
        file_sha = item.get("sha", "")

        try:
            raw_content = client.get_file_content(item)
            envelope = rp.deserialize_envelope(raw_content, passphrase=config.shared_key or None)
        except Exception as err:
            logger.warning(f"Error parsing envelope from {file_name}: {err}")
            continue

        msg_id = envelope.get("msg_id", "")
        room = envelope.get("room", "main")
        if room == "workers-room":
            room = "workers"
        sender = envelope.get("sender", "mohammad")
        created_at = envelope.get("created_at") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = envelope.get("body", "")
        attachments = envelope.get("attachments") or []

        # Check deduplication state
        cursor.execute("SELECT 1 FROM relay_sync_state WHERE msg_id = ?", (msg_id,))
        if cursor.fetchone() is not None:
            # Already processed
            if is_inbox and file_sha:
                client.delete_file(file_path, file_sha, f"Clean processed inbox message {msg_id[:12]}")
            continue

        # Secret scrub before inserting locally
        sanitized_body = rp.scrub_secrets(body)
        attachments_json = json.dumps(attachments)
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Ingest into messages table
        try:
            cursor.execute(
                """
                INSERT INTO messages (msg_id, room, sender, body, attachments_json, created_at, synced_to_github)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(msg_id) DO NOTHING
                """,
                (msg_id, room, sender, sanitized_body, attachments_json, created_at),
            )
            cursor.execute(
                """
                INSERT INTO relay_sync_state (msg_id, room, direction, github_path, synced_at)
                VALUES (?, ?, 'inbound', ?, ?)
                ON CONFLICT(msg_id) DO NOTHING
                """,
                (msg_id, room, file_path, now_iso),
            )
            conn.commit()
            total_ingested += 1
            logger.info(f"Ingested inbound message {msg_id[:12]} in room '{room}' from '{sender}'")

        except sqlite3.Error as db_err:
            logger.error(f"Failed to insert inbound message {msg_id} into SQLite: {db_err}")
            continue

        # Optional: broadcast to local cluster-chat API endpoint for SSE
        if config.api_url:
            _notify_cluster_chat_api(config.api_url, room, sender, sanitized_body, attachments, created_at, msg_id)

        # Inbound attachment fetch if referenced
        if attachments:
            _sync_inbound_attachments(client, attachments, config.upload_dir)

        # Handle /web or !web command
        web_cmd = rp.parse_web_command(sanitized_body)
        if web_cmd and sender in ("mohammad", "argon"):
            req_payload = {
                "url": web_cmd["url"],
                "room": room,
                "mode": web_cmd["mode"],
                "max_bytes": web_cmd["max_bytes"],
                "request_id": f"web_{msg_id[:12]}",
            }
            client.dispatch_web_bridge(req_payload)

        # Clean up processed inbox file from GitHub
        if is_inbox and file_sha:
            client.delete_file(file_path, file_sha, f"Clean processed inbox message {msg_id[:12]}")

    return total_ingested


def _notify_cluster_chat_api(
    api_url: str,
    room: str,
    sender: str,
    body: str,
    attachments: list[dict[str, Any]],
    created_at: str,
    msg_id: str,
) -> None:
    """Send HTTP POST to local Cluster Chat API to trigger live SSE distribution."""
    payload = {
        "room": room,
        "sender": sender,
        "text": body,
        "body": body,
        "attachments": attachments,
        "created_at": created_at,
        "msg_id": msg_id,
        "source": "github_relay",
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        api_url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "cluster-chat-vps-relay/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            pass
    except Exception as err:
        logger.debug(f"Notification to local API {api_url} failed: {err}")


def _sync_inbound_attachments(
    client: GitHubQueueClient,
    attachments: list[dict[str, Any]],
    upload_dir: str,
) -> None:
    """Download referenced attachment chunks from relay_queue/attachments/ and store locally."""
    dest_base = Path(upload_dir)
    dest_base.mkdir(parents=True, exist_ok=True)

    for att in attachments:
        if not isinstance(att, dict):
            continue
        att_id = att.get("id") or ""
        name = att.get("name") or "attachment.bin"
        safe_name = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", name)

        if not att_id:
            continue

        # Look for matching attachment payload file
        att_file_path = f"relay_queue/attachments/{att_id}.json"
        try:
            status, item, _ = client.get_contents(att_file_path, use_cache=True)
            if status != 200 or not isinstance(item, dict):
                # Try finding by name in directory
                status_dir, dir_items, _ = client.get_contents("relay_queue/attachments", use_cache=True)
                if status_dir == 200 and isinstance(dir_items, list):
                    for di in dir_items:
                        if isinstance(di, dict) and att_id in di.get("name", ""):
                            item = di
                            break

            if item and isinstance(item, dict):
                raw_json = client.get_file_content(item)
                att_data = json.loads(raw_json)
                b64_str = att_data.get("data")
                if b64_str:
                    file_bytes = base64.b64decode(b64_str)
                    target_file = dest_base / f"{att_id}_{safe_name}"
                    target_file.write_bytes(file_bytes)
                    logger.info(f"Downloaded attachment {name} ({len(file_bytes)} bytes) to {target_file}")
        except Exception as err:
            logger.debug(f"Unable to download attachment {att_id}: {err}")


# ==============================================================================
# Outbound Sync: VPS SQLite -> GitHub Room Snapshots
# ==============================================================================

def sync_outbound_to_github(
    client: GitHubQueueClient,
    conn: sqlite3.Connection,
    config: RelayConfig,
) -> int:
    """Query unsynced local messages, update rolling room snapshots, and mark synced.

    For each room in ('main', 'dm-argon', 'workers'):
      - Identifies local messages not present in `relay_sync_state`.
      - Scrubs secrets and prepares canonical message envelopes.
      - Syncs any associated attachments to `relay_queue/attachments/`.
      - If room has new messages:
        - Retrieves the latest 100 room messages from SQLite.
        - Constructs/encrypts room snapshot and commits to `relay_queue/outbox/snapshot_<room>.json`.
        - Records message IDs in `relay_sync_state` with direction='outbound'.

    Returns:
      Total number of newly synced outbound messages across all rooms.
    """
    total_synced = 0
    cursor = conn.cursor()

    for room in VALID_ROOMS:
        # Find unsynced messages for this room
        cursor.execute(
            """
            SELECT id, msg_id, sender, body, attachments_json, created_at
            FROM messages
            WHERE room = ? AND msg_id NOT IN (SELECT msg_id FROM relay_sync_state)
            ORDER BY id ASC
            """,
            (room,),
        )
        unsynced_rows = cursor.fetchall()
        if not unsynced_rows:
            continue

        # Ensure all unsynced rows have valid deterministic msg_ids
        for row in unsynced_rows:
            row_id, row_msg_id, sender, body, _, created_at = row
            if not row_msg_id:
                new_msg_id = rp.compute_msg_id(room, sender, created_at, body, nonce="")
                cursor.execute("UPDATE messages SET msg_id = ? WHERE id = ?", (new_msg_id, row_id))
        conn.commit()

        # Re-fetch the last 100 messages for this room to construct the rolling snapshot
        cursor.execute(
            """
            SELECT id, msg_id, sender, body, attachments_json, created_at
            FROM messages
            WHERE room = ?
            ORDER BY id DESC LIMIT 100
            """,
            (room,),
        )
        snapshot_rows = cursor.fetchall()
        snapshot_rows.reverse()  # Chronological order

        envelopes: list[dict[str, Any]] = []
        for _, row_msg_id, sender, raw_body, atts_json, created_at in snapshot_rows:
            try:
                atts = json.loads(atts_json) if atts_json else []
            except Exception:
                atts = []

            clean_body = rp.scrub_secrets(raw_body or "")
            envelope = rp.create_envelope(
                room=room,
                sender=sender if sender in rp.VALID_SENDERS else "worker",
                body=clean_body,
                attachments=atts,
                created_at=created_at,
                meta={"source": "vps-bridge"},
            )

            # Encrypt envelope if shared key is configured
            if config.shared_key:
                serialized = rp.serialize_envelope(envelope, passphrase=config.shared_key)
                envelope = json.loads(serialized)

            envelopes.append(envelope)

        # Format snapshot document compatible with both object and array parsers
        snapshot_doc = {
            "ok": True,
            "room": room,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "count": len(envelopes),
            "messages": envelopes,
        }
        snapshot_bytes = json.dumps(snapshot_doc, indent=2, sort_keys=True).encode("utf-8")
        snapshot_path = f"relay_queue/outbox/snapshot_{room}.json"

        # Check existing remote file SHA for snapshot
        status, existing_item, _ = client.get_contents(snapshot_path, use_cache=False)
        existing_sha = existing_item.get("sha") if (status == 200 and isinstance(existing_item, dict)) else None

        commit_msg = f"Update rolling snapshot for room '{room}' ({len(envelopes)} messages)"
        client.put_file(snapshot_path, snapshot_bytes, commit_msg, sha=existing_sha)

        # Mark all unsynced messages as synced in relay_sync_state
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for row in unsynced_rows:
            row_id, row_msg_id, _, _, _, _ = row
            cursor.execute(
                """
                INSERT INTO relay_sync_state (msg_id, room, direction, github_path, synced_at)
                VALUES (?, ?, 'outbound', ?, ?)
                ON CONFLICT(msg_id) DO NOTHING
                """,
                (row_msg_id, room, snapshot_path, now_iso),
            )
            cursor.execute("UPDATE messages SET synced_to_github = 1 WHERE id = ?", (row_id,))
            total_synced += 1

        conn.commit()
        logger.info(f"Published snapshot for room '{room}' with {len(unsynced_rows)} new outbound messages")

    return total_synced


# ==============================================================================
# Diagnostics & Status Summary
# ==============================================================================

def get_status_summary(conn: sqlite3.Connection, config: RelayConfig) -> dict[str, Any]:
    """Compile diagnostic status summary of local SQLite database and sync state."""
    init_db(conn)
    cursor = conn.cursor()

    # Total message counts
    cursor.execute("SELECT COUNT(*) FROM messages")
    total_messages = cursor.fetchone()[0]

    # Messages per room
    messages_by_room: dict[str, int] = {r: 0 for r in VALID_ROOMS}
    cursor.execute("SELECT room, COUNT(*) FROM messages GROUP BY room")
    for room, cnt in cursor.fetchall():
        if room in messages_by_room:
            messages_by_room[room] = cnt

    # Synced counts by direction
    synced_inbound = 0
    synced_outbound = 0
    cursor.execute("SELECT direction, COUNT(*) FROM relay_sync_state GROUP BY direction")
    for direction, cnt in cursor.fetchall():
        if direction == "inbound":
            synced_inbound = cnt
        elif direction == "outbound":
            synced_outbound = cnt

    # Synced counts by room
    synced_by_room: dict[str, int] = {r: 0 for r in VALID_ROOMS}
    cursor.execute("SELECT room, COUNT(*) FROM relay_sync_state GROUP BY room")
    for room, cnt in cursor.fetchall():
        if room in synced_by_room:
            synced_by_room[room] = cnt

    # Unsynced count
    cursor.execute(
        "SELECT COUNT(*) FROM messages WHERE msg_id NOT IN (SELECT msg_id FROM relay_sync_state)"
    )
    unsynced_count = cursor.fetchone()[0]

    # Last sync timestamps
    cursor.execute("SELECT MAX(synced_at) FROM relay_sync_state")
    last_sync = cursor.fetchone()[0]

    cursor.execute("SELECT MAX(synced_at) FROM relay_sync_state WHERE direction = 'inbound'")
    last_inbound = cursor.fetchone()[0]

    cursor.execute("SELECT MAX(synced_at) FROM relay_sync_state WHERE direction = 'outbound'")
    last_outbound = cursor.fetchone()[0]

    # ETag cache count
    cursor.execute("SELECT COUNT(*) FROM relay_etag_cache")
    etag_count = cursor.fetchone()[0]

    return {
        "status": "ok",
        "database": config.db_path,
        "config": config.sanitized_dict(),
        "total_messages": total_messages,
        "messages_by_room": messages_by_room,
        "synced_counts": {
            "inbound": synced_inbound,
            "outbound": synced_outbound,
            "total": synced_inbound + synced_outbound,
        },
        "synced_by_room": synced_by_room,
        "unsynced_count": unsynced_count,
        "last_sync_timestamp": last_sync,
        "last_inbound_sync": last_inbound,
        "last_outbound_sync": last_outbound,
        "etag_cache_entries": etag_count,
    }


# ==============================================================================
# CLI Entry Point & Daemon Execution
# ==============================================================================

def main() -> int:
    """Main CLI entry point for the LeadDesk Cluster Chat GitHub Relay Daemon."""
    parser = argparse.ArgumentParser(
        description="LeadDesk AI Cluster Chat GitHub Relay Daemon (VPS /opt/hq/)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Path to SQLite database (default: $CLUSTER_CHAT_DB or /opt/hq/cluster_chat.db)",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="GitHub repository in owner/repo format (default: $GITHUB_REPO)",
    )
    parser.add_argument(
        "--branch",
        default=None,
        help="GitHub repository branch (default: $GITHUB_BRANCH or main)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="GitHub Personal Access Token (can also be provided via $GITHUB_TOKEN)",
    )
    parser.add_argument(
        "--shared-key",
        default=None,
        help="Passphrase for symmetric AES-256-GCM encryption (default: $RELAY_SHARED_KEY)",
    )
    parser.add_argument(
        "--api-url",
        default=None,
        help="Cluster Chat HTTP API URL for SSE broadcasts (default: $CLUSTER_CHAT_API_URL)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=None,
        help="Polling cadence in seconds (default: $RELAY_POLL_INTERVAL or 8.0)",
    )
    parser.add_argument(
        "--upload-dir",
        default=None,
        help="Directory for local attachment storage (default: $CLUSTER_CHAT_UPLOAD_DIR)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print JSON diagnostic summary of local DB and sync state and exit",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single inbound and outbound synchronization cycle and exit",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run continuous synchronization loop sleeping poll-interval between cycles",
    )

    args = parser.parse_args()

    # Load configuration
    config = RelayConfig(
        token=args.token,
        repo=args.repo,
        branch=args.branch,
        db_path=args.db,
        api_url=args.api_url,
        shared_key=args.shared_key,
        poll_interval=args.poll_interval,
        upload_dir=args.upload_dir,
    )

    # Ensure database directory exists
    db_file = Path(config.db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(config.db_path)
    try:
        init_db(conn)

        # Mode 1: Status diagnostic output
        if args.status or (not args.once and not args.daemon):
            summary = get_status_summary(conn, config)
            print(json.dumps(summary, indent=2))
            return 0

        # Mode 2 & 3 require GitHub token
        if not config.token:
            logger.error("GITHUB_TOKEN is required for sync operations (--once / --daemon).")
            return 1

        client = GitHubQueueClient(
            token=config.token,
            repo=config.repo,
            branch=config.branch,
            conn=conn,
        )

        # Mode 2: Run once
        if args.once:
            logger.info("Executing single synchronization cycle...")
            inbound = sync_inbound_from_github(client, conn, config)
            outbound = sync_outbound_to_github(client, conn, config)
            logger.info(f"Sync complete. Inbound: {inbound}, Outbound: {outbound}")
            return 0

        # Mode 3: Continuous daemon loop
        if args.daemon:
            logger.info(
                f"Starting Cluster Chat GitHub Relay Daemon (Repo: {config.repo}, "
                f"Branch: {config.branch}, Cadence: {config.poll_interval}s, Encryption: {bool(config.shared_key)})"
            )

            running = True

            def _handle_signal(signum: int, frame: Any) -> None:
                nonlocal running
                logger.info(f"Signal {signum} received. Initiating graceful shutdown...")
                running = False

            signal.signal(signal.SIGINT, _handle_signal)
            signal.signal(signal.SIGTERM, _handle_signal)

            while running:
                try:
                    sync_inbound_from_github(client, conn, config)
                    sync_outbound_to_github(client, conn, config)
                except Exception as cycle_err:
                    logger.error(f"Error during synchronization cycle: {cycle_err}")

                # Sleep in short increments for responsive signal handling
                sleep_remaining = config.poll_interval
                while running and sleep_remaining > 0:
                    time.sleep(min(0.5, sleep_remaining))
                    sleep_remaining -= 0.5

            logger.info("Relay daemon stopped cleanly.")
            return 0

    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
