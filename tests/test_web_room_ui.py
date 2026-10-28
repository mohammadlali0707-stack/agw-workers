"""tests/test_web_room_ui.py

Automated verification tests for Cluster-Chat Web Room UI redesign:
- Full-viewport (100dvh) flex column layout with 100vh fallback
- Maximized chat message stream (flex: 1 1 auto, min-height: 0, overflow-y: auto)
- Default-hidden agent status modal/drawer (0px screen space in default visible flow)
- Compact header with room switcher tabs and agent status toggle
- Persian RTL (lang="fa", dir="rtl") layout and CSS logical properties
- Functional room switching and room isolation across main, dm-argon, and workers
- Clean compose footer with file upload attachment trigger
- Safe static HTTP asset delivery and agents schema contract
"""

import json
from pathlib import Path
import re
import tempfile
import unittest
import urllib.request

from cluster_chat.attachments import AttachmentStorageManager
from cluster_chat.rooms import RoomManager
from cluster_chat.server import ClusterChatServer


def _extract_css_block(css: str, selector_prefix: str) -> str:
    """Extract the contents of a CSS rule or media query block with balanced braces."""
    start = css.find(selector_prefix)
    if start == -1:
        return ""
    brace_open = css.find("{", start)
    if brace_open == -1:
        return ""
    depth = 1
    i = brace_open + 1
    while i < len(css) and depth > 0:
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
        i += 1
    return css[brace_open + 1 : i - 1]


