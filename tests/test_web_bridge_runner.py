"""tests/test_web_bridge_runner.py

Comprehensive unit and integration tests for scripts/web_bridge_runner.py.
Covers SSRF validation across private, loopback, link-local, and metadata addresses,
HTML to Markdown parsing and script stripping, safe fetching, binary attachment packaging,
and outbox / snapshot queue processing.
"""

from __future__ import annotations

import base64
import html
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.request

import cluster_chat.relay_protocol as rp
from scripts.web_bridge_runner import (
    BLOCKED_HOSTNAMES,
    HTMLToMarkdownParser,
    SSRFError,
    fetch_external_resource,
    format_size_human,
    process_web_fetch,
    sanitize_filename,
    scan_and_process_queue,
    update_room_snapshot,
    validate_safe_url,
    write_outbox_envelope,
)


class TestSSRFValidation(unittest.TestCase):
    """Test SSRF protection rules in validate_safe_url."""

    def test_blocks_invalid_schemes(self):
        for scheme in ("ftp://example.com", "file:///etc/passwd", "gopher://1.2.3.4", "javascript:alert(1)"):
            with self.assertRaises(SSRFError):
                validate_safe_url(scheme)

    def test_blocks_loopback_ips(self):
        for ip in ("127.0.0.1", "127.0.1.1", "127.100.0.1"):
            with self.assertRaises(SSRFError):
                validate_safe_url(f"http://{ip}/path")

    def test_blocks_rfc1918_private_ips(self):
        private_ips = (
            "10.0.0.1",
            "10.255.255.254",
            "172.16.0.1",
            "172.31.255.254",
            "192.168.0.1",
            "192.168.1.100",
        )
        for ip in private_ips:
            with self.assertRaises(SSRFError):
                validate_safe_url(f"http://{ip}/index.html")

    def test_blocks_cloud_metadata_ips(self):
        for ip in ("169.254.169.254", "100.100.100.200", "0.0.0.0", "255.255.255.255"):
            with self.assertRaises(SSRFError):
                validate_safe_url(f"http://{ip}/latest/meta-data/")

    def test_blocks_cgnat_network(self):
        with self.assertRaises(SSRFError):
            validate_safe_url("http://100.64.0.1/")

    def test_blocks_blocked_hostnames_and_suffixes(self):
        blocked = (
            "http://localhost/admin",
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://metadata/secret",
            "http://db.internal/query",
            "http://server.local/status",
            "http://router.lan/admin",
            "http://nas.home/shares",
            "http://auth.corp/login",
            "http://foo.localhost:8080/",
        )
        for url in blocked:
            with self.assertRaises(SSRFError):
                validate_safe_url(url)

    def test_blocks_ipv6_loopback_and_link_local(self):
        for url in ("http://[::1]/", "http://[fe80::1]/", "http://[::ffff:127.0.0.1]/"):
            with self.assertRaises(SSRFError):
                validate_safe_url(url)

    def test_blocks_dns_resolving_to_private_ip(self):
        mock_addr = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.50", 80))]
        with patch("socket.getaddrinfo", return_value=mock_addr):
            with self.assertRaises(SSRFError):
                validate_safe_url("http://evil-rebinding-domain.com/")

    def test_allows_public_domains(self):
        mock_addr = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        with patch("socket.getaddrinfo", return_value=mock_addr):
            url = validate_safe_url("https://example.com/test-article")
            self.assertEqual(url, "https://example.com/test-article")


