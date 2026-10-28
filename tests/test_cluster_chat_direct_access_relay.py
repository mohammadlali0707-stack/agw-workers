"""tests/test_cluster_chat_direct_access_relay.py

Automated Verification Test Suite for P5:
- Direct-IP Host Header Support (e.g., Host: 185.100.200.10:8080, Origin: http://185.100.200.10:8080)
- Host-Agnostic Auth/Session Cookies (no Domain attribute, Secure only on HTTPS)
- Relative URLs everywhere for messages, uploads, and attachments (never airboxvip.top)
- Incremental HTTP Polling Fallback API (?since=, ?after=) with Cache-Control: no-store
- Deployment configs and helper scripts validation (Nginx direct IP, Iranian relay, README)
- Static codebase audit ensuring zero hardcoded airboxvip.top runtime dependencies in cluster_chat
"""

import http.client
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import urllib.parse

from cluster_chat.attachments import AttachmentStorageManager
from cluster_chat.rooms import RoomManager
from cluster_chat.server import ClusterChatServer


class TestClusterChatDirectAccessRelay(unittest.TestCase):
    """Integration and validation test suite for Direct-IP access and Domestic Relay."""

    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parent.parent
        cls.cluster_chat_dir = cls.repo_root / "cluster_chat"
        cls.deploy_dir = cls.cluster_chat_dir / "deploy"
        cls.web_dir = cls.cluster_chat_dir / "web"

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmpdir.name)
        self.upload_dir = self.tmp_path / "uploads"
        self.data_dir = self.tmp_path / "data"

        self.storage = AttachmentStorageManager(
            upload_dir=self.upload_dir,
            max_upload_mb=50,
        )
        self.room_mgr = RoomManager(data_dir=self.data_dir)

        self.server = ClusterChatServer(
            host="127.0.0.1",
            port=0,
            storage=self.storage,
            room_manager=self.room_mgr,
            web_dir=self.web_dir,
        )
        self.server.start(daemon=True)
        self.port = self.server.port
        self.base_url = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.stop()
        self.tmpdir.cleanup()

    def _http_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Execute a low-level HTTP request against the test server."""
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

    # --------------------------------------------------------------------------
    # 1. Direct IP Host Header & CORS Tests
    # --------------------------------------------------------------------------
    def test_direct_ip_health_and_ping_endpoints(self):
        """Validate health and ping endpoints respond with 200 OK to raw IP Host header."""
        raw_ip_host = "185.100.200.10:8080"
        raw_origin = "http://185.100.200.10:8080"

        for ep in ["/api/health", "/api/ping", "/api/cluster-chat/health", "/api/cluster-chat/ping"]:
            status, headers, body = self._http_request(
                "GET",
                ep,
                headers={
                    "Host": raw_ip_host,
                    "Origin": raw_origin,
                },
            )
            self.assertEqual(status, 200, f"Expected 200 OK for {ep}")
            self.assertNotIn("location", headers, "Must not redirect direct IP requests")
            self.assertEqual(headers.get("access-control-allow-origin"), raw_origin)
            self.assertEqual(headers.get("access-control-allow-credentials"), "true")
            self.assertIn("no-store", headers.get("cache-control", ""))

            data = json.loads(body.decode("utf-8"))
            self.assertTrue(data.get("ok"))
            self.assertEqual(data.get("host"), raw_ip_host)
            self.assertTrue(data.get("direct_ip_supported"))

    def test_direct_ip_cors_preflight(self):
        """Validate CORS OPTIONS preflight succeeds for direct IP origins."""
        raw_origin = "http://185.100.200.10:8080"
        status, headers, _ = self._http_request(
            "OPTIONS",
            "/api/cluster-chat/upload",
            headers={
                "Origin": raw_origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type, X-Requested-With",
            },
        )
        self.assertEqual(status, 204)
        self.assertEqual(headers.get("access-control-allow-origin"), raw_origin)
        self.assertEqual(headers.get("access-control-allow-credentials"), "true")
        self.assertIn("POST", headers.get("access-control-allow-methods", ""))

    def test_direct_ip_cookie_security_policy(self):
        """Validate cookies omit Domain attribute and only set Secure on HTTPS."""
        raw_ip_host = "185.100.200.10:8080"
        login_body = json.dumps({"user": "mohammad"}).encode("utf-8")

        # 1. Plain HTTP Direct IP: NO Domain, NO Secure
        status, headers, body = self._http_request(
            "POST",
            "/api/auth/session",
            headers={
                "Host": raw_ip_host,
                "Content-Type": "application/json",
            },
            body=login_body,
        )
        self.assertEqual(status, 200)
        set_cookie = headers.get("set-cookie", "")
        self.assertTrue(set_cookie, "Set-Cookie must be returned on login")
        self.assertNotIn("domain=", set_cookie.lower(), "Cookie must NOT contain Domain attribute")
        self.assertNotIn("secure", set_cookie.lower(), "Cookie must NOT be Secure over plain HTTP")

        # 2. Reverse proxy with HTTPS: Sets Secure, still NO fixed Domain
        status_s, headers_s, _ = self._http_request(
            "POST",
            "/api/auth/session",
            headers={
                "Host": raw_ip_host,
                "X-Forwarded-Proto": "https",
                "Content-Type": "application/json",
            },
            body=login_body,
        )
        self.assertEqual(status_s, 200)
        set_cookie_s = headers_s.get("set-cookie", "")
        self.assertIn("secure", set_cookie_s.lower(), "Cookie MUST be Secure over HTTPS proxy")
        self.assertNotIn("domain=", set_cookie_s.lower(), "Cookie must NOT contain Domain attribute")

    # --------------------------------------------------------------------------
    # 2. Relative URLs Everywhere & Attachment Lifecycle
    # --------------------------------------------------------------------------
    def test_message_and_attachment_relative_urls(self):
        """Validate posting messages and uploading attachments return relative URLs only."""
        raw_ip_host = "185.100.200.10:8080"

        # 1. Multipart file upload via POST /api/cluster-chat/upload
        boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
        file_payload = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="room"\r\n\r\n'
            f"dm-argon\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="sender"\r\n\r\n'
            f"mohammad\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="text"\r\n\r\n'
            f"Direct-IP upload test with Iranian relay readiness\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="national_net_spec.txt"\r\n'
            f"Content-Type: text/plain\r\n\r\n"
            f"National Internet Resilient Cluster Chat Spec Content\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")

        status, headers, body = self._http_request(
            "POST",
            "/api/cluster-chat/upload",
            headers={
                "Host": raw_ip_host,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            body=file_payload,
        )
        self.assertIn(status, [200, 201])
        upload_resp = json.loads(body.decode("utf-8"))
        self.assertTrue(upload_resp.get("ok"))
        self.assertIn("attachments", upload_resp)
        attachments = upload_resp["attachments"]
        self.assertGreater(len(attachments), 0)

        for att in attachments:
            pub_url = att.get("public_url", "")
            self.assertTrue(pub_url.startswith("/"), f"Attachment public_url must start with '/': {pub_url}")
            self.assertNotIn("airboxvip.top", pub_url)
            self.assertNotIn("http://", pub_url)
            self.assertNotIn("https://", pub_url)

            # 2. Fetch the file using the relative public_url
            f_status, f_headers, f_body = self._http_request(
                "GET",
                pub_url,
                headers={"Host": raw_ip_host},
            )
            self.assertEqual(f_status, 200)
            self.assertIn(b"National Internet Resilient", f_body)
            self.assertNotIn("location", f_headers)

            # 3. Fetch via /api/attachments/<id>
            att_id = att.get("attachment_id")
            if att_id:
                a_status, a_headers, a_body = self._http_request(
                    "GET",
                    f"/api/attachments/{att_id}",
                    headers={"Host": raw_ip_host},
                )
                self.assertEqual(a_status, 200)
                self.assertEqual(a_body, f_body)

    # --------------------------------------------------------------------------
    # 3. Incremental Polling Fallback API
    # --------------------------------------------------------------------------
    def test_incremental_polling_fallback_filtering(self):
        """Validate incremental polling (?since= and ?after=) returns only newer messages."""
        raw_ip_host = "185.100.200.10:8080"
        msg_ids = []

        # Post 4 messages
        for idx in range(1, 5):
            payload = json.dumps({
                "room": "relay-room",
                "sender": f"worker_{idx}",
                "text": f"Telemetry beacon #{idx}",
            }).encode("utf-8")
            status, _, body = self._http_request(
                "POST",
                "/api/cluster-chat/rooms/relay-room/messages",
                headers={
                    "Host": raw_ip_host,
                    "Content-Type": "application/json",
                },
                body=payload,
            )
            self.assertIn(status, [200, 201])
            res = json.loads(body.decode("utf-8"))
            msg_ids.append(res["message"]["message_id"])

        self.assertEqual(len(msg_ids), 4)

        # 1. Full query without cursor
        status, headers, body = self._http_request(
            "GET",
            "/api/messages?room=relay-room",
            headers={"Host": raw_ip_host},
        )
        self.assertEqual(status, 200)
        self.assertIn("no-store", headers.get("cache-control", ""))
        self.assertEqual(headers.get("pragma"), "no-cache")
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(len(data.get("messages", [])), 4)

        # 2. Incremental polling with ?since=<first_id> -> returns 3 newer messages
        status_since, headers_since, body_since = self._http_request(
            "GET",
            f"/api/messages?room=relay-room&since={msg_ids[0]}",
            headers={"Host": raw_ip_host},
        )
        self.assertEqual(status_since, 200)
        self.assertIn("no-store", headers_since.get("cache-control", ""))
        data_since = json.loads(body_since.decode("utf-8"))
        polled_since = data_since.get("messages", [])
        self.assertEqual(len(polled_since), 3)
        self.assertEqual([m["message_id"] for m in polled_since], msg_ids[1:])

        # 3. Incremental polling with ?after=<second_id> -> returns 2 newer messages
        status_after, headers_after, body_after = self._http_request(
            "GET",
            f"/api/cluster-chat/rooms/relay-room/messages?after={msg_ids[1]}",
            headers={"Host": raw_ip_host},
        )
        self.assertEqual(status_after, 200)
        data_after = json.loads(body_after.decode("utf-8"))
        polled_after = data_after.get("messages", [])
        self.assertEqual(len(polled_after), 2)
        self.assertEqual([m["message_id"] for m in polled_after], msg_ids[2:])

        # 4. Incremental polling at latest message -> returns 0 messages
        status_latest, _, body_latest = self._http_request(
            "GET",
            f"/api/messages?room=relay-room&since={msg_ids[-1]}",
            headers={"Host": raw_ip_host},
        )
        self.assertEqual(status_latest, 200)
        data_latest = json.loads(body_latest.decode("utf-8"))
        self.assertEqual(len(data_latest.get("messages", [])), 0)

    # --------------------------------------------------------------------------
    # 4. Deployment Configs & Scripts Validation
    # --------------------------------------------------------------------------
    def test_deployment_files_exist_and_clean(self):
        """Validate all required deploy files exist and contain zero airboxvip.top references."""
        required_files = [
            "nginx-direct-ip.conf",
            "nginx-iran-relay.conf",
            "iran-relay-setup.sh",
            "README-DIRECT-ACCESS.md",
        ]
        for fname in required_files:
            fpath = self.deploy_dir / fname
            self.assertTrue(fpath.is_file(), f"Missing required deployment file: {fpath}")
            content = fpath.read_text(encoding="utf-8")
            self.assertNotIn("airboxvip.top", content, f"{fname} must not reference airboxvip.top")

    def test_nginx_configs_structure_and_syntax(self):
        """Validate Nginx configuration templates for direct-IP and domestic relay."""
        nginx_direct = (self.deploy_dir / "nginx-direct-ip.conf").read_text(encoding="utf-8")
        nginx_relay = (self.deploy_dir / "nginx-iran-relay.conf").read_text(encoding="utf-8")

        # 1. Direct-IP Nginx config checks
        self.assertIn("server_name _;", nginx_direct)
        self.assertIn("client_max_body_size 50M;", nginx_direct)
        self.assertIn("$connection_upgrade", nginx_direct)
        self.assertIn("proxy_set_header Upgrade $http_upgrade;", nginx_direct)
        self.assertIn("proxy_buffering off;", nginx_direct)
        self.assertIn("text/event-stream", nginx_direct)

        # 2. Relay Nginx config checks
        self.assertIn("server_name _;", nginx_relay)
        self.assertIn("client_max_body_size 50M;", nginx_relay)
        self.assertIn("foreign_cluster_chat_vps", nginx_relay)
        self.assertIn("proxy_set_header Host $http_host;", nginx_relay)
        self.assertIn("proxy_set_header X-Forwarded-Host $http_host;", nginx_relay)

        # 3. Test syntax with nginx -t if nginx is installed on the system
        nginx_bin = shutil.which("nginx")
        if nginx_bin:
            for conf_file in [self.deploy_dir / "nginx-direct-ip.conf", self.deploy_dir / "nginx-iran-relay.conf"]:
                with tempfile.TemporaryDirectory() as td:
                    tdp = Path(td)
                    # Use unprivileged port 18080 so non-root test doesn't fail on socket binding
                    raw_text = conf_file.read_text(encoding="utf-8")
                    test_text = raw_text.replace("listen 80;", "listen 18080;").replace("listen [::]:80;", "listen [::]:18080;")
                    test_conf = tdp / conf_file.name
                    test_conf.write_text(test_text)

                    nginx_master = tdp / "nginx.conf"
                    pid_file = tdp / "nginx.pid"
                    err_file = tdp / "error.log"
                    nginx_master.write_text(f"""
