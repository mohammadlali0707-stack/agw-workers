"""tests/test_telegram_bridge.py

Unit and integration tests for bidirectional Telegram photo and document bridge,
covering room routing, 10 MB fallback, agent inbox delivery, getFile download,
and server webhook integration.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import urllib.parse
import urllib.request

from cluster_chat.attachments import (
    AttachmentStorageManager,
    detect_mime_type,
)
from cluster_chat.rooms import RoomManager
from cluster_chat.server import ClusterChatServer
from cluster_chat.telegram_bridge import (
    MAX_TELEGRAM_PHOTO_SIZE,
    TelegramBridge,
    encode_multipart_formdata,
    extract_target_agent,
    handle_telegram_update,
    send_attachment_to_telegram,
)


class TestTelegramBridge(unittest.TestCase):
    """Test suite for TelegramBridge operations."""

    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp(prefix="cluster_chat_tg_test_")
        self.upload_dir = Path(self.test_dir) / "uploads"
        self.data_dir = Path(self.test_dir) / "data"

        self.storage = AttachmentStorageManager(
            upload_dir=self.upload_dir,
            public_base_url="http://test.cluster",
        )
        self.room_mgr = RoomManager(data_dir=self.data_dir)

        # Clear any environment variables that might interfere
        self.env_patcher = patch.dict(os.environ, {}, clear=True)
        self.env_patcher.start()

    def tearDown(self) -> None:
        self.env_patcher.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_missing_token_or_chat_id_graceful_skip(self) -> None:
        """Verify send_attachment_to_telegram degrades gracefully when token or chat_id is missing."""
        # 1. No token configured
        bridge_no_token = TelegramBridge(
            bot_token=None,
            storage=self.storage,
            room_manager=self.room_mgr,
            data_dir=self.data_dir,
        )
        dummy_msg = {"room": "main", "sender": "mohammad", "text": "hello"}
        dummy_att = {"kind": "photo", "filename": "test.png", "size_bytes": 100}

        res = bridge_no_token.send_attachment_to_telegram(dummy_msg, dummy_att)
        self.assertFalse(res["ok"])
        self.assertEqual(res["skipped"], "no_token_or_chat_id")

        # 2. Token configured, but room has no mapped chat_id
        bridge_with_token = TelegramBridge(
            bot_token="test_token_123",
            room_map={},
            storage=self.storage,
            room_manager=self.room_mgr,
            data_dir=self.data_dir,
        )
        res2 = bridge_with_token.send_attachment_to_telegram(
            {"room": "unknown-room", "sender": "mohammad"},
            dummy_att,
        )
        self.assertFalse(res2["ok"])
        self.assertEqual(res2["skipped"], "no_token_or_chat_id")

    def test_room_mapping_configuration(self) -> None:
        """Verify room-to-chat mapping via TELEGRAM_ROOM_MAP_JSON and per-room env vars."""
        # Test TELEGRAM_ROOM_MAP_JSON
        json_map = {
            "main": {"chat_id": "-1001234567890", "message_thread_id": 10},
            "dm-argon": {"chat_id": "987654321"},
            "workers-room": {"chat_id": "-1009999999999", "message_thread_id": 20},
        }
        with patch.dict(os.environ, {"TELEGRAM_ROOM_MAP_JSON": json.dumps(json_map)}):
            bridge = TelegramBridge(bot_token="mock_token")
            # Forward mapping
            self.assertEqual(bridge.resolve_room_target("main"), ("-1001234567890", 10))
            self.assertEqual(bridge.resolve_room_target("dm-argon"), ("987654321", None))
            self.assertEqual(bridge.resolve_room_target("workers-room"), ("-1009999999999", 20))

            # Reverse mapping
            self.assertEqual(bridge.map_chat_to_room("-1001234567890", thread_id=10), "main")
            self.assertEqual(bridge.map_chat_to_room("987654321"), "dm-argon")
            self.assertEqual(bridge.map_chat_to_room("-1009999999999", thread_id=20), "workers-room")
            # Unmapped defaults to main
            self.assertEqual(bridge.map_chat_to_room("99999999"), "main")

        # Test per-room environment variables
        env_vars = {
            "TELEGRAM_CHAT_ID_MAIN": "-100111",
            "TELEGRAM_THREAD_ID_MAIN": "1",
            "TELEGRAM_CHAT_ID_DM_ARGON": "222",
            "TELEGRAM_CHAT_ID_WORKERS_ROOM": "-100333",
            "TELEGRAM_THREAD_ID_WORKERS_ROOM": "3",
        }
        with patch.dict(os.environ, env_vars):
            bridge_env = TelegramBridge(bot_token="mock_token")
            self.assertEqual(bridge_env.resolve_room_target("main"), ("-100111", 1))
            self.assertEqual(bridge_env.resolve_room_target("dm-argon"), ("222", None))
            self.assertEqual(bridge_env.resolve_room_target("workers-room"), ("-100333", 3))

            self.assertEqual(bridge_env.map_chat_to_room("-100111", thread_id=1), "main")
            self.assertEqual(bridge_env.map_chat_to_room("222"), "dm-argon")
            self.assertEqual(bridge_env.map_chat_to_room("-100333", thread_id=3), "workers-room")

    def test_thread_bound_room_isolation_from_general_topic(self) -> None:
        """Verify messages from general topic (thread_id=None) do not misroute to thread-bound rooms sharing the same chat_id."""
        shared_chat_id = "-100777888"
        mapping = {
            "main": {"chat_id": shared_chat_id},  # General chat topic
            "workers-room": {"chat_id": shared_chat_id, "message_thread_id": 55},  # Dedicated topic thread
        }
        bridge = TelegramBridge(bot_token="test_token", room_map=mapping)

        # Threaded message (thread_id=55) MUST map to workers-room
        self.assertEqual(bridge.map_chat_to_room(shared_chat_id, thread_id=55), "workers-room")

        # General message (thread_id=None) MUST map to main, NOT workers-room
        self.assertEqual(bridge.map_chat_to_room(shared_chat_id, thread_id=None), "main")

    def test_extract_target_agent(self) -> None:
        """Verify target agent resolution from text mentions and room semantics."""
        # Mention in text
        self.assertEqual(extract_target_agent("main", "Please check this @argon"), "argon")
        self.assertEqual(extract_target_agent("main", "Hey @cookie can you run tests?"), "cookie")
        self.assertEqual(extract_target_agent("main", "cc @coca @workers"), "coca")
        self.assertEqual(extract_target_agent("main", "argon: check screenshot"), "argon")

        # Room semantics
        self.assertEqual(extract_target_agent("dm-argon", "no mention here"), "argon")
        self.assertEqual(extract_target_agent("workers-room", "task update"), "workers")
        self.assertEqual(extract_target_agent("dm-cookie", "direct msg"), "cookie")

        # Fallback
        self.assertIsNone(extract_target_agent("main", "general announcement to everyone"))

    def test_outbound_send_photo_under_10mb(self) -> None:
        """Verify sendPhoto is invoked for photo attachments under 10 MB."""
        img_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 2048
        att_dict = self.storage.save_bytes(
            content=img_bytes,
            filename="screenshot.png",
            room="dm-argon",
            sender="mohammad",
        )

        bridge = TelegramBridge(
            bot_token="bot12345",
            room_map={"dm-argon": {"chat_id": "987654321"}},
            storage=self.storage,
            room_manager=self.room_mgr,
            data_dir=self.data_dir,
        )

        msg = {
            "room": "dm-argon",
            "sender": "mohammad",
            "text": "Review architecture diagram",
            "attachments": [att_dict],
        }

        # Mock Telegram Bot API sendPhoto response
        mock_tg_resp = {
            "ok": True,
            "result": {
                "message_id": 701,
                "chat": {"id": 987654321},
                "photo": [
                    {"file_id": "thumb_id", "file_unique_id": "u1", "width": 90, "height": 90},
                    {"file_id": "highest_res_photo_id", "file_unique_id": "u2", "width": 800, "height": 600},
                ],
            },
        }

        with patch.object(
            bridge,
            "_http_request",
            return_value=(200, json.dumps(mock_tg_resp).encode("utf-8")),
        ) as mock_req:
            res = bridge.send_attachment_to_telegram(msg, att_dict)

            self.assertTrue(res["ok"])
            self.assertEqual(res["method"], "sendPhoto")
            self.assertEqual(res["telegram_file_id"], "highest_res_photo_id")
            # Verify attachment metadata dictionary was updated with returned file_id
            self.assertEqual(att_dict["telegram_file_id"], "highest_res_photo_id")

            # Check outbound request URL & headers
            mock_req.assert_called_once()
            called_url = mock_req.call_args[1]["url"]
            self.assertTrue(called_url.endswith("/botbot12345/sendPhoto"))
            body = mock_req.call_args[1]["body"]
            self.assertIn(b"987654321", body)
            self.assertIn(b"[dm-argon] mohammad: Review architecture diagram", body)
            self.assertIn(b'name="photo"', body)

    def test_outbound_send_photo_over_10mb_fallback(self) -> None:
        """Verify sendDocument fallback when photo exceeds 10 MB limit."""
        # 11 MB simulated photo
        eleven_mb = (11 * 1024 * 1024)
        large_att_dict = {
            "attachment_id": "att_large_photo",
            "kind": "photo",
            "filename": "huge_diagram.png",
            "mime_type": "image/png",
            "size_bytes": eleven_mb,
            "sha256": "fakehash",
            "room": "main",
            "sender": "mohammad",
            "local_path": "",
            "public_url": "/api/cluster-chat/files/main/2026-10-28/huge.png",
            "telegram_file_id": "existing_large_tg_id",
            "created_at": "2026-10-28T06:00:00Z",
        }

        bridge = TelegramBridge(
            bot_token="bot12345",
            room_map={"main": {"chat_id": "-100123"}},
            storage=self.storage,
            room_manager=self.room_mgr,
            data_dir=self.data_dir,
        )

        msg = {"room": "main", "sender": "mohammad", "text": "Large photo"}

        mock_doc_resp = {
            "ok": True,
            "result": {
                "message_id": 702,
                "document": {"file_id": "returned_doc_file_id"},
            },
        }

        with patch.object(
            bridge,
            "_http_request",
            return_value=(200, json.dumps(mock_doc_resp).encode("utf-8")),
        ) as mock_req:
            res = bridge.send_attachment_to_telegram(msg, large_att_dict)

            self.assertTrue(res["ok"])
            # MUST use sendDocument due to 10 MB limit!
            self.assertEqual(res["method"], "sendDocument")
            self.assertEqual(res["telegram_file_id"], "returned_doc_file_id")

            called_url = mock_req.call_args[1]["url"]
            self.assertTrue(called_url.endswith("/botbot12345/sendDocument"))

    def test_outbound_send_document(self) -> None:
        """Verify sendDocument is invoked for non-photo documents."""
        doc_content = b"diff --git a/test b/test\n+new line\n"
        att_dict = self.storage.save_bytes(
            content=doc_content,
            filename="patch.diff",
            room="workers-room",
            sender="argon",
        )
        self.assertEqual(att_dict["kind"], "document")

        bridge = TelegramBridge(
            bot_token="bot12345",
            room_map={"workers-room": {"chat_id": "-100999"}},
            storage=self.storage,
            room_manager=self.room_mgr,
            data_dir=self.data_dir,
        )

        msg = {
            "room": "workers-room",
            "sender": "argon",
            "text": "Attached patch for ACC4",
            "attachments": [att_dict],
        }

        mock_resp = {
            "ok": True,
            "result": {
                "message_id": 703,
                "document": {"file_id": "patch_tg_id_888"},
            },
        }

        with patch.object(
            bridge,
            "_http_request",
            return_value=(200, json.dumps(mock_resp).encode("utf-8")),
        ) as mock_req:
            res = bridge.send_attachment_to_telegram(msg, att_dict)
            self.assertTrue(res["ok"])
            self.assertEqual(res["method"], "sendDocument")
            self.assertEqual(res["telegram_file_id"], "patch_tg_id_888")
            self.assertEqual(att_dict["telegram_file_id"], "patch_tg_id_888")

            called_url = mock_req.call_args[1]["url"]
            self.assertTrue(called_url.endswith("/botbot12345/sendDocument"))
            body = mock_req.call_args[1]["body"]
            self.assertIn(b'name="document"', body)

    def test_inbound_photo_update_and_download(self) -> None:
        """Verify inbound Telegram photo update downloads bytes via getFile and saves locally."""
        bridge = TelegramBridge(
            bot_token="bot_secret_token",
            room_map={"dm-argon": {"chat_id": "12345678"}},
            storage=self.storage,
            room_manager=self.room_mgr,
            data_dir=self.data_dir,
        )

        fake_photo_bytes = b"\xff\xd8\xff\xe0" + b"JPEG_TEST_CONTENT" * 100

        # Simulate getFile and file download responses
        def fake_http(url: str, method: str = "GET", **kwargs):
            if "getFile" in url:
                resp_json = {
                    "ok": True,
                    "result": {
                        "file_id": "photo_file_high_res",
                        "file_unique_id": "uniq_1",
                        "file_size": len(fake_photo_bytes),
                        "file_path": "photos/file_99.jpg",
                    },
                }
                return 200, json.dumps(resp_json).encode("utf-8")
            if "file/botbot_secret_token/photos/file_99.jpg" in url:
                return 200, fake_photo_bytes
            return 404, b"Not Found"

        tg_update = {
            "update_id": 9001,
            "message": {
                "message_id": 501,
                "chat": {"id": 12345678, "type": "private"},
                "from": {"id": 888, "first_name": "Mohammad", "username": "mohammad"},
                "date": 1761622000,
                "caption": "Architecture snapshot",
                "photo": [
                    {"file_id": "photo_low", "width": 90, "height": 90},
                    {"file_id": "photo_file_high_res", "width": 1280, "height": 720},
                ],
            },
        }

        with patch.object(bridge, "_http_request", side_effect=fake_http):
            posted = bridge.handle_telegram_update(tg_update)

            self.assertIsNotNone(posted)
            self.assertEqual(posted["room"], "dm-argon")
            self.assertEqual(posted["sender"], "mohammad")
            self.assertEqual(posted["text"], "Architecture snapshot")
            self.assertEqual(posted["target_agent"], "argon")

            attachments = posted["attachments"]
            self.assertEqual(len(attachments), 1)
            att = attachments[0]
            self.assertEqual(att["kind"], "photo")
            self.assertEqual(att["telegram_file_id"], "photo_file_high_res")

            # Verify local disk persistence
            local_path = Path(att["local_path"])
            self.assertTrue(local_path.exists())
            self.assertEqual(local_path.read_bytes(), fake_photo_bytes)

            # Verify room manager persistence
            room_messages = self.room_mgr.get_messages("dm-argon")
            self.assertEqual(len(room_messages), 1)
            self.assertEqual(room_messages[0]["message_id"], posted["message_id"])

    def test_inbound_document_update_and_download(self) -> None:
        """Verify inbound Telegram document update downloads bytes and persists attachment."""
        bridge = TelegramBridge(
            bot_token="bot_secret_token",
            room_map={"main": {"chat_id": "-100555"}},
            storage=self.storage,
            room_manager=self.room_mgr,
            data_dir=self.data_dir,
        )

        pdf_bytes = b"%PDF-1.4\n%test pdf data\n%%EOF"

        def fake_http(url: str, method: str = "GET", **kwargs):
            if "getFile" in url:
                resp = {
                    "ok": True,
                    "result": {
                        "file_id": "doc_file_id_444",
                        "file_path": "documents/report.pdf",
                    },
                }
                return 200, json.dumps(resp).encode("utf-8")
            if "file/botbot_secret_token/documents/report.pdf" in url:
                return 200, pdf_bytes
            return 404, b"Not Found"

        tg_update = {
            "update_id": 9002,
            "message": {
                "message_id": 502,
                "chat": {"id": -100555, "type": "supergroup"},
                "from": {"id": 888, "first_name": "Mohammad", "username": "mohammad"},
                "date": 1761622010,
                "caption": "Project requirements @argon",
                "document": {
                    "file_id": "doc_file_id_444",
                    "file_name": "project_spec.pdf",
                    "mime_type": "application/pdf",
                    "file_size": len(pdf_bytes),
                },
            },
        }

        with patch.object(bridge, "_http_request", side_effect=fake_http):
            posted = bridge.handle_telegram_update(tg_update)

            self.assertIsNotNone(posted)
            self.assertEqual(posted["room"], "main")
            self.assertEqual(posted["target_agent"], "argon")
            self.assertEqual(len(posted["attachments"]), 1)
            att = posted["attachments"][0]
            self.assertEqual(att["kind"], "document")
            self.assertEqual(att["filename"], "project_spec.pdf")
            self.assertEqual(att["telegram_file_id"], "doc_file_id_444")

            local_path = Path(att["local_path"])
            self.assertTrue(local_path.exists())
            self.assertEqual(local_path.read_bytes(), pdf_bytes)

    def test_inbound_target_agent_inbox_jsonl_delivery(self) -> None:
        """Verify target agent inbox file (data/cluster-chat/inbox/<target_agent>.jsonl) receives payload."""
        webhook_mock = MagicMock()
        bridge = TelegramBridge(
            bot_token="bot_secret_token",
            room_map={"workers-room": {"chat_id": "-100777"}},
            storage=self.storage,
            room_manager=self.room_mgr,
            data_dir=self.data_dir,
            agent_webhook_url="http://agent.internal/webhook",
        )

        test_log_bytes = b"[2026-10-28] Build succeeded\n"

        def fake_http(url: str, method: str = "GET", **kwargs):
            if "getFile" in url:
                return 200, json.dumps({"ok": True, "result": {"file_id": "log_id", "file_path": "docs/build.log"}}).encode("utf-8")
            if "file/botbot_secret_token/docs/build.log" in url:
                return 200, test_log_bytes
            if "http://agent.internal/webhook" in url:
                webhook_mock(url=url, **kwargs)
                return 200, b'{"status": "delivered"}'
            return 404, b"Not Found"

        tg_update = {
            "update_id": 9003,
            "message": {
                "message_id": 503,
                "chat": {"id": -100777, "type": "supergroup"},
                "from": {"id": 888, "first_name": "Mohammad", "username": "mohammad"},
                "date": 1761622020,
                "caption": "Build logs for workers",
                "document": {
                    "file_id": "log_id",
                    "file_name": "build.log",
                    "mime_type": "text/plain",
                },
            },
        }

        with patch.object(bridge, "_http_request", side_effect=fake_http):
            posted = bridge.handle_telegram_update(tg_update)
            self.assertIsNotNone(posted)
            self.assertEqual(posted["target_agent"], "workers")

            # Check local agent inbox JSONL file: data/cluster-chat/inbox/workers.jsonl
            inbox_file = self.data_dir / "inbox" / "workers.jsonl"
            self.assertTrue(inbox_file.exists())

            lines = inbox_file.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            inbox_record = json.loads(lines[0])

            self.assertEqual(inbox_record["target_agent"], "workers")
            self.assertEqual(inbox_record["room"], "workers-room")
            self.assertTrue(inbox_record["local_path"].endswith("build.log"))
            self.assertTrue(inbox_record["public_url"].startswith("http://test.cluster/api/cluster-chat/files/"))
            self.assertEqual(len(inbox_record["attachments"]), 1)

            # Check that optional webhook was dispatched
            webhook_mock.assert_called_once()
            call_body = json.loads(webhook_mock.call_args[1]["body"].decode("utf-8"))
            self.assertEqual(call_body["target_agent"], "workers")
            self.assertEqual(call_body["message_id"], posted["message_id"])

    def test_server_webhook_endpoint_integration(self) -> None:
        """Integration test verifying ClusterChatServer POST /api/cluster-chat/telegram/webhook."""
        bridge = TelegramBridge(
            bot_token="test_token",
            room_map={"main": {"chat_id": "-100123"}},
            storage=self.storage,
            room_manager=self.room_mgr,
            data_dir=self.data_dir,
        )

        server = ClusterChatServer(
            host="127.0.0.1",
            port=0,
            storage=self.storage,
            room_manager=self.room_mgr,
            telegram_bridge=bridge,
        )

        with server:
            base_url = server.get_url()

            # 1. Test invalid non-JSON payload rejected
            req_invalid = urllib.request.Request(
                f"{base_url}/api/cluster-chat/telegram/webhook",
                data=b"not json",
                headers={"Content-Type": "text/plain"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req_invalid)
            self.assertEqual(ctx.exception.code, 415)

            # 2. Test valid Telegram text message through webhook
            tg_text_update = {
                "update_id": 9010,
                "message": {
                    "message_id": 601,
                    "chat": {"id": -100123},
                    "from": {"id": 888, "first_name": "Mohammad", "username": "mohammad"},
                    "date": 1761622050,
                    "text": "Hello from Telegram webhook!",
                },
            }
            req_valid = urllib.request.Request(
                f"{base_url}/api/cluster-chat/telegram/webhook",
                data=json.dumps(tg_text_update).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req_valid) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data["ok"])
                self.assertEqual(data["result"]["text"], "Hello from Telegram webhook!")
                self.assertEqual(data["result"]["room"], "main")

            # 3. Test alias endpoint /api/telegram/webhook
            tg_alias_update = {
                "update_id": 9011,
                "message": {
                    "message_id": 602,
                    "chat": {"id": -100123},
                    "from": {"id": 888, "first_name": "Mohammad", "username": "mohammad"},
                    "date": 1761622055,
                    "text": "Hello via alias webhook!",
                },
            }
            req_alias = urllib.request.Request(
                f"{base_url}/api/telegram/webhook",
                data=json.dumps(tg_alias_update).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req_alias) as resp_alias:
                self.assertEqual(resp_alias.status, 200)
                data_alias = json.loads(resp_alias.read().decode("utf-8"))
                self.assertTrue(data_alias["ok"])

            # 4. Verify message query endpoint shows the webhook messages
            req_get = urllib.request.Request(f"{base_url}/api/cluster-chat/rooms/main/messages")
            with urllib.request.urlopen(req_get) as resp_get:
                self.assertEqual(resp_get.status, 200)
                room_data = json.loads(resp_get.read().decode("utf-8"))
                messages = room_data["messages"]
                self.assertTrue(any(m["text"] == "Hello from Telegram webhook!" for m in messages))
                self.assertTrue(any(m["text"] == "Hello via alias webhook!" for m in messages))


if __name__ == "__main__":
    unittest.main()
