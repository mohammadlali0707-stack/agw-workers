"""tests/test_cluster_chat_uploads.py

Comprehensive test suite for Cluster Chat Bridge File and Photo Upload Support (P3 Subtask 4):
1. Saving photos (png, jpg) and documents (pdf, txt, json) across all standard rooms
   (main, dm-argon, workers-room) and verifying all metadata fields.
2. Path traversal protection (in filenames, safe path resolution, and HTTP file serving)
   and oversize upload rejection.
3. End-to-end HTTP server tests (POST /upload via multipart, JSON base64, JSON local_path;
   GET /files/...; GET /messages).
4. Mocked Telegram inbound photo and document updates (getFile download, local persistence,
   and agent inbox JSONL delivery).
5. Mocked Telegram outbound sendPhoto and automatic fallback to sendDocument for files > 10 MB.
6. CLI and Python SDK invocations (local mode, HTTP mode, multi-file attachments, bin wrapper).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.parse
import urllib.request

from cluster_chat.attachments import (
    DEFAULT_MAX_UPLOAD_MB,
    STANDARD_ROOMS,
    AttachmentMetadata,
    AttachmentStorageManager,
    detect_mime_type,
    sanitize_filename,
    validate_room_name,
)
from cluster_chat.cli import (
    build_parser,
    format_file_size,
    list_room_messages,
    main as cli_main,
    send_room_file,
)
from cluster_chat.rooms import (
    DEFAULT_DATA_DIR,
    RoomManager,
    RoomMessage,
)
from cluster_chat.server import ClusterChatServer
from cluster_chat.telegram_bridge import (
    MAX_TELEGRAM_PHOTO_SIZE,
    TelegramBridge,
    encode_multipart_formdata,
)


def _make_multipart(fields: dict[str, str], files: list[tuple[str, str, bytes, str]]) -> tuple[bytes, str]:
    """Helper to build multipart/form-data payload with boundary."""
    boundary = "----TestUploadsBoundary" + "A" * 16
    body = bytearray()

    for k, v in fields.items():
        body.extend(f"--{boundary}\r\n".encode("latin-1"))
        body.extend(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode("latin-1"))
        body.extend(str(v).encode("utf-8"))
        body.extend(b"\r\n")

    for field_name, filename, data, mime in files:
        body.extend(f"--{boundary}\r\n".encode("latin-1"))
        body.extend(f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode("latin-1"))
        body.extend(f"Content-Type: {mime}\r\n\r\n".encode("latin-1"))
        body.extend(data)
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode("latin-1"))
    content_type = f"multipart/form-data; boundary={boundary}"
    return bytes(body), content_type


class TestClusterChatUploadsSuite(unittest.TestCase):
    """End-to-end and unit test suite covering file upload features across Cluster Chat."""

    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp(prefix="cluster_chat_upload_suite_")
        self.upload_dir = Path(self.test_dir) / "uploads"
        self.data_dir = Path(self.test_dir) / "data"

        self.storage = AttachmentStorageManager(
            upload_dir=self.upload_dir,
            public_base_url="http://cluster.local",
            max_upload_mb=5,
        )
        self.room_mgr = RoomManager(data_dir=self.data_dir)

        # Environment patcher to isolate from host settings
        self.env_patcher = patch.dict(os.environ, {}, clear=True)
        self.env_patcher.start()

    def tearDown(self) -> None:
        self.env_patcher.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # --------------------------------------------------------------------------
    # 1. Attachment Saving Across All Rooms & Metadata Validation
    # --------------------------------------------------------------------------

    def test_save_photos_and_documents_across_all_rooms(self) -> None:
        """Verify saving photos (png, jpg) and documents (pdf, txt, json) across all rooms."""
        test_payloads = [
            ("arch.png", b"\x89PNG\r\n\x1a\nfake_png_data", "photo", "image/png"),
            ("photo.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIFfake_jpg", "photo", "image/jpeg"),
            ("spec.pdf", b"%PDF-1.4\n%fake_pdf_stream\n%%EOF", "document", "application/pdf"),
            ("log.txt", b"2026-10-28 06:00:00 [INFO] System running", "document", "text/plain"),
            ("config.json", b'{"cluster": "lead-desk", "active": true}', "document", "application/json"),
        ]

        for room in ("main", "dm-argon", "workers-room"):
            for filename, content, expected_kind, expected_mime in test_payloads:
                meta = self.storage.save_bytes(
                    content=content,
                    filename=filename,
                    room=room,
                    sender="tester",
                    target_agent="argon",
                )

                # Validate all metadata fields
                self.assertTrue(meta["attachment_id"].startswith("att_"))
                self.assertGreaterEqual(len(meta["attachment_id"]), 20)
                self.assertEqual(meta["kind"], expected_kind)
                self.assertEqual(meta["mime_type"], expected_mime)
                self.assertEqual(meta["filename"], filename)
                self.assertEqual(meta["size_bytes"], len(content))
                self.assertEqual(meta["sha256"], hashlib.sha256(content).hexdigest())
                self.assertEqual(meta["room"], room)
                self.assertEqual(meta["sender"], "tester")
                self.assertEqual(meta["target_agent"], "argon")

                # Verify local file persistence and content integrity
                saved_path = Path(meta["local_path"])
                self.assertTrue(saved_path.is_file())
                self.assertTrue(saved_path.is_relative_to(self.upload_dir))
                self.assertEqual(saved_path.read_bytes(), content)

                # Verify public URL format
                expected_url_prefix = f"http://cluster.local/api/cluster-chat/files/{room}/"
                self.assertTrue(meta["public_url"].startswith(expected_url_prefix))
                self.assertIn(filename, meta["public_url"])

    # --------------------------------------------------------------------------
    # 2. Path Traversal & Oversize File Rejection
    # --------------------------------------------------------------------------

    def test_path_traversal_protection(self) -> None:
        """Verify strict protection against path traversal in filenames and URLs."""
        # A. Path traversal in save_bytes filename: must be sanitized safely
        meta = self.storage.save_bytes(
            content=b"safe content",
            filename="../../../etc/shadow",
            room="main",
            sender="tester",
        )
        saved_file = Path(meta["local_path"])
        self.assertTrue(saved_file.is_relative_to(self.upload_dir))
        self.assertNotIn("..", str(saved_file))
        self.assertIn("shadow", saved_file.name)

        # B. Path traversal in resolve_safe_file_path must raise ValueError
        with self.assertRaises(ValueError):
            self.storage.resolve_safe_file_path("main", "2026-10-28", "../../passwd")

        with self.assertRaises(ValueError):
            self.storage.resolve_safe_file_path("main", "2026-10-28", "something/nested.txt")

        with self.assertRaises(ValueError):
            self.storage.resolve_safe_file_path("main", "../2026-10-28", "valid.txt")

        # C. Invalid room names must be rejected
        with self.assertRaises(ValueError):
            self.storage.save_bytes(b"content", "file.txt", room="../secret", sender="tester")

        with self.assertRaises(ValueError):
            self.storage.save_bytes(b"content", "file.txt", room="room with spaces", sender="tester")

    def test_oversize_file_rejection(self) -> None:
        """Verify files exceeding CLUSTER_CHAT_MAX_UPLOAD_MB are rejected."""
        # Limit is 5 MB in setUp; try saving 6 MB
        oversize_bytes = b"X" * (6 * 1024 * 1024)

        with self.assertRaises(ValueError) as ctx:
            self.storage.save_bytes(
                content=oversize_bytes,
                filename="big.bin",
                room="main",
                sender="tester",
            )
        self.assertIn("exceeds maximum upload limit", str(ctx.exception))

        # Check save_file_from_path oversize rejection
        big_temp = Path(self.test_dir) / "huge.dat"
        big_temp.write_bytes(oversize_bytes)
        with self.assertRaises(ValueError) as ctx:
            self.storage.save_file_from_path(big_temp, room="main", sender="tester")
        self.assertIn("exceeds maximum upload limit", str(ctx.exception))

    # --------------------------------------------------------------------------
    # 3. End-to-End HTTP Server: Uploads, File Serving, and Messages
    # --------------------------------------------------------------------------

    def test_http_server_upload_and_file_serving(self) -> None:
        """End-to-end test of POST /upload, GET /files/..., and GET /messages."""
        server = ClusterChatServer(
            host="127.0.0.1",
            port=0,
            storage=self.storage,
            room_manager=self.room_mgr,
        )
        server.start()
        self.addCleanup(server.stop)

        base_url = f"http://127.0.0.1:{server.port}"

        # 1. Multipart POST /api/cluster-chat/upload with photo and document
        photo_bytes = b"\x89PNG\r\n\x1a\nfake_photo_bytes_png"
        doc_bytes = b"Hello from uploaded document!"

        fields = {
            "room": "dm-argon",
            "sender": "mohammad",
            "text": "Please review this architectural draft",
            "target_agent": "argon",
        }
        files = [
            ("file", "draft.png", photo_bytes, "image/png"),
            ("file", "notes.txt", doc_bytes, "text/plain"),
        ]
        body, ct = _make_multipart(fields, files)

        req = urllib.request.Request(
            f"{base_url}/api/cluster-chat/upload",
            data=body,
            headers={"Content-Type": ct, "Content-Length": str(len(body))},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 201)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            attachments = data["attachments"]
            self.assertEqual(len(attachments), 2)

            photo_meta = next(a for a in attachments if a["filename"] == "draft.png")
            doc_meta = next(a for a in attachments if a["filename"] == "notes.txt")
            self.assertEqual(photo_meta["kind"], "photo")
            self.assertEqual(doc_meta["kind"], "document")

        # 2. GET /api/cluster-chat/files/<room>/<date>/<filename>
        # The public_url was configured with base "http://cluster.local/api/cluster-chat/files/..."
        # Extract relative path to request against local test server
        parsed_url = urllib.parse.urlparse(photo_meta["public_url"])
        photo_path = parsed_url.path

        get_req = urllib.request.Request(f"{base_url}{photo_path}")
        with urllib.request.urlopen(get_req) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Content-Type"), "image/png")
            self.assertEqual(resp.read(), photo_bytes)

        # 3. JSON POST with base64 encoded file
        json_b64_payload = {
            "room": "workers-room",
            "sender": "coca",
            "text": "Worker patch data",
            "filename": "patch.diff",
            "mime_type": "text/x-diff",
            "content_base64": base64.b64encode(b"--- a/file\n+++ b/file\n").decode("ascii"),
        }
        b64_body = json.dumps(json_b64_payload).encode("utf-8")
        req_b64 = urllib.request.Request(
            f"{base_url}/api/cluster-chat/upload",
            data=b64_body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(b64_body))},
            method="POST",
        )
        with urllib.request.urlopen(req_b64) as resp:
            self.assertEqual(resp.status, 201)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["attachments"][0]["filename"], "patch.diff")

        # 4. JSON POST with local_path forwarding
        temp_local = Path(self.test_dir) / "agent_log.txt"
        temp_local.write_text("agent local log data")

        json_local_payload = {
            "room": "workers-room",
            "sender": "argon",
            "text": "Attached local file",
            "local_path": str(temp_local),
        }
        local_body = json.dumps(json_local_payload).encode("utf-8")
        req_local = urllib.request.Request(
            f"{base_url}/api/cluster-chat/upload",
            data=local_body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(local_body))},
            method="POST",
        )
        with urllib.request.urlopen(req_local) as resp:
            self.assertEqual(resp.status, 201)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["attachments"][0]["filename"], "agent_log.txt")

        # 5. GET /api/cluster-chat/rooms/workers-room/messages
        messages_req = urllib.request.Request(f"{base_url}/api/cluster-chat/rooms/workers-room/messages")
        with urllib.request.urlopen(messages_req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertEqual(len(data["messages"]), 2)

        # 6. Verify HTTP 400/403/404 on Path Traversal request
        bad_paths = [
            "/api/cluster-chat/files/main/2026-10-28/../../etc/passwd",
            "/api/cluster-chat/files/main/2026-10-28/%2e%2e%2fpasswd",
        ]
        for bp in bad_paths:
            try:
                urllib.request.urlopen(f"{base_url}{bp}")
                self.fail(f"Expected HTTP error on bad path {bp}")
            except urllib.error.HTTPError as he:
                self.assertIn(he.code, (400, 403, 404))

    # --------------------------------------------------------------------------
    # 4. Telegram Inbound Updates & Agent Inbox Persistence
    # --------------------------------------------------------------------------

    def test_telegram_inbound_photo_and_document_webhook(self) -> None:
        """Verify inbound Telegram update downloads bytes and delivers to agent inbox JSONL."""
        bridge = TelegramBridge(
            bot_token="TEST_BOT_TOKEN_123",
            storage=self.storage,
            room_manager=self.room_mgr,
            data_dir=self.data_dir,
            room_map={"main": {"chat_id": "-1001234567890"}},
        )

        fake_photo_bytes = b"\xff\xd8\xff\xe0JFIF_photo_payload"
        fake_doc_bytes = b"%PDF-1.4 Telegram Document"

        # Mock Telegram getFile and download requests
        def mock_http_request(url, method="GET", body=None, headers=None):
            if "getFile" in url:
                if "photo_file_1" in url:
                    resp = {"ok": True, "result": {"file_id": "photo_file_1", "file_path": "photos/file_0.jpg"}}
                else:
                    resp = {"ok": True, "result": {"file_id": "doc_file_2", "file_path": "documents/report.pdf"}}
                return 200, json.dumps(resp).encode("utf-8")
            elif "/file/bot" in url:
                if "photos" in url:
                    return 200, fake_photo_bytes
                else:
                    return 200, fake_doc_bytes
            return 200, b'{"ok": true}'

        bridge._http_request = mock_http_request

        # 1. Inbound Photo Update targeted to @argon
        photo_update = {
            "update_id": 1001,
            "message": {
                "message_id": 42,
                "chat": {"id": -1001234567890, "type": "supergroup"},
                "from": {"id": 111, "first_name": "Mohammad", "username": "mohammadlali"},
                "caption": "@argon please review this diagram",
                "photo": [
                    {"file_id": "thumb_1", "file_size": 100, "width": 50, "height": 50},
                    {"file_id": "photo_file_1", "file_size": 1024, "width": 800, "height": 600},
                ],
            },
        }

        res = bridge.handle_telegram_update(photo_update)
        self.assertIsNotNone(res)
        saved_msg = res
        self.assertEqual(saved_msg["room"], "main")
        self.assertEqual(saved_msg["target_agent"], "argon")
        self.assertEqual(len(saved_msg["attachments"]), 1)
        att = saved_msg["attachments"][0]
        self.assertEqual(att["kind"], "photo")
        self.assertTrue(Path(att["local_path"]).is_file())
        self.assertEqual(Path(att["local_path"]).read_bytes(), fake_photo_bytes)

        # Verify target agent inbox file written at data/cluster-chat/inbox/argon.jsonl
        inbox_file = self.data_dir / "inbox" / "argon.jsonl"
        self.assertTrue(inbox_file.is_file())
        inbox_lines = [json.loads(line) for line in inbox_file.read_text().strip().splitlines()]
        self.assertEqual(len(inbox_lines), 1)
        self.assertEqual(inbox_lines[0]["message_id"], saved_msg["message_id"])
        self.assertEqual(inbox_lines[0]["attachments"][0]["kind"], "photo")

        # 2. Inbound Document Update targeted to cookie
        doc_update = {
            "update_id": 1002,
            "message": {
                "message_id": 43,
                "chat": {"id": -1001234567890, "type": "supergroup"},
                "from": {"id": 111, "first_name": "Mohammad", "username": "mohammadlali"},
                "caption": "cookie: please index this pdf",
                "document": {
                    "file_id": "doc_file_2",
                    "file_name": "quarterly_report.pdf",
                    "mime_type": "application/pdf",
                    "file_size": len(fake_doc_bytes),
                },
            },
        }

        res_doc = bridge.handle_telegram_update(doc_update)
        self.assertIsNotNone(res_doc)
        saved_doc_msg = res_doc
        self.assertEqual(saved_doc_msg["target_agent"], "cookie")

        # Verify cookie's inbox JSONL record created
        cookie_inbox = self.data_dir / "inbox" / "cookie.jsonl"
        self.assertTrue(cookie_inbox.is_file())
        cookie_records = [json.loads(line) for line in cookie_inbox.read_text().strip().splitlines()]
        self.assertEqual(len(cookie_records), 1)
        self.assertEqual(cookie_records[0]["attachments"][0]["kind"], "document")

    # --------------------------------------------------------------------------
    # 5. Telegram Outbound sendPhoto & 10 MB Fallback to sendDocument
    # --------------------------------------------------------------------------

    def test_telegram_outbound_send_photo_and_fallback(self) -> None:
        """Verify Telegram sendPhoto is called for <= 10MB images, and sendDocument for > 10MB or documents."""
        bridge = TelegramBridge(
            bot_token="MOCK_BOT_TOKEN_XYZ",
            storage=self.storage,
            room_manager=self.room_mgr,
            data_dir=self.data_dir,
            room_map={"dm-argon": {"chat_id": "-1009999"}},
        )

        sent_urls: list[str] = []

        def mock_http(url, method="POST", body=None, headers=None):
            sent_urls.append(url)
            return 200, b'{"ok": true, "result": {"message_id": 999}}'

        bridge._http_request = mock_http

        # A. Normal photo <= 10 MB (e.g. 500 KB)
        normal_photo = self.storage.save_bytes(
            content=b"P" * (500 * 1024),
            filename="diagram.png",
            room="dm-argon",
            sender="argon",
        )
        msg_photo = {"room": "dm-argon", "sender": "argon", "text": "Photo 500KB"}
        res = bridge.send_attachment_to_telegram(msg_photo, normal_photo)
        self.assertTrue(res["ok"])
        self.assertTrue(any("sendPhoto" in u for u in sent_urls))
        self.assertEqual(sent_urls[-1], "https://api.telegram.org/botMOCK_BOT_TOKEN_XYZ/sendPhoto")

        # B. Oversize photo > 10 MB (e.g. 11 MB): must fall back to sendDocument
        sent_urls.clear()
        big_photo_meta = dict(normal_photo)
        big_photo_meta["size_bytes"] = 11 * 1024 * 1024  # 11 MB
        big_photo_meta["kind"] = "photo"

        res_big = bridge.send_attachment_to_telegram(msg_photo, big_photo_meta)
        self.assertTrue(res_big["ok"])
        self.assertTrue(any("sendDocument" in u for u in sent_urls))
        self.assertEqual(sent_urls[-1], "https://api.telegram.org/botMOCK_BOT_TOKEN_XYZ/sendDocument")

        # C. Document: always sends via sendDocument
        sent_urls.clear()
        doc_meta = self.storage.save_bytes(
            content=b"sample doc content",
            filename="report.pdf",
            room="dm-argon",
            sender="argon",
        )
        msg_doc = {"room": "dm-argon", "sender": "argon", "text": "Document"}
        res_doc = bridge.send_attachment_to_telegram(msg_doc, doc_meta)
        self.assertTrue(res_doc["ok"])
        self.assertTrue(any("sendDocument" in u for u in sent_urls))
        self.assertEqual(sent_urls[-1], "https://api.telegram.org/botMOCK_BOT_TOKEN_XYZ/sendDocument")

    # --------------------------------------------------------------------------
    # 6. CLI Invocation & Python SDK Helper
    # --------------------------------------------------------------------------

    def test_sdk_and_cli_invocation_local_mode(self) -> None:
        """Verify Python SDK send_room_file/list_room_messages and CLI in local direct mode."""
        # Create test sample files
        test_file1 = Path(self.test_dir) / "diag.png"
        test_file1.write_bytes(b"\x89PNG\r\n\x1a\ncli_diagram")
        test_file2 = Path(self.test_dir) / "notes.txt"
        test_file2.write_text("meeting notes from CLI test")

        # A. SDK send_room_file local invocation with multiple attachments
        res = send_room_file(
            room="workers-room",
            sender="agy-1",
            files=[test_file1, test_file2],
            text="Initial multi-file dispatch",
            target_agent="mohammad",
            upload_dir=self.upload_dir,
            data_dir=self.data_dir,
            forward_telegram=False,
        )
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["attachments"]), 2)
        self.assertEqual(res["message"]["sender"], "agy-1")
        self.assertEqual(res["message"]["target_agent"], "mohammad")

        # B. SDK list_room_messages local invocation
        messages = list_room_messages(
            room="workers-room",
            limit=10,
            data_dir=self.data_dir,
        )
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["text"], "Initial multi-file dispatch")

        # C. CLI invocation via cli_main (simulating python3 -m cluster_chat.cli send ...)
        cli_test_file = Path(self.test_dir) / "cli_artifact.json"
        cli_test_file.write_text('{"build": "success"}')

        cli_args = [
            "send",
            "--room", "workers-room",
            "--sender", "argon",
            "--target-agent", "cookie",
            "--text", "Build artifact from CLI command",
            "--file", str(cli_test_file),
            "--upload-dir", str(self.upload_dir),
            "--data-dir", str(self.data_dir),
            "--no-telegram",
            "--json",
        ]
        ret_code = cli_main(cli_args)
        self.assertEqual(ret_code, 0)

        # D. CLI list command
        list_args = [
            "list",
            "--room", "workers-room",
            "--limit", "5",
            "--data-dir", str(self.data_dir),
            "--json",
        ]
        ret_code = cli_main(list_args)
        self.assertEqual(ret_code, 0)

        # Verify messages now contain 2 entries
        all_msgs = self.room_mgr.get_messages("workers-room")
        self.assertEqual(len(all_msgs), 2)
        self.assertEqual(all_msgs[1]["sender"], "argon")
        self.assertEqual(all_msgs[1]["attachments"][0]["filename"], "cli_artifact.json")

    def test_cli_validation_and_size_formatting(self) -> None:
        """Verify CLI rejects empty send calls, handles None sizes safely, and prints version."""
        # 1. Validation error when neither text nor file is provided
        ret = cli_main(["send", "--room", "main"])
        self.assertEqual(ret, 1)

        ret_json = cli_main(["send", "--room", "main", "--json"])
        self.assertEqual(ret_json, 1)

        # 2. format_file_size handles None, int, float, and strings safely
        self.assertEqual(format_file_size(None), "0 B")
        self.assertEqual(format_file_size(0), "0 B")
        self.assertEqual(format_file_size(512), "512 B")
        self.assertEqual(format_file_size(2048), "2.0 KB")
        self.assertEqual(format_file_size(5 * 1024 * 1024), "5.0 MB")
        self.assertEqual(format_file_size("invalid"), "0 B")

        # 3. Parser version flag
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["--version"])
        self.assertEqual(ctx.exception.code, 0)

    def test_sdk_and_cli_invocation_http_mode(self) -> None:
        """Verify Python SDK and CLI using --api-url against running ClusterChatServer."""
        server = ClusterChatServer(
            host="127.0.0.1",
            port=0,
            storage=self.storage,
            room_manager=self.room_mgr,
        )
        server.start()
        self.addCleanup(server.stop)

        api_url = f"http://127.0.0.1:{server.port}"

        test_file = Path(self.test_dir) / "remote_doc.md"
        test_file.write_text("# Remote Upload Documentation")

        # A. SDK send_room_file over HTTP
        res = send_room_file(
            room="dm-argon",
            sender="coca",
            file_path=test_file,
            text="Remote HTTP upload through SDK",
            target_agent="argon",
            api_url=api_url,
            forward_telegram=False,
        )
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["attachments"]), 1)
        self.assertEqual(res["attachments"][0]["filename"], "remote_doc.md")

        # B. SDK list_room_messages over HTTP
        msgs = list_room_messages(
            room="dm-argon",
            limit=5,
            api_url=api_url,
        )
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["sender"], "coca")

        # C. CLI send command with --api-url
        cli_file = Path(self.test_dir) / "screenshot.png"
        cli_file.write_bytes(b"\x89PNG\r\n\x1a\nremote_png")

        cli_args = [
            "send",
            "--room", "dm-argon",
            "--sender", "mohammad",
            "--text", "Uploaded via CLI --api-url",
            "--file", str(cli_file),
            "--api-url", api_url,
            "--no-telegram",
        ]
        ret = cli_main(cli_args)
        self.assertEqual(ret, 0)

        # D. CLI list command with --api-url
        cli_list_args = [
            "list",
            "--room", "dm-argon",
            "--limit", "10",
            "--api-url", api_url,
        ]
        ret_list = cli_main(cli_list_args)
        self.assertEqual(ret_list, 0)

    def test_bin_cluster_chat_upload_executable(self) -> None:
        """Verify bin/cluster-chat-upload binary script execution via subprocess."""
        bin_script = Path(__file__).resolve().parent.parent / "bin" / "cluster-chat-upload"
        self.assertTrue(bin_script.exists())
        self.assertTrue(os.access(bin_script, os.X_OK))

        dummy_file = Path(self.test_dir) / "bin_test.txt"
        dummy_file.write_text("Testing bin/cluster-chat-upload")

        # Run bin/cluster-chat-upload send ...
        cmd = [
            str(bin_script),
            "send",
            "--room", "main",
            "--sender", "agy-worker",
            "--target-agent", "argon",
            "--text", "Testing bin script directly",
            "--file", str(dummy_file),
            "--upload-dir", str(self.upload_dir),
            "--data-dir", str(self.data_dir),
            "--no-telegram",
            "--json",
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(proc.returncode, 0, f"Error running bin script: {proc.stderr}")

        output_json = json.loads(proc.stdout)
        self.assertTrue(output_json["ok"])
        self.assertEqual(output_json["message"]["sender"], "agy-worker")
        self.assertEqual(output_json["attachments"][0]["filename"], "bin_test.txt")


if __name__ == "__main__":
    unittest.main()
