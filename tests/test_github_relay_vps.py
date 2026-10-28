"""tests/test_github_relay_vps.py

Comprehensive unit and integration tests for hq/github_relay.py.
Covers SQLite schema dynamic adaptation, bidirectional sync (inbound & outbound),
secret scrubbing, ETag 304 caching, HTTP 409 Conflict auto-retry, and status diagnostics.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.request

import cluster_chat.relay_protocol as rp
from hq.github_relay import (
    DEFAULT_BRANCH,
    DEFAULT_REPO,
    GitHubQueueClient,
    RelayConfig,
    get_status_summary,
    init_db,
    sync_inbound_from_github,
    sync_outbound_to_github,
)


class MockGitHubQueueClient:
    """In-memory mock of GitHubQueueClient for testing sync_inbound and sync_outbound."""

    def __init__(self, branch: str = "main"):
        self.branch = branch
        self.files: dict[str, dict[str, Any]] = {}
        # path -> {"content": str_or_bytes, "sha": str}
        self.dispatched_workflows: list[dict[str, Any]] = []
        self.deleted_files: list[tuple[str, str]] = []
        self.put_calls: list[dict[str, Any]] = []

    def add_file(self, path: str, content: str | bytes, sha: str = "sha_mock_1"):
        clean_path = path.lstrip("/")
        self.files[clean_path] = {"content": content, "sha": sha}

    def get_contents(self, path: str, use_cache: bool = True) -> tuple[int, Any, dict[str, str]]:
        clean_path = path.lstrip("/")
        # Check if direct file exists
        if clean_path in self.files:
            item = self.files[clean_path]
            content = item["content"]
            b64_str = (
                base64.b64encode(content if isinstance(content, bytes) else content.encode("utf-8")).decode("ascii")
            )
            data = {
                "type": "file",
                "name": Path(clean_path).name,
                "path": clean_path,
                "sha": item["sha"],
                "encoding": "base64",
                "content": b64_str,
            }
            return 200, data, {"ETag": f'"{item["sha"]}"'}

        # Check directory listing
        dir_prefix = clean_path if clean_path.endswith("/") else clean_path + "/"
        matching_items = []
        for fpath, item in self.files.items():
            if fpath.startswith(dir_prefix):
                rel_name = fpath[len(dir_prefix):]
                if "/" not in rel_name:
                    b64_str = base64.b64encode(
                        item["content"] if isinstance(item["content"], bytes) else item["content"].encode("utf-8")
                    ).decode("ascii")
                    matching_items.append({
                        "type": "file",
                        "name": rel_name,
                        "path": fpath,
                        "sha": item["sha"],
                        "encoding": "base64",
                        "content": b64_str,
                    })

        if matching_items:
            return 200, matching_items, {"ETag": '"dir_etag"'}

        return 404, None, {}

    def get_file_content(self, item_dict: dict[str, Any]) -> str:
        clean_path = item_dict.get("path", "").lstrip("/")
        if clean_path in self.files:
            c = self.files[clean_path]["content"]
            return c.decode("utf-8") if isinstance(c, bytes) else c
        if item_dict.get("content"):
            raw_b64 = item_dict["content"].replace("\n", "").replace("\r", "")
            return base64.b64decode(raw_b64).decode("utf-8")
        raise ValueError(f"File not found: {clean_path}")

    def put_file(
        self,
        path: str,
        content_bytes: bytes,
        commit_message: str,
        sha: str | None = None,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        clean_path = path.lstrip("/")
        new_sha = f"sha_{len(self.put_calls) + 1}"
        self.files[clean_path] = {"content": content_bytes, "sha": new_sha}
        call_info = {
            "path": clean_path,
            "content_bytes": content_bytes,
            "commit_message": commit_message,
            "sha": sha,
        }
        self.put_calls.append(call_info)
        return {"content": {"sha": new_sha, "path": clean_path}}

    def delete_file(self, path: str, sha: str, commit_message: str) -> bool:
        clean_path = path.lstrip("/")
        self.deleted_files.append((clean_path, sha))
        if clean_path in self.files:
            del self.files[clean_path]
        return True

    def dispatch_web_bridge(self, request_payload: dict[str, Any]) -> bool:
        self.dispatched_workflows.append(request_payload)
        return True


class TestRelayConfig(unittest.TestCase):
    """Test RelayConfig defaults and environment variable overrides."""

    def test_default_config(self):
        with patch.dict("os.environ", {}, clear=True):
            cfg = RelayConfig()
            self.assertEqual(cfg.repo, DEFAULT_REPO)
            self.assertEqual(cfg.branch, DEFAULT_BRANCH)
            self.assertEqual(cfg.poll_interval, 8.0)
            self.assertEqual(cfg.token, "")
            self.assertEqual(cfg.shared_key, "")

    def test_env_override(self):
        env_vars = {
            "GITHUB_TOKEN": "test_token_123",
            "GITHUB_REPO": "org/custom-repo",
            "GITHUB_BRANCH": "relay-state",
            "CLUSTER_CHAT_DB": "/custom/path/chat.db",
            "RELAY_SHARED_KEY": "pass123",
            "RELAY_POLL_INTERVAL": "12.5",
        }
        with patch.dict("os.environ", env_vars, clear=True):
            cfg = RelayConfig()
            self.assertEqual(cfg.token, "test_token_123")
            self.assertEqual(cfg.repo, "org/custom-repo")
            self.assertEqual(cfg.branch, "relay-state")
            self.assertEqual(cfg.db_path, "/custom/path/chat.db")
            self.assertEqual(cfg.shared_key, "pass123")
            self.assertEqual(cfg.poll_interval, 12.5)

            sanitized = cfg.sanitized_dict()
            self.assertTrue(sanitized["encryption_enabled"])
            self.assertTrue(sanitized["has_token"])
            self.assertNotIn("test_token_123", str(sanitized))


class TestInitDb(unittest.TestCase):
    """Test SQLite initialization and schema adaptation for existing databases."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_fresh_database_creation(self):
        init_db(self.conn)
        cursor = self.conn.cursor()

        # Verify tables created
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        self.assertIn("messages", tables)
        self.assertIn("relay_sync_state", tables)
        self.assertIn("relay_etag_cache", tables)

        # Verify columns of messages
        cursor.execute("PRAGMA table_info(messages)")
        cols = {row[1] for row in cursor.fetchall()}
        expected_cols = {"id", "msg_id", "room", "sender", "body", "attachments_json", "created_at", "synced_to_github"}
        self.assertTrue(expected_cols.issubset(cols))

    def test_legacy_schema_adaptation(self):
        # Create legacy messages table with 'text' column, missing 'msg_id', 'body', 'synced_to_github'
        cursor = self.conn.cursor()
        cursor.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room TEXT,
                sender TEXT,
                text TEXT,
                created_at TEXT
            )
            """
        )
        cursor.execute(
            "INSERT INTO messages (room, sender, text, created_at) VALUES (?, ?, ?, ?)",
            ("main", "mohammad", "Existing legacy message", "2026-10-28T07:00:00Z"),
        )
        self.conn.commit()

        # Run init_db to migrate
        init_db(self.conn)

        # Check that columns were added
        cursor.execute("PRAGMA table_info(messages)")
        cols = {row[1] for row in cursor.fetchall()}
        self.assertIn("body", cols)
        self.assertIn("msg_id", cols)
        self.assertIn("synced_to_github", cols)

        # Check that text was copied to body
        cursor.execute("SELECT body, text FROM messages WHERE id = 1")
        row = cursor.fetchone()
        self.assertEqual(row[0], "Existing legacy message")
        self.assertEqual(row[1], "Existing legacy message")


class TestInboundSync(unittest.TestCase):
    """Test inbound sync: downloading inbox messages, decrypting, deduplicating, and web dispatch."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        init_db(self.conn)
        self.client = MockGitHubQueueClient()
        self.shared_key = "Shared-Secret-Passphrase-2026"
        self.config = RelayConfig(shared_key=self.shared_key)

    def tearDown(self):
        self.conn.close()

    def test_inbound_plaintext_message(self):
        env = rp.create_envelope(
            room="main",
            sender="mohammad",
            body="Hello from Static Web UI!",
            created_at="2026-10-28T08:00:00Z",
            nonce="in1",
        )
        self.client.add_file("relay_queue/inbox/msg_1_in1.json", rp.serialize_envelope(env), sha="sha_in1")

        ingested = sync_inbound_from_github(self.client, self.conn, self.config)
        self.assertEqual(ingested, 1)

        # Verify inserted into messages
        cursor = self.conn.cursor()
        cursor.execute("SELECT msg_id, room, sender, body FROM messages WHERE msg_id = ?", (env["msg_id"],))
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[1], "main")
        self.assertEqual(row[2], "mohammad")
        self.assertEqual(row[3], "Hello from Static Web UI!")

        # Verify recorded in relay_sync_state
        cursor.execute("SELECT direction FROM relay_sync_state WHERE msg_id = ?", (env["msg_id"],))
        sync_row = cursor.fetchone()
        self.assertIsNotNone(sync_row)
        self.assertEqual(sync_row[0], "inbound")

        # Verify inbox file deleted from GitHub
        self.assertTrue(any(f[0] == "relay_queue/inbox/msg_1_in1.json" for f in self.client.deleted_files))

    def test_inbound_encrypted_message(self):
        env = rp.create_envelope(
            room="dm-argon",
            sender="mohammad",
            body="Confidential instructions for Argon",
            created_at="2026-10-28T08:05:00Z",
            nonce="in2",
        )
        encrypted_json = rp.serialize_envelope(env, passphrase=self.shared_key)
        self.client.add_file("relay_queue/inbox/msg_2_in2.json", encrypted_json, sha="sha_in2")

        ingested = sync_inbound_from_github(self.client, self.conn, self.config)
        self.assertEqual(ingested, 1)

        # Verify decrypted in local SQLite
        cursor = self.conn.cursor()
        cursor.execute("SELECT body FROM messages WHERE msg_id = ?", (env["msg_id"],))
        row = cursor.fetchone()
        self.assertEqual(row[0], "Confidential instructions for Argon")

    def test_inbound_web_command_triggers_dispatch(self):
        env = rp.create_envelope(
            room="workers",
            sender="mohammad",
            body="/web --mode=markdown https://example.com/api/status",
            created_at="2026-10-28T08:10:00Z",
            nonce="in3",
        )
        self.client.add_file("relay_queue/inbox/msg_3_in3.json", rp.serialize_envelope(env), sha="sha_in3")

        ingested = sync_inbound_from_github(self.client, self.conn, self.config)
        self.assertEqual(ingested, 1)

        # Verify dispatch_web_bridge was called
        self.assertEqual(len(self.client.dispatched_workflows), 1)
        wf = self.client.dispatched_workflows[0]
        self.assertEqual(wf["url"], "https://example.com/api/status")
        self.assertEqual(wf["room"], "workers")
        self.assertEqual(wf["mode"], "markdown")

    def test_inbound_deduplication(self):
        env = rp.create_envelope(
            room="main",
            sender="mohammad",
            body="Unique test message",
            created_at="2026-10-28T08:15:00Z",
            nonce="in4",
        )
        self.client.add_file("relay_queue/inbox/msg_4_in4.json", rp.serialize_envelope(env), sha="sha_in4")

        # First run: ingests 1
        ingested1 = sync_inbound_from_github(self.client, self.conn, self.config)
        self.assertEqual(ingested1, 1)

        # Put file back (simulating duplicate webhook / polling)
        self.client.add_file("relay_queue/inbox/msg_4_in4.json", rp.serialize_envelope(env), sha="sha_in4")

        # Second run: does not ingest again (0 new messages)
        ingested2 = sync_inbound_from_github(self.client, self.conn, self.config)
        self.assertEqual(ingested2, 0)

        # Messages table still has exactly 1 row
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM messages WHERE msg_id = ?", (env["msg_id"],))
        self.assertEqual(cursor.fetchone()[0], 1)


