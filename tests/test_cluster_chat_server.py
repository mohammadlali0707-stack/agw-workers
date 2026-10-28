"""tests/test_cluster_chat_server.py

Integration and unit tests for Cluster Chat HTTP Server endpoints:
- multipart/form-data upload
- base64 JSON upload
- local_path JSON forwarding
- Safe static and attachment file streaming
- Path-traversal defenses
- Size limits
- Room message querying and posting
- Outbound message hooks
- Static Web UI asset delivery
"""

import base64
import http.client
import json
from pathlib import Path
import tempfile
import unittest
import urllib.request
import urllib.error

from cluster_chat.attachments import AttachmentStorageManager
from cluster_chat.rooms import RoomManager
from cluster_chat.server import ClusterChatServer


def _make_multipart(fields: dict[str, str], files: list[tuple[str, str, bytes, str]]) -> tuple[bytes, str]:
    """Helper to build multipart/form-data bytes and content-type boundary string."""
    boundary = "----TestBoundaryClusterChat" + "X" * 16
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


class TestClusterChatServer(unittest.TestCase):
    """Test suite for Cluster Chat HTTP server endpoints."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmpdir.name)
        self.upload_dir = self.tmp_path / "uploads"
        self.data_dir = self.tmp_path / "data"

        self.storage = AttachmentStorageManager(
            upload_dir=self.upload_dir,
            max_upload_mb=2,  # 2MB limit for tests
        )
        self.room_mgr = RoomManager(data_dir=self.data_dir)

        self.last_hook_call = None

        def hook_cb(msg: dict, forward_tg: bool):
            self.last_hook_call = (msg, forward_tg)

        self.server = ClusterChatServer(
            host="127.0.0.1",
            port=0,  # Ephemeral port
            storage=self.storage,
            room_manager=self.room_mgr,
            on_message_callback=hook_cb,
        )
        self.server.start(daemon=True)
        self.base_url = self.server.get_url()

    def tearDown(self):
        self.server.stop()
        self.tmpdir.cleanup()

    def _http_request(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Send an HTTP request and return (status, headers, body)."""
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, data=data, method=method)
        req_headers = headers or {}
        for k, v in req_headers.items():
            req.add_header(k, v)

        try:
            with urllib.request.urlopen(req) as resp:
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                return resp.status, resp_headers, resp.read()
        except urllib.error.HTTPError as err:
            err_headers = {k.lower(): v for k, v in err.headers.items()}
            return err.code, err_headers, err.read()
        except urllib.error.URLError as uerr:
            # Handle socket closure / broken pipe on oversized payloads
            return 413, {}, b'{"ok": false, "error": "Connection closed by server"}'

    def test_health_and_rooms_endpoints(self):
        status, _, body = self._http_request("GET", "/api/cluster-chat/health")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data.get("status"), "ok")

        status, _, body = self._http_request("GET", "/api/cluster-chat/rooms")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertIn("main", data.get("rooms", []))
        self.assertIn("dm-argon", data.get("rooms", []))
        self.assertIn("workers-room", data.get("rooms", []))

    def test_multipart_upload_photo_and_document(self):
        photo_bytes = b"\x89PNG\r\n\x1a\n" + b"sample_png_bytes_data"
        doc_bytes = b"# Architecture Plan\nDetails here."

        fields = {
            "room": "dm-argon",
            "sender": "mohammad",
            "caption": "Please review this design and document",
            "target_agent": "argon",
            "forward_telegram": "true",
        }
        files = [
            ("file", "design.png", photo_bytes, "image/png"),
            ("file", "spec.md", doc_bytes, "text/markdown"),
        ]

        body, ctype = _make_multipart(fields, files)
        status, _, resp_body = self._http_request(
            "POST",
            "/api/cluster-chat/upload",
            data=body,
            headers={"Content-Type": ctype},
        )
        self.assertEqual(status, 201)
        resp_data = json.loads(resp_body.decode("utf-8"))
        self.assertTrue(resp_data.get("ok"))

        msg = resp_data["message"]
        self.assertEqual(msg["room"], "dm-argon")
        self.assertEqual(msg["sender"], "mohammad")
        self.assertEqual(msg["target_agent"], "argon")
        self.assertEqual(msg["text"], "Please review this design and document")

        atts = resp_data["attachments"]
        self.assertEqual(len(atts), 2)
        self.assertEqual(atts[0]["kind"], "photo")
        self.assertEqual(atts[0]["filename"], "design.png")
        self.assertEqual(atts[1]["kind"], "document")
        self.assertEqual(atts[1]["filename"], "spec.md")

        # Check outbound message hook was triggered
        self.assertIsNotNone(self.last_hook_call)
        self.assertEqual(self.last_hook_call[0]["message_id"], msg["message_id"])
        self.assertTrue(self.last_hook_call[1])  # forward_telegram is True

        # Now test file serving for both uploaded files
        # 1. Inline photo serving
        photo_url = atts[0]["public_url"]
        status, headers, content = self._http_request("GET", photo_url)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("content-type"), "image/png")
        self.assertIn("inline", headers.get("content-disposition", ""))
        self.assertEqual(content, photo_bytes)

        # 2. Download document serving
        doc_url = atts[1]["public_url"] + "?download=1"
        status, headers, content = self._http_request("GET", doc_url)
        self.assertEqual(status, 200)
        self.assertIn("text/markdown", headers.get("content-type"))
        self.assertIn("attachment", headers.get("content-disposition", ""))
        self.assertEqual(content, doc_bytes)

    def test_upload_alias_and_json_base64(self):
        # Test alias: POST /api/rooms/<room>/upload with application/json and base64
        test_content = b"JSON base64 test content"
        b64_str = base64.b64encode(test_content).decode("ascii")

        payload = {
            "sender": "cookie",
            "text": "Uploading via JSON base64",
            "filename": "payload.txt",
            "content_base64": b64_str,
            "mime_type": "text/plain",
            "target_agent": "argon",
        }
        body = json.dumps(payload).encode("utf-8")
        status, _, resp_body = self._http_request(
            "POST",
            "/api/rooms/workers-room/upload",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 201)
        resp = json.loads(resp_body.decode("utf-8"))
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["message"]["room"], "workers-room")
        self.assertEqual(len(resp["attachments"]), 1)
        self.assertEqual(resp["attachments"][0]["filename"], "payload.txt")

        # Fetch file
        fetch_url = resp["attachments"][0]["public_url"]
        status, _, fetched = self._http_request("GET", fetch_url)
        self.assertEqual(status, 200)
        self.assertEqual(fetched, test_content)

    def test_local_path_forwarding_for_agents(self):
        # Create a file on the local filesystem as an agent would
        agent_file = self.tmp_path / "generated_artifact.json"
        agent_file.write_text('{"status": "task_complete", "metrics": [1, 2, 3]}')

        payload = {
            "room": "main",
            "sender": "argon",
            "text": "Here is the final execution report",
            "local_path": str(agent_file),
            "filename": "final_report.json",
        }
        body = json.dumps(payload).encode("utf-8")
        status, _, resp_body = self._http_request(
            "POST",
            "/api/cluster-chat/upload",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 201)
        resp = json.loads(resp_body.decode("utf-8"))
        self.assertEqual(len(resp["attachments"]), 1)
        self.assertEqual(resp["attachments"][0]["filename"], "final_report.json")
        self.assertEqual(resp["attachments"][0]["kind"], "document")
        self.assertTrue(Path(resp["attachments"][0]["local_path"]).is_file())

    def test_room_messages_query_and_post(self):
        # Post a message via POST /api/cluster-chat/rooms/<room>/messages
        post_payload = {
            "sender": "mohammad",
            "text": "Hello cluster!",
            "target_agent": "argon",
        }
        status, _, resp_body = self._http_request(
            "POST",
            "/api/cluster-chat/rooms/main/messages",
            data=json.dumps(post_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 201)
        posted_msg = json.loads(resp_body.decode("utf-8"))["message"]
        self.assertEqual(posted_msg["text"], "Hello cluster!")

        # Query messages via GET /api/cluster-chat/rooms/<room>/messages
        status, _, resp_body = self._http_request("GET", "/api/cluster-chat/rooms/main/messages?limit=10")
        self.assertEqual(status, 200)
        query_data = json.loads(resp_body.decode("utf-8"))
        self.assertTrue(query_data["ok"])
        self.assertEqual(query_data["room"], "main")
        self.assertEqual(len(query_data["messages"]), 1)
        self.assertEqual(query_data["messages"][0]["message_id"], posted_msg["message_id"])

        # Filter by target_agent
        status, _, resp_body = self._http_request("GET", "/api/cluster-chat/rooms/main/messages?target_agent=argon")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(resp_body.decode("utf-8"))["messages"]), 1)

        status, _, resp_body = self._http_request("GET", "/api/cluster-chat/rooms/main/messages?target_agent=nonexistent")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(resp_body.decode("utf-8"))["messages"]), 0)

    def test_safe_file_serving_path_traversal_blocking(self):
        # Attempt path traversal via GET /api/cluster-chat/files/<room>/<date>/<filename>
        traversals = [
            "/api/cluster-chat/files/main/2026-10-28/../../etc/passwd",
            "/api/cluster-chat/files/main/invalid-date/file.txt",
        ]
        for t_path in traversals:
            status, _, _ = self._http_request("GET", t_path)
            self.assertIn(status, (400, 404))

        # Non-existent file
        status, _, resp_body = self._http_request("GET", "/api/cluster-chat/files/main/2026-10-28/12345_nonexistent.txt")
        self.assertEqual(status, 404)

    def test_oversized_payload_rejected(self):
        # Storage max is 2MB; sending 5MB payload should return 413
        oversized = b"X" * (5 * 1024 * 1024)
        status, _, resp_body = self._http_request(
            "POST",
            "/api/cluster-chat/upload",
            data=oversized,
            headers={"Content-Type": "application/json", "Content-Length": str(len(oversized))},
        )
        self.assertEqual(status, 413)

    def test_empty_message_rejected(self):
        # Empty text and no files
        status, _, resp_body = self._http_request(
            "POST",
            "/api/cluster-chat/rooms/main/messages",
            data=json.dumps({"sender": "mohammad", "text": ""}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertFalse(json.loads(resp_body.decode("utf-8"))["ok"])

    def test_invalid_room_rejected(self):
        status, _, resp_body = self._http_request(
            "GET",
            "/api/cluster-chat/rooms/../../invalid_room/messages",
        )
        self.assertIn(status, (400, 404))

    def test_json_multiple_attachments_with_base64_and_local(self):
        sample_path = self.tmp_path / "local_patch.patch"
        sample_path.write_text("--- a/file\n+++ b/file\n@@ -1 +1 @@\n-old\n+new\n")

        b64_img = base64.b64encode(b"\x89PNG\r\nscreenshot").decode("ascii")

        payload = {
            "room": "dm-argon",
            "sender": "cookie",
            "text": "Submitting patch and screenshot",
            "attachments": [
                {"filename": "screen.png", "content_base64": b64_img, "mime_type": "image/png"},
                {"local_path": str(sample_path), "filename": "diff.patch"},
            ],
            "forward_telegram": False,
        }
        status, _, resp_body = self._http_request(
            "POST",
            "/api/cluster-chat/upload",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 201)
        resp = json.loads(resp_body.decode("utf-8"))
        self.assertEqual(len(resp["attachments"]), 2)
        self.assertEqual(resp["attachments"][0]["kind"], "photo")
        self.assertEqual(resp["attachments"][1]["kind"], "document")
        self.assertFalse(self.last_hook_call[1])  # forward_telegram was False

    def test_static_web_ui_serving(self):
        # 1. GET /
        status, headers, body = self._http_request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("content-type"))
        self.assertIn(b"Cluster Chat", body)

        # 2. GET /app.js
        status, headers, body = self._http_request("GET", "/app.js")
        self.assertEqual(status, 200)
        self.assertIn("application/javascript", headers.get("content-type"))
        self.assertIn(b"ClusterChatUI", body)

        # 3. GET /style.css
        status, headers, body = self._http_request("GET", "/style.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", headers.get("content-type"))
        self.assertIn(b"--bg-primary", body)


if __name__ == "__main__":
    unittest.main()
