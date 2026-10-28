"""tests/test_cluster_chat_host_agnostic.py

Unit and integration tests for Host-Agnostic Direct-IP Access, Relative URLs,
and Resilient Incremental HTTP Polling in Cluster Chat.

Validates:
- Direct IP:Port Host headers (e.g. Host: 203.0.113.10:8080)
- Trusting reverse-proxy headers (X-Forwarded-Proto, X-Forwarded-Host, X-Forwarded-For)
- Host-agnostic CORS matching direct IP origins and echoing credentials
- Auth/session cookies omitting Domain attributes and conditionally setting Secure only on HTTPS
- Incremental HTTP polling (?since=<id_or_ts>, ?after=<id>, ?limit=<n>)
- Cache-Control: no-store, no-cache, must-revalidate on polling and health endpoints
- Health / transport probe endpoints (/api/health, /api/ping)
- Relative attachment URLs (/api/cluster-chat/files/..., /api/attachments/<id>)
- Codebase audit: zero active airboxvip.top references in cluster_chat
"""

import http.client
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request

from cluster_chat.attachments import AttachmentStorageManager
from cluster_chat.client import list_room_messages, send_room_file
from cluster_chat.rooms import RoomManager
from cluster_chat.server import ClusterChatServer


class TestClusterChatHostAgnostic(unittest.TestCase):
    """Integration test suite for direct-IP, host-agnostic, and polling features."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmpdir.name)
        self.upload_dir = self.tmp_path / "uploads"
        self.data_dir = self.tmp_path / "data"

        self.storage = AttachmentStorageManager(
            upload_dir=self.upload_dir,
            max_upload_mb=5,
        )
        self.room_mgr = RoomManager(data_dir=self.data_dir)

        self.server = ClusterChatServer(
            host="127.0.0.1",
            port=0,
            storage=self.storage,
            room_manager=self.room_mgr,
        )
        self.server.start(daemon=True)
        self.port = self.server.port
        self.base_url = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.stop()
        self.tmpdir.cleanup()

    def _raw_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Perform low-level HTTP request via http.client.HTTPConnection."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        req_headers = headers.copy() if headers else {}
        if body and "Content-Length" not in req_headers:
            req_headers["Content-Length"] = str(len(body))

        conn.request(method, path, body=body, headers=req_headers)
        resp = conn.getresponse()
        resp_headers = {k.lower(): v for k, v in resp.getheaders()}
        resp_body = resp.read()
        conn.close()
        return resp.status, resp_headers, resp_body

    def test_codebase_zero_airboxvip_in_cluster_chat(self):
        """Ensure zero hardcoded airboxvip strings exist in cluster_chat package."""
        pkg_dir = Path(__file__).resolve().parent.parent / "cluster_chat"
        self.assertTrue(pkg_dir.is_dir(), f"cluster_chat directory not found at {pkg_dir}")

        offenders = []
        for p in pkg_dir.rglob("*.py"):
            content = p.read_text(encoding="utf-8")
            for line_no, line in enumerate(content.splitlines(), start=1):
                if "airboxvip" in line.lower():
                    offenders.append(f"{p.name}:{line_no} -> {line.strip()}")

        self.assertEqual(
            offenders,
            [],
            f"Found hardcoded airboxvip references in cluster_chat: {offenders}",
        )

    def test_direct_ip_host_header_health_probe(self):
        """Validate health check responds with direct IP host header and transport probe."""
        direct_ip_host = "203.0.113.10:8080"
        status, headers, body = self._raw_request(
            "GET",
            "/api/health",
            headers={"Host": direct_ip_host},
        )
        self.assertEqual(status, 200)
        self.assertIn("no-store", headers.get("cache-control", ""))

        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("host"), direct_ip_host)
        self.assertIn("ws", data.get("transport", []))
        self.assertIn("sse", data.get("transport", []))
        self.assertIn("poll", data.get("transport", []))
        self.assertTrue(data.get("direct_ip_supported"))

    def test_reverse_proxy_headers_trust(self):
        """Validate reverse proxy headers (X-Forwarded-Host, X-Forwarded-Proto, X-Forwarded-For)."""
        relay_host = "iran-relay.local:8443"
        status, headers, body = self._raw_request(
            "GET",
            "/api/ping",
            headers={
                "Host": "127.0.0.1",
                "X-Forwarded-Host": relay_host,
                "X-Forwarded-Proto": "https",
                "X-Forwarded-For": "198.51.100.42, 10.0.0.1",
            },
        )
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("host"), relay_host)

    def test_head_method_health_probe(self):
        """Validate HEAD requests to health probe endpoints."""
        status, headers, body = self._raw_request(
            "HEAD",
            "/api/health",
            headers={"Host": "192.168.1.100:8080"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(body), 0)
        self.assertIn("no-store", headers.get("cache-control", ""))

    def test_direct_ip_cors_and_credentials(self):
        """Validate CORS echoing direct IP Origin with credentials enabled."""
        direct_origin = "http://203.0.113.10:8080"
        status, headers, body = self._raw_request(
            "OPTIONS",
            "/api/messages",
            headers={
                "Origin": direct_origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type, Authorization",
            },
        )
        self.assertEqual(status, 204)
        self.assertEqual(headers.get("access-control-allow-origin"), direct_origin)
        self.assertEqual(headers.get("access-control-allow-credentials"), "true")
        self.assertIn("origin", headers.get("vary", "").lower())

    def test_auth_session_cookies_over_plain_http_direct_ip(self):
        """Validate session cookies on plain http direct IP omit Domain and Secure."""
        direct_ip_host = "203.0.113.10:8080"
        login_payload = json.dumps({"user": "mohammad"}).encode("utf-8")

        status, headers, body = self._raw_request(
            "POST",
            "/api/auth/session",
            headers={
                "Host": direct_ip_host,
                "Content-Type": "application/json",
            },
            body=login_payload,
        )
        self.assertEqual(status, 200)
        resp_data = json.loads(body.decode("utf-8"))
        self.assertTrue(resp_data.get("ok"))
        self.assertTrue(resp_data.get("authenticated"))
        self.assertEqual(resp_data.get("user"), "mohammad")

        set_cookie = headers.get("set-cookie", "")
        self.assertTrue(set_cookie, "Set-Cookie header must be present")
        self.assertIn("cluster_chat_session=", set_cookie)
        self.assertIn("Path=/", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        # MUST omit fixed Domain attribute so it binds to direct IP:port
        self.assertNotIn("Domain=", set_cookie)
        # MUST NOT set Secure flag over plain HTTP
        self.assertNotIn("Secure", set_cookie)

        # Now test session verification with the cookie
        sess_token = resp_data.get("session_id")
        status, headers, body = self._raw_request(
            "GET",
            "/api/auth/session",
            headers={
                "Host": direct_ip_host,
                "Cookie": f"cluster_chat_session={sess_token}",
            },
        )
        self.assertEqual(status, 200)
        verify_data = json.loads(body.decode("utf-8"))
        self.assertTrue(verify_data.get("ok"))
        self.assertTrue(verify_data.get("authenticated"))
        self.assertEqual(verify_data.get("user"), "mohammad")

    def test_auth_session_cookies_over_https_proxy(self):
        """Validate session cookies set Secure when request is HTTPS through proxy."""
        relay_host = "iran-relay.local:8443"
        login_payload = json.dumps({"user": "argon"}).encode("utf-8")

        status, headers, body = self._raw_request(
            "POST",
            "/api/auth/session",
            headers={
                "Host": relay_host,
                "X-Forwarded-Proto": "https",
                "Content-Type": "application/json",
            },
            body=login_payload,
        )
        self.assertEqual(status, 200)
        set_cookie = headers.get("set-cookie", "")
        self.assertIn("Secure", set_cookie)
        self.assertNotIn("Domain=", set_cookie)

    def test_direct_messages_post_and_incremental_polling(self):
        """Validate POST /api/messages and incremental polling with ?since and ?after."""
        # 1. Post 3 messages via POST /api/messages
        posted_ids = []
        for i in range(1, 4):
            payload = json.dumps({
                "room": "main",
                "sender": "mohammad",
                "text": f"Direct-IP message #{i}",
            }).encode("utf-8")
            status, _, body = self._raw_request(
                "POST",
                "/api/messages",
                headers={"Content-Type": "application/json"},
                body=payload,
            )
            self.assertEqual(status, 201)
            resp = json.loads(body.decode("utf-8"))
            self.assertTrue(resp.get("ok"))
            posted_ids.append(resp["message"]["message_id"])

        self.assertEqual(len(posted_ids), 3)

        # 2. Poll messages without cursor -> should return all 3
        status, headers, body = self._raw_request("GET", "/api/messages?room=main")
        self.assertEqual(status, 200)
        self.assertIn("no-store", headers.get("cache-control", ""))
        self.assertEqual(headers.get("pragma"), "no-cache")
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(len(data.get("messages", [])), 3)

        # 3. Incremental polling with ?since=<id>
        # Polling since the first message ID should return only messages #2 and #3
        status, headers, body = self._raw_request(
            "GET",
            f"/api/messages?room=main&since={posted_ids[0]}",
        )
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        new_msgs = data.get("messages", [])
        self.assertEqual(len(new_msgs), 2)
        self.assertEqual([m["message_id"] for m in new_msgs], posted_ids[1:])

        # 4. Incremental polling with ?after=<id>
        status, headers, body = self._raw_request(
            "GET",
            f"/api/cluster-chat/rooms/main/messages?after={posted_ids[1]}",
        )
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        new_msgs = data.get("messages", [])
        self.assertEqual(len(new_msgs), 1)
        self.assertEqual(new_msgs[0]["message_id"], posted_ids[2])

        # 5. Polling with cursor pointing to the latest message -> returns empty list
        status, _, body = self._raw_request(
            "GET",
            f"/api/messages?room=main&since={posted_ids[2]}",
        )
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(len(data.get("messages", [])), 0)

    def test_relative_attachment_urls_and_direct_file_serving(self):
        """Validate that file uploads return relative URLs starting with / and can be fetched."""
        test_content = b"Host-agnostic raw IP file content\nLine 2"
        # Save file directly via storage manager
        meta = self.storage.save_bytes(
            content=test_content,
            filename="ip_test_note.txt",
            room="dm-argon",
            sender="mohammad",
        )

        public_url = meta.get("public_url")
        self.assertTrue(public_url.startswith("/"), f"public_url must be relative: {public_url}")
        self.assertNotIn("airboxvip.top", public_url)

        # Register message in room manager so attachment ID lookup works
        msg = self.room_mgr.post_message(
            room="dm-argon",
            sender="mohammad",
            text="Uploaded direct-IP attachment",
            attachments=[meta],
        )

        # 1. Fetch file via relative public_url path
        status, headers, body = self._raw_request(
            "GET",
            public_url,
            headers={"Host": "203.0.113.10:8080"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, test_content)
        self.assertIn("text/plain", headers.get("content-type", ""))

        # 2. Fetch file via /api/attachments/<id> endpoint
        att_id = meta.get("attachment_id")
        status, headers, body = self._raw_request(
            "GET",
            f"/api/attachments/{att_id}",
            headers={"Host": "203.0.113.10:8080"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, test_content)

    def test_client_sdk_incremental_polling_support(self):
        """Validate list_room_messages Python client SDK with since and after parameters."""
        # Post 2 messages via SDK send_room_file
        res1 = send_room_file(
            room="workers-room",
            sender="argon",
            text="First message from SDK",
            api_url=self.base_url,
            forward_telegram=False,
        )
        msg1_id = res1["message"]["message_id"]

        res2 = send_room_file(
            room="workers-room",
            sender="cookie",
            text="Second message from SDK",
            api_url=self.base_url,
            forward_telegram=False,
        )
        msg2_id = res2["message"]["message_id"]

        # Polling after msg1_id should return exactly 1 message (msg2)
        polled = list_room_messages(
            room="workers-room",
            after=msg1_id,
            api_url=self.base_url,
        )
        self.assertEqual(len(polled), 1)
        self.assertEqual(polled[0]["message_id"], msg2_id)


if __name__ == "__main__":
    unittest.main()