class TestWebRoomUIRedesign(unittest.TestCase):
    """Test suite verifying Web Room UI layout, CSS rules, DOM structure, and static serving."""

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
        self.base_url = self.server.get_url()

    def tearDown(self):
        self.server.stop()
        self.tmpdir.cleanup()

    def _http_get(self, path: str) -> tuple[int, dict[str, str], bytes]:
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            headers = {k.lower(): v for k, v in resp.getheaders()}
            return resp.status, headers, resp.read()

    def _http_post_json(self, path: str, payload: dict) -> tuple[int, dict[str, str], dict]:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            headers = {k.lower(): v for k, v in resp.getheaders()}
            body = json.loads(resp.read().decode("utf-8"))
            return resp.status, headers, body

    def test_full_viewport_100dvh_css_layout(self):
        """Verify CSS delivered by server defines 100dvh flex column shell and 100vh fallback."""
        _, _, body = self._http_get("/style.css")
        css = body.decode("utf-8")

        # 1. html, body styling
        html_body_block = _extract_css_block(css, "html, body")
        self.assertTrue(html_body_block, "html, body block must exist in served stylesheet")
        self.assertIn("height: 100%", html_body_block)
        self.assertIn("overflow: hidden", html_body_block)

        # 2. Root shell (.chat-app / .app-layout)
        app_block = _extract_css_block(css, ".chat-app")
        if not app_block:
            app_block = _extract_css_block(css, ".app-layout")
        self.assertTrue(app_block, "Root container block (.chat-app / .app-layout) must exist")
        self.assertIn("100vh", app_block)
        self.assertIn("100dvh", app_block)
        self.assertIn("display: flex", app_block)
        self.assertIn("flex-direction: column", app_block)
        self.assertIn("overflow: hidden", app_block)

    def test_chat_messages_viewport_maximization(self):
        """Verify chat messages container claims available height with flex: 1 1 auto and min-height: 0."""
        _, _, body = self._http_get("/style.css")
        css = body.decode("utf-8")

        # Desktop rules for .chat-container
        chat_block = _extract_css_block(css, ".chat-container")
        self.assertTrue(chat_block, ".chat-container block must exist in served stylesheet")
        self.assertIn("flex: 1 1 auto", chat_block)
        self.assertIn("min-height: 0", chat_block)
        self.assertIn("overflow-y: auto", chat_block)

        # Responsive media query block strictly isolated
        media_block = _extract_css_block(css, "@media (max-width: 768px)")
        self.assertTrue(media_block, "@media (max-width: 768px) block must exist")
        # Ensure inside this media query block specifically, .chat-container is maximized
        mobile_chat_block = _extract_css_block(media_block, ".chat-container")
        self.assertTrue(mobile_chat_block, ".chat-container rule must exist inside mobile media query")
        self.assertIn("flex: 1 1 auto", mobile_chat_block)
        self.assertIn("min-height: 0", mobile_chat_block)

    def test_compact_header_and_room_switcher(self):
        """Verify header is compact (48px-56px) and contains room switcher tabs and agent status toggle."""
        # CSS assertions
        _, _, css_body = self._http_get("/style.css")
        css = css_body.decode("utf-8")
        header_block = _extract_css_block(css, ".chat-header")
        self.assertTrue(header_block, ".chat-header block must exist in served stylesheet")
        self.assertIn("flex: 0 0 auto", header_block)
        self.assertIn("min-height: 48px", header_block)
        self.assertIn("max-height: 56px", header_block)

        # HTML assertions
        _, _, html_body = self._http_get("/")
        html = html_body.decode("utf-8")
        self.assertIn('<header class="chat-header">', html)
        self.assertIn('id="room-list"', html)
        self.assertIn('id="agent-status-toggle"', html)
        self.assertIn('class="btn-agent-status"', html)
        self.assertIn('id="refresh-btn"', html)

    def test_bulky_agent_status_hidden_by_default(self):
        """Verify agent status cards are removed from visible flow and tucked in default-hidden modal."""
        _, _, html_body = self._http_get("/")
        html = html_body.decode("utf-8")

        # Modal exists and has class 'hidden'
        modal_match = re.search(r'<div[^>]*id="agent-status-modal"[^>]*>', html)
        self.assertIsNotNone(modal_match, "agent-status-modal element must exist")
        self.assertIn("hidden", modal_match.group(0), "agent-status-modal must have 'hidden' class by default")

        # Modal close button and backdrop exist
        self.assertIn('id="agent-modal-close"', html)
        self.assertIn('id="agent-modal-backdrop"', html)

        # CSS hides .hidden element with !important
        _, _, css_body = self._http_get("/style.css")
        css = css_body.decode("utf-8")
        hidden_block = _extract_css_block(css, ".hidden")
        self.assertIn("display: none !important", hidden_block)

    def test_persian_rtl_and_logical_properties(self):
        """Verify Persian RTL attributes and stylesheet logical properties."""
        _, _, html_body = self._http_get("/")
        html = html_body.decode("utf-8")
        self.assertRegex(html, r'<html[^>]*lang="fa"[^>]*dir="rtl"')

        _, _, css_body = self._http_get("/style.css")
        css = css_body.decode("utf-8")
        rtl_block = _extract_css_block(css, 'html[dir="rtl"]')
        self.assertIn("direction: rtl", rtl_block)
        self.assertIn("text-align: right", rtl_block)

        # Check for CSS logical properties (bidirectional compatibility)
        self.assertIn("margin-inline-start", css)
        self.assertIn("border-inline-start", css)

    def test_clean_compose_bar_and_upload_preservation(self):
        """Verify compose footer structure and upload trigger preservation."""
        _, _, html_body = self._http_get("/")
        html = html_body.decode("utf-8")
        self.assertIn('class="composer-container"', html)
        self.assertIn('id="composer-form"', html)
        self.assertIn('id="attach-btn"', html)
        self.assertIn('id="file-input"', html)
        self.assertIn('id="message-text"', html)
        self.assertIn('id="send-btn"', html)
        self.assertIn('id="staged-tray"', html)

    def test_functional_room_switching_and_isolation(self):
        """Verify room isolation and message delivery across main, dm-argon, and workers."""
        # 1. Post distinct messages to main, dm-argon, and workers
        status1, _, body1 = self._http_post_json(
            "/api/cluster-chat/rooms/main/messages",
            {"sender": "mohammad", "text": "Hello main channel"},
        )
        self.assertEqual(status1, 201)
        self.assertTrue(body1.get("ok"))

        status2, _, body2 = self._http_post_json(
            "/api/cluster-chat/rooms/dm-argon/messages",
            {"sender": "argon", "text": "Hello dm-argon direct chat"},
        )
        self.assertEqual(status2, 201)
        self.assertTrue(body2.get("ok"))

        status3, _, body3 = self._http_post_json(
            "/api/cluster-chat/rooms/workers/messages",
            {"sender": "cookie", "text": "Hello workers mesh room"},
        )
        self.assertEqual(status3, 201)
        self.assertTrue(body3.get("ok"))

        # 2. Query messages per room and verify strict isolation
        _, _, main_resp = self._http_get("/api/cluster-chat/rooms/main/messages")
        main_msgs = json.loads(main_resp.decode("utf-8")).get("messages", [])
        self.assertTrue(any("Hello main channel" in m.get("text", "") for m in main_msgs))
        self.assertFalse(any("Hello dm-argon" in m.get("text", "") for m in main_msgs))
        self.assertFalse(any("Hello workers mesh" in m.get("text", "") for m in main_msgs))

        _, _, argon_resp = self._http_get("/api/cluster-chat/rooms/dm-argon/messages")
        argon_msgs = json.loads(argon_resp.decode("utf-8")).get("messages", [])
        self.assertTrue(any("Hello dm-argon direct chat" in m.get("text", "") for m in argon_msgs))
        self.assertFalse(any("Hello main channel" in m.get("text", "") for m in argon_msgs))

        _, _, workers_resp = self._http_get("/api/cluster-chat/rooms/workers/messages")
        workers_msgs = json.loads(workers_resp.decode("utf-8")).get("messages", [])
        self.assertTrue(any("Hello workers mesh room" in m.get("text", "") for m in workers_msgs))
        self.assertFalse(any("Hello main channel" in m.get("text", "") for m in workers_msgs))

    def test_api_agents_status_schema_contract(self):
        """Verify /api/cluster-chat/agents endpoint fulfills schema contract without invented dummy fixtures."""
        status, headers, body = self._http_get("/api/cluster-chat/agents")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        agents = data.get("agents")
        self.assertIsInstance(agents, list)
        self.assertEqual(data.get("count"), len(agents))
        self.assertGreater(len(agents), 0)

        # Validate agent item schema contract
        for agent in agents:
            self.assertIsInstance(agent, dict)
            self.assertIn("name", agent)
            self.assertIn("role", agent)
            self.assertIn("status", agent)
            self.assertTrue(agent["name"])
            self.assertTrue(agent["status"])


if __name__ == "__main__":
    unittest.main()