class TestOutboundSync(unittest.TestCase):
    """Test outbound sync: querying unsynced rows, scrubbing secrets, and updating snapshots."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        init_db(self.conn)
        self.client = MockGitHubQueueClient()
        self.shared_key = "Shared-Secret-2026"
        self.config = RelayConfig(shared_key=self.shared_key)

    def tearDown(self):
        self.conn.close()

    def test_outbound_scrubs_secrets_and_writes_snapshot(self):
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO messages (msg_id, room, sender, body, attachments_json, created_at, synced_to_github)
            VALUES (?, ?, ?, ?, ?, ?, 0)
            """,
            (
                "msg_out_1",
                "main",
                "argon",
                "Deployment complete with token ghp_1234567890abcdef1234567890123456 and secret sk-1234567890abcdef12345678",
                "[]",
                "2026-10-28T08:20:00Z",
            ),
        )
        self.conn.commit()

        synced = sync_outbound_to_github(self.client, self.conn, self.config)
        self.assertEqual(synced, 1)

        # Check snapshot file was committed
        snapshot_path = "relay_queue/outbox/snapshot_main.json"
        self.assertIn(snapshot_path, self.client.files)
        raw_snapshot = self.client.files[snapshot_path]["content"]
        snapshot_doc = json.loads(raw_snapshot.decode("utf-8") if isinstance(raw_snapshot, bytes) else raw_snapshot)

        self.assertTrue(snapshot_doc["ok"])
        self.assertEqual(snapshot_doc["room"], "main")
        self.assertEqual(len(snapshot_doc["messages"]), 1)

        # Envelope in snapshot should be encrypted since shared_key is set
        env = snapshot_doc["messages"][0]
        self.assertTrue(env["encrypted"])

        # Decrypt to check secret scrubbing
        decrypted_body = rp.decrypt_payload(env, self.shared_key)
        self.assertNotIn("ghp_", decrypted_body)
        self.assertNotIn("sk-", decrypted_body)
        self.assertIn("[REDACTED_SECRET]", decrypted_body)

        # Verify messages table marked synced
        cursor.execute("SELECT synced_to_github FROM messages WHERE msg_id = 'msg_out_1'")
        self.assertEqual(cursor.fetchone()[0], 1)

        # Verify relay_sync_state has outbound entry
        cursor.execute("SELECT direction FROM relay_sync_state WHERE msg_id = 'msg_out_1'")
        self.assertEqual(cursor.fetchone()[0], "outbound")

    def test_outbound_all_three_rooms(self):
        cursor = self.conn.cursor()
        for idx, room in enumerate(("main", "dm-argon", "workers")):
            cursor.execute(
                """
                INSERT INTO messages (msg_id, room, sender, body, created_at, synced_to_github)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (f"msg_room_{idx}", room, "worker", f"Message in {room}", "2026-10-28T08:25:00Z"),
            )
        self.conn.commit()

        synced = sync_outbound_to_github(self.client, self.conn, self.config)
        self.assertEqual(synced, 3)

        for room in ("main", "dm-argon", "workers"):
            self.assertIn(f"relay_queue/outbox/snapshot_{room}.json", self.client.files)


class TestETagAndConflictHandling(unittest.TestCase):
    """Test conditional ETag 304 handling and HTTP 409 Conflict automatic SHA refresh retry."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        init_db(self.conn)
        self.client = GitHubQueueClient(token="fake_pat", repo="owner/repo", branch="main", conn=self.conn)

    def tearDown(self):
        self.conn.close()

    def test_etag_304_caching(self):
        endpoint = "https://api.github.com/repos/owner/repo/contents/relay_queue/inbox?ref=main"
        cached_body = json.dumps([{"name": "msg_cached.json", "type": "file"}])

        # Pre-populate relay_etag_cache
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO relay_etag_cache (endpoint, etag, cached_json, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (endpoint, '"etag123"', cached_body, "2026-10-28T08:00:00Z"),
        )
        self.conn.commit()

        # Mock urllib.request.urlopen raising HTTPError 304
        http_err = urllib.error.HTTPError(
            url=endpoint,
            code=304,
            msg="Not Modified",
            hdrs={"ETag": '"etag123"'},
            fp=None,
        )

        with patch("urllib.request.urlopen", side_effect=http_err):
            status, parsed, hdrs = self.client.get_contents("relay_queue/inbox", use_cache=True)
            self.assertEqual(status, 304)
            self.assertIsNotNone(parsed)
            self.assertEqual(len(parsed), 1)
            self.assertEqual(parsed[0]["name"], "msg_cached.json")

    def test_put_file_409_conflict_retry(self):
        # First call raises 409 Conflict; second call succeeds
        err_409 = urllib.error.HTTPError(
            url="https://api.github.com/repos/owner/repo/contents/file.txt",
            code=409,
            msg="Conflict",
            hdrs={},
            fp=None,
        )

        # Mock response for get_contents after 409 (to refresh SHA)
        mock_get_resp = MagicMock()
        mock_get_resp.status = 200
        mock_get_resp.headers = {"ETag": '"sha2"'}
        mock_get_resp.read.return_value = json.dumps({"sha": "refreshed_sha_123"}).encode("utf-8")
        mock_get_resp.__enter__.return_value = mock_get_resp

        # Mock successful PUT response
        mock_put_success = MagicMock()
        mock_put_success.status = 200
        mock_put_success.headers = {}
        mock_put_success.read.return_value = json.dumps({"content": {"sha": "final_sha_456"}}).encode("utf-8")
        mock_put_success.__enter__.return_value = mock_put_success

        with patch("time.sleep", return_value=None):
            with patch("urllib.request.urlopen", side_effect=[err_409, mock_get_resp, mock_put_success]):
                result = self.client.put_file("file.txt", b"hello world", "test commit", sha="old_sha")
                self.assertEqual(result["content"]["sha"], "final_sha_456")


class TestStatusSummary(unittest.TestCase):
    """Test get_status_summary diagnostics."""

    def test_status_counts(self):
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO messages (msg_id, room, sender, body, created_at, synced_to_github) VALUES ('m1', 'main', 'mohammad', 'hi', '', 1)"
        )
        cursor.execute(
            "INSERT INTO messages (msg_id, room, sender, body, created_at, synced_to_github) VALUES ('m2', 'dm-argon', 'argon', 'ok', '', 0)"
        )
        cursor.execute(
            "INSERT INTO relay_sync_state (msg_id, room, direction, synced_at) VALUES ('m1', 'main', 'inbound', '')"
        )
        conn.commit()

        cfg = RelayConfig()
        summary = get_status_summary(conn, cfg)

        self.assertEqual(summary["total_messages"], 2)
        self.assertEqual(summary["messages_by_room"]["main"], 1)
        self.assertEqual(summary["messages_by_room"]["dm-argon"], 1)
        self.assertEqual(summary["synced_counts"]["inbound"], 1)
        self.assertEqual(summary["unsynced_count"], 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