class TestHTMLToMarkdownParser(unittest.TestCase):
    """Test HTML to clean Markdown parser including script stripping and entity handling."""

    def test_converts_headings_and_paragraphs(self):
        raw_html = (
            "<html><head><title>Test Title</title></head><body>"
            "<h1>Header 1</h1>"
            "<p>This is a paragraph with <b>bold</b> and <i>italic</i> words.</p>"
            "<h2>Header 2</h2>"
            "<p>Another paragraph.</p>"
            "</body></html>"
        )
        parser = HTMLToMarkdownParser()
        parser.feed(raw_html)
        self.assertEqual(parser.title.strip(), "Test Title")
        md = parser.get_markdown()
        self.assertIn("# Header 1", md)
        self.assertIn("## Header 2", md)
        self.assertIn("**bold**", md)
        self.assertIn("*italic*", md)
        self.assertIn("This is a paragraph", md)

    def test_strips_scripts_styles_and_navs(self):
        raw_html = (
            "<div>"
            "<script>alert('malicious script execution!');</script>"
            "<style>body { color: red; }</style>"
            "<nav><a href='/home'>Home</a></nav>"
            "<header>Site Header</header>"
            "<footer>Copyright 2026</footer>"
            "<p>Clean content preserved here.</p>"
            "</div>"
        )
        parser = HTMLToMarkdownParser()
        parser.feed(raw_html)
        md = parser.get_markdown()
        self.assertNotIn("malicious", md)
        self.assertNotIn("color: red", md)
        self.assertNotIn("Site Header", md)
        self.assertNotIn("Copyright 2026", md)
        self.assertIn("Clean content preserved here.", md)

    def test_converts_lists_and_code_blocks(self):
        raw_html = (
            "<ul><li>Bullet A</li><li>Bullet B</li></ul>"
            "<ol><li>Number 1</li><li>Number 2</li></ol>"
            "<pre><code>def hello():\n    return 'world'</code></pre>"
            "<blockquote>Important quote</blockquote>"
        )
        parser = HTMLToMarkdownParser()
        parser.feed(raw_html)
        md = parser.get_markdown()
        self.assertIn("- Bullet A", md)
        self.assertIn("- Bullet B", md)
        self.assertIn("1. Number 1", md)
        self.assertIn("2. Number 2", md)
        self.assertIn("```\ndef hello():\n    return 'world'\n```", md)
        self.assertIn("> Important quote", md)

    def test_converts_links_with_base_url(self):
        raw_html = (
            "<p>Check <a href='/docs'>Documentation</a> and "
            "<a href='https://example.com/external'>External</a>.</p>"
        )
        parser = HTMLToMarkdownParser(base_url="https://leaddesk.ai")
        parser.feed(raw_html)
        md = parser.get_markdown()
        self.assertIn("[Documentation](https://leaddesk.ai/docs)", md)
        self.assertIn("[External](https://example.com/external)", md)


class TestFetchExternalResource(unittest.TestCase):
    """Test safe resource fetching across Markdown, HTML, and binary attachment modes."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.queue_dir = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    @patch("scripts.web_bridge_runner.validate_safe_url", return_value="https://example.com/article")
    def test_fetch_html_mode_markdown(self, mock_validate):
        html_payload = b"<html><head><title>Sample Article</title></head><body><h1>Article Headline</h1><p>Great article text.</p></body></html>"

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "text/html; charset=utf-8"}
        mock_resp.read.side_effect = [html_payload, b""]
        mock_resp.geturl.return_value = "https://example.com/article"
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.build_opener") as mock_build_opener:
            mock_opener = MagicMock()
            mock_opener.open.return_value = mock_resp
            mock_build_opener.return_value = mock_opener

            res = fetch_external_resource(
                url="https://example.com/article",
                mode="markdown",
                queue_root=self.queue_dir,
            )

            self.assertEqual(res["status_code"], 200)
            self.assertEqual(res["title"], "Sample Article")
            self.assertIn("# Article Headline", res["body_markdown"])
            self.assertIn("Great article text.", res["body_markdown"])
            self.assertFalse(res["truncated"])
            self.assertEqual(len(res["attachments"]), 0)

    @patch("scripts.web_bridge_runner.validate_safe_url", return_value="https://example.com/report.pdf")
    def test_fetch_binary_file_mode(self, mock_validate):
        pdf_bytes = b"%PDF-1.4 Mock PDF binary content stream for testing attachment packaging"

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {
            "Content-Type": "application/pdf",
            "Content-Disposition": 'attachment; filename="quarterly_report.pdf"',
        }
        mock_resp.read.side_effect = [pdf_bytes, b""]
        mock_resp.geturl.return_value = "https://example.com/report.pdf"
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.build_opener") as mock_build_opener:
            mock_opener = MagicMock()
            mock_opener.open.return_value = mock_resp
            mock_build_opener.return_value = mock_opener

            res = fetch_external_resource(
                url="https://example.com/report.pdf",
                mode="file",
                queue_root=self.queue_dir,
            )

            self.assertEqual(res["status_code"], 200)
            self.assertEqual(len(res["attachments"]), 1)
            att = res["attachments"][0]
            self.assertEqual(att["name"], "quarterly_report.pdf")
            self.assertEqual(att["size"], len(pdf_bytes))

            # Verify saved file in relay_queue/attachments/
            att_files = list((self.queue_dir / "attachments").glob("*.json"))
            self.assertEqual(len(att_files), 1)
            saved_json = json.loads(att_files[0].read_text(encoding="utf-8"))
            self.assertEqual(saved_json["filename"], "quarterly_report.pdf")
            self.assertEqual(base64.b64decode(saved_json["data_b64"]), pdf_bytes)


class TestOutboxAndSnapshots(unittest.TestCase):
    """Test writing envelopes to outbox and updating rolling snapshot documents."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.queue_dir = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_update_room_snapshot_schema(self):
        outbox_dir = self.queue_dir / "outbox"
        env1 = rp.create_envelope(room="main", sender="web-bridge", body="Response 1")
        env2 = rp.create_envelope(room="main", sender="web-bridge", body="Response 2")

        update_room_snapshot("main", env1, outbox_dir)
        snapshot_file = update_room_snapshot("main", env2, outbox_dir)

        self.assertTrue(snapshot_file.exists())
        data = json.loads(snapshot_file.read_text(encoding="utf-8"))
        self.assertTrue(data["ok"])
        self.assertEqual(data["room"], "main")
        self.assertEqual(len(data["messages"]), 2)
        self.assertEqual(data["messages"][0]["msg_id"], env1["msg_id"])
        self.assertEqual(data["messages"][1]["msg_id"], env2["msg_id"])

    def test_write_outbox_envelope_with_encryption(self):
        passphrase = "test-shared-passphrase"
        env = rp.create_envelope(room="dm-argon", sender="web-bridge", body="Secret web response")

        outbox_file, snapshot_file = write_outbox_envelope(
            envelope=env,
            room="dm-argon",
            request_id="req_test_123",
            queue_root=self.queue_dir,
            passphrase=passphrase,
        )

        self.assertTrue(outbox_file.exists())
        self.assertTrue(snapshot_file.exists())

        # Verify outbox file is encrypted
        outbox_doc = json.loads(outbox_file.read_text(encoding="utf-8"))
        self.assertTrue(outbox_doc["encrypted"])
        self.assertEqual(rp.decrypt_payload(outbox_doc, passphrase), "Secret web response")