pid {pid_file};
error_log {err_file};
events {{
    worker_connections 512;
}}
http {{
    access_log off;
    include {test_conf};
}}
""")
                    proc = subprocess.run(
                        [nginx_bin, "-t", "-c", str(nginx_master)],
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        proc.returncode,
                        0,
                        f"nginx -t failed for {conf_file.name}:\n{proc.stderr}",
                    )
                    self.assertIn("syntax is ok", proc.stderr)

    def test_iran_relay_setup_script(self):
        """Validate iran-relay-setup.sh script execution, options, and dry-run output."""
        script_path = self.deploy_dir / "iran-relay-setup.sh"
        self.assertTrue(os.access(script_path, os.X_OK), "iran-relay-setup.sh must be executable")

        # 1. Check bash syntax via bash -n
        syntax_res = subprocess.run(["bash", "-n", str(script_path)], capture_output=True, text=True)
        self.assertEqual(syntax_res.returncode, 0, f"Bash syntax check failed: {syntax_res.stderr}")

        # 2. Run --help
        help_res = subprocess.run([str(script_path), "--help"], capture_output=True, text=True)
        self.assertEqual(help_res.returncode, 0)
        self.assertIn("Cluster Chat Domestic Iranian Relay Setup", help_res.stdout)
        self.assertIn("--foreign-ip", help_res.stdout)
        self.assertIn("--mode <socat|nginx>", help_res.stdout)

        # 3. Dry-run socat mode
        socat_res = subprocess.run(
            [str(script_path), "--foreign-ip", "198.51.100.42", "--mode", "socat", "--dry-run"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(socat_res.returncode, 0)
        self.assertIn("socat TCP4-LISTEN:8080,fork,reuseaddr TCP4:198.51.100.42:8080", socat_res.stdout)
        self.assertIn("cluster-chat-relay", socat_res.stdout)

        # 4. Dry-run nginx mode
        nginx_res = subprocess.run(
            [str(script_path), "--foreign-ip", "198.51.100.42", "--local-port", "8443", "--mode", "nginx", "--dry-run"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(nginx_res.returncode, 0)
        self.assertIn("198.51.100.42", nginx_res.stdout)
        self.assertIn("listen 8443;", nginx_res.stdout)

    def test_readme_direct_access_documentation(self):
        """Validate README-DIRECT-ACCESS.md contains both English and Persian guides."""
        readme_path = self.deploy_dir / "README-DIRECT-ACCESS.md"
        content = readme_path.read_text(encoding="utf-8")

        self.assertIn("English Guide", content)
        self.assertIn("راهنمای فارسی", content)
        self.assertIn("نت ملی", content)
        self.assertIn("CLUSTER_CHAT_PUBLIC_URL", content)
        self.assertIn("iran-relay-setup.sh", content)
        self.assertIn("document.execCommand", content)
        self.assertNotIn("airboxvip.top", content)

    # --------------------------------------------------------------------------
    # 5. Static Codebase Audit: Zero Hardcoded airboxvip.top in cluster_chat
    # --------------------------------------------------------------------------
    def test_static_codebase_zero_airboxvip_audit(self):
        """Comprehensive scan: zero runtime airboxvip.top occurrences in cluster_chat."""
        cluster_chat_path = self.cluster_chat_dir
        offenders = []

        # Scan all files in cluster_chat except comments and legacy regex normalization test patterns
        for file_path in cluster_chat_path.rglob("*"):
            if file_path.is_dir() or "__pycache__" in file_path.parts:
                continue
            if file_path.suffix in [".pyc", ".pyo"]:
                continue

            text = file_path.read_text(encoding="utf-8", errors="ignore")
            for line_no, line in enumerate(text.splitlines(), start=1):
                clean_line = line.strip()
                # Skip comments explaining legacy airboxvip sanitization or deprecation
                if clean_line.startswith(("#", "//", "/*", "*", "<!--")):
                    continue
                # In app.js, skip regex definitions that sanitize or strip airboxvip
                if file_path.name == "app.js" and ("replace(" in clean_line or "/airboxvip" in clean_line or "airboxvip" in clean_line and "test(" in clean_line):
                    continue

                if "airboxvip.top" in clean_line:
                    offenders.append(f"{file_path.relative_to(self.repo_root)}:{line_no} -> {clean_line}")

        self.assertEqual(
            offenders,
            [],
            f"Found hardcoded active airboxvip.top in cluster_chat:\n" + "\n".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