class TestProcessWebFetchAndQueueScan(unittest.TestCase):
    """Test process_web_fetch and scan_and_process_queue integration."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.queue_dir = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    @patch("scripts.web_bridge_runner.fetch_external_resource")
    def test_process_web_fetch_success(self, mock_fetch):
        mock_fetch.return_value = {
            "status_code": 200,
            "body_markdown": "### Web Page Extracted\nSample content",
            "attachments": [],
        }

        env = process_web_fetch(
            url="https://example.com/test",
            room="main",
            mode="markdown",
            request_id="req_ok",
            queue_root=self.queue_dir,
        )

        self.assertEqual(env["room"], "main")
        self.assertEqual(env["sender"], "web-bridge")
        self.assertIn("Sample content", env["body"])

    @patch("scripts.web_bridge_runner.fetch_external_resource", side_effect=SSRFError("Blocked private IP 10.0.0.1"))
    def test_process_web_fetch_ssrf_error_generates_envelope(self, mock_fetch):
        env = process_web_fetch(
            url="http://10.0.0.1/admin",
            room="workers",
            mode="markdown",
            request_id="req_bad",
            queue_root=self.queue_dir,
        )

        self.assertEqual(env["room"], "workers")
        self.assertEqual(env["sender"], "web-bridge")
        self.assertIn("Web Fetch Failed", env["body"])
        self.assertIn("SSRF Security Violation", env["body"])

    @patch("scripts.web_bridge_runner.fetch_external_resource")
    def test_scan_and_process_queue(self, mock_fetch):
        mock_fetch.return_value = {
            "status_code": 200,
            "body_markdown": "Queue processed result",
            "attachments": [],
        }

        # Create pending request in relay_queue/web_requests/req_001.json
        req_dir = self.queue_dir / "web_requests"
        req_dir.mkdir(parents=True, exist_ok=True)
        req_file = req_dir / "req_001.json"
        req_file.write_text(
            json.dumps({
                "url": "https://example.com/item",
                "room": "dm-argon",
                "mode": "markdown",
                "request_id": "req_001",
            }),
            encoding="utf-8",
        )

        results = scan_and_process_queue(queue_root=self.queue_dir)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["room"], "dm-argon")

        # Request file should have been deleted after processing
        self.assertFalse(req_file.exists())


class TestUtilities(unittest.TestCase):
    """Test helper functions sanitize_filename and format_size_human."""

    def test_sanitize_filename(self):
        self.assertEqual(sanitize_filename("../../../etc/passwd"), "passwd")
        self.assertEqual(sanitize_filename("document (1).pdf"), "document__1_.pdf")
        self.assertEqual(sanitize_filename(""), "download.bin")

    def test_format_size_human(self):
        self.assertEqual(format_size_human(500), "500 B")
        self.assertEqual(format_size_human(2048), "2.0 KB")
        self.assertEqual(format_size_human(2097152), "2.00 MB")


if __name__ == "__main__":
    unittest.main()
