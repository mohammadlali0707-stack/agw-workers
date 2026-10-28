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


def _make_multipart(fields: dict[str, str], files: list[tuple[str, str, bytes, str]]) -> tuple[bytes, str]:
    """Helper to build multipart/form-data bytes and content-type boundary string."""
    boundary = "----WebRoomUITestBoundary" + "W" * 16
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

    def _http_post_multipart(self, path: str, fields: dict, files: list) -> tuple[int, dict[str, str], dict]:
        data, content_type = _make_multipart(fields, files)
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": content_type},
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
        """Verify compose footer structure, CSS flex layout, and upload trigger preservation."""
        _, _, html_body = self._http_get("/")
        html = html_body.decode("utf-8")
        self.assertIn('class="composer-container"', html)
        self.assertIn('id="composer-form"', html)
        self.assertIn('id="attach-btn"', html)
        self.assertIn('id="file-input"', html)
        self.assertIn('id="message-text"', html)
        self.assertIn('id="send-btn"', html)
        self.assertIn('id="staged-tray"', html)

        _, _, css_body = self._http_get("/style.css")
        css = css_body.decode("utf-8")
        composer_block = _extract_css_block(css, ".composer-container")
        self.assertIn("flex: 0 0 auto", composer_block)
        self.assertIn("width: 100%", composer_block)
        self.assertIn("box-sizing: border-box", composer_block)

        form_block = _extract_css_block(css, ".composer-form")
        self.assertIn("display: flex", form_block)
        self.assertIn("align-items: center", form_block)
        self.assertIn("gap: 8px", form_block)
        self.assertIn("padding: 8px 12px", form_block)

        input_block = _extract_css_block(css, ".message-input")
        self.assertIn("flex: 1 1 auto", input_block)
        self.assertIn("min-width: 0", input_block)
        self.assertIn("position: static", input_block)

    def test_functional_room_switching_and_isolation(self):
        """Verify complete bidirectional room isolation and message delivery across main, dm-argon, and workers."""
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

        # 2. Query messages per room and verify complete bidirectional isolation
        _, _, main_resp = self._http_get("/api/cluster-chat/rooms/main/messages")
        main_msgs = json.loads(main_resp.decode("utf-8")).get("messages", [])
        self.assertTrue(any("Hello main channel" in m.get("text", "") for m in main_msgs))
        self.assertFalse(any("Hello dm-argon" in m.get("text", "") for m in main_msgs))
        self.assertFalse(any("Hello workers mesh" in m.get("text", "") for m in main_msgs))

        _, _, argon_resp = self._http_get("/api/cluster-chat/rooms/dm-argon/messages")
        argon_msgs = json.loads(argon_resp.decode("utf-8")).get("messages", [])
        self.assertTrue(any("Hello dm-argon direct chat" in m.get("text", "") for m in argon_msgs))
        self.assertFalse(any("Hello main channel" in m.get("text", "") for m in argon_msgs))
        self.assertFalse(any("Hello workers mesh" in m.get("text", "") for m in argon_msgs))

        _, _, workers_resp = self._http_get("/api/cluster-chat/rooms/workers/messages")
        workers_msgs = json.loads(workers_resp.decode("utf-8")).get("messages", [])
        self.assertTrue(any("Hello workers mesh room" in m.get("text", "") for m in workers_msgs))
        self.assertFalse(any("Hello main channel" in m.get("text", "") for m in workers_msgs))
        self.assertFalse(any("Hello dm-argon" in m.get("text", "") for m in workers_msgs))

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

    def test_clean_compose_bar_css_layout_and_rules(self):
        """Verify CSS layout rules for clean compose bar: flex row, static textarea, non-overlapping controls."""
        _, _, body = self._http_get("/style.css")
        css = body.decode("utf-8")

        # 1. Compose footer container
        composer_container_block = _extract_css_block(css, ".composer-container")
        self.assertTrue(composer_container_block, ".composer-container block must exist in served stylesheet")
        self.assertIn("flex: 0 0 auto", composer_container_block)
        self.assertIn("width: 100%", composer_container_block)
        self.assertIn("box-sizing: border-box", composer_container_block)

        # 2. Compose form horizontal flex row
        form_block = _extract_css_block(css, ".composer-form")
        self.assertTrue(form_block, ".composer-form block must exist in served stylesheet")
        self.assertIn("display: flex", form_block)
        self.assertIn("align-items: center", form_block)
        self.assertIn("gap: 8px", form_block)
        self.assertIn("padding: 8px 12px", form_block)

        # 3. Attachment button
        attach_block = _extract_css_block(css, ".btn-attach")
        self.assertTrue(attach_block, ".btn-attach block must exist")
        self.assertIn("flex: 0 0 auto", attach_block)

        # 4. Message input / textarea
        input_block = _extract_css_block(css, ".message-input")
        self.assertTrue(input_block, ".message-input block must exist")
        self.assertIn("flex: 1 1 auto", input_block)
        self.assertIn("min-width: 0", input_block)
        self.assertIn("position: static", input_block)

        # 5. Send button
        send_block = _extract_css_block(css, ".btn-send")
        self.assertTrue(send_block, ".btn-send block must exist")
        self.assertIn("flex: 0 0 auto", send_block)

    def test_clean_compose_bar_dom_no_overlapping_controls(self):
        """Verify DOM structure: compose form contains ONLY attachment button, textarea, and send button."""
        _, _, html_body = self._http_get("/")
        html = html_body.decode("utf-8")

        # Extract .composer-container block
        footer_match = re.search(r'<footer[^>]*class="[^"]*composer-container[^"]*"[^>]*>(.*?)</footer>', html, re.DOTALL)
        self.assertIsNotNone(footer_match, "composer-container footer element must exist")
        footer_content = footer_match.group(1)

        # Staging tray must appear before the composer-form in the footer DOM flow
        staged_idx = footer_content.find('id="staged-tray"')
        form_idx = footer_content.find('id="composer-form"')
        self.assertNotEqual(staged_idx, -1, "staged-tray must be inside composer-container")
        self.assertNotEqual(form_idx, -1, "composer-form must be inside composer-container")
        self.assertLess(staged_idx, form_idx, "staged-tray preview strip must be placed above composer-form")

        # Extract composer-form content
        form_match = re.search(r'<form[^>]*id="composer-form"[^>]*>(.*?)</form>', footer_content, re.DOTALL)
        self.assertIsNotNone(form_match, "composer-form form element must exist")
        form_content = form_match.group(1)

        # Inside composer-form, only attach-btn, message-text, and send-btn exist
        self.assertIn('id="attach-btn"', form_content)
        self.assertIn('id="message-text"', form_content)
        self.assertIn('id="send-btn"', form_content)

        # Count direct child tags in composer-form: only button, textarea, and span (inside send button) allowed
        tags = re.findall(r'<([a-zA-Z0-9]+)', form_content)
        for tag in tags:
            self.assertIn(tag.lower(), ["button", "textarea", "span"], f"Unexpected tag <{tag}> in compose bar")

        # Ensure no chip, preset, badge, or quick-reply elements inside composer-container or composer-form
        self.assertNotIn("chip", footer_content.lower())
        self.assertNotIn("preset", footer_content.lower())
        self.assertNotIn("quick", footer_content.lower())
        self.assertNotIn("badge", form_content.lower())

        # Ensure message-text textarea has RTL auto direction and clean attributes
        textarea_match = re.search(r'<textarea[^>]*id="message-text"[^>]*>', form_content)
        self.assertIsNotNone(textarea_match)
        self.assertIn('dir="auto"', textarea_match.group(0))

        # Check CSS: textarea is static (no floating/absolute overlays covering it)
        _, _, css_body = self._http_get("/style.css")
        css = css_body.decode("utf-8")
        input_css = _extract_css_block(css, ".message-input")
        self.assertIn("position: static", input_css)
        self.assertIn("flex: 1 1 auto", input_css)

    def test_staging_preview_strip_structure_and_behavior(self):
        """Verify staging preview strip card layout, thumbnail rendering, and removal controls."""
        _, _, html_body = self._http_get("/")
        html = html_body.decode("utf-8")

        # Staging tray HTML elements
        self.assertIn('id="staged-tray"', html)
        self.assertIn('id="staged-count"', html)
        self.assertIn('id="clear-staged-btn"', html)
        self.assertIn('id="staged-items"', html)

        # Must have class 'hidden' by default
        tray_match = re.search(r'<div[^>]*id="staged-tray"[^>]*>', html)
        self.assertIsNotNone(tray_match)
        self.assertIn("hidden", tray_match.group(0))

        # Staging tray CSS styles
        _, _, css_body = self._http_get("/style.css")
        css = css_body.decode("utf-8")
        tray_css = _extract_css_block(css, ".staged-tray")
        self.assertIn("flex: 0 0 auto", tray_css)
        self.assertIn("border-bottom: 1px solid", tray_css)

        items_css = _extract_css_block(css, ".staged-items")
        self.assertIn("display: flex", items_css)
        self.assertIn("overflow-x: auto", items_css)

        item_css = _extract_css_block(css, ".staged-item {")
        self.assertIn("display: flex", item_css)
        self.assertIn("align-items: center", item_css)

        thumb_css = _extract_css_block(css, ".staged-thumb")
        self.assertIn("width: 24px", thumb_css)
        self.assertIn("height: 24px", thumb_css)
        self.assertIn("object-fit: cover", thumb_css)

        remove_css = _extract_css_block(css, ".staged-item .btn-remove")
        self.assertIn("cursor: pointer", remove_css)

        # JavaScript staging behavior assertions
        _, _, js_body = self._http_get("/app.js")
        js = js_body.decode("utf-8")
        self.assertIn("function stageFiles", js)
        self.assertIn("function unstageFile", js)
        self.assertIn("function renderStagedTray", js)
        self.assertIn("state.stagedFiles.splice(index, 1)", js)
        self.assertIn('elements.stagedTray.classList.remove("hidden")', js)
        self.assertIn('elements.stagedTray.classList.add("hidden")', js)

    def test_room_switcher_ui_navigation_and_active_state(self):
        """Verify room switcher UI pills, data-room attributes, and JS room switching behavior."""
        _, _, html_body = self._http_get("/")
        html = html_body.decode("utf-8")

        # Verify room buttons exist with proper data-room attributes
        self.assertIn('data-room="main"', html)
        self.assertIn('data-room="dm-argon"', html)
        self.assertIn('data-room="workers"', html)

        # Default active room is main
        main_btn_match = re.search(r'<button[^>]*data-room="main"[^>]*>', html)
        self.assertIsNotNone(main_btn_match)
        self.assertIn("active", main_btn_match.group(0))

        # CSS assertions for active room tab
        _, _, css_body = self._http_get("/style.css")
        css = css_body.decode("utf-8")
        active_btn_css = _extract_css_block(css, ".room-btn.active")
        self.assertIn("background-color: var(--accent-light)", active_btn_css)
        self.assertIn("color: var(--accent)", active_btn_css)

        # JavaScript room switcher logic
        _, _, js_body = self._http_get("/app.js")
        js = js_body.decode("utf-8")
        self.assertIn("function switchRoom(room)", js)
        self.assertIn("state.currentRoom = room", js)
        self.assertIn('btn.classList.add("active")', js)
        self.assertIn('btn.classList.remove("active")', js)
        self.assertIn("elements.currentRoomTitle.textContent = room", js)
        self.assertIn("elements.dropRoomTarget.textContent = room", js)

    def test_attachment_upload_and_preview_flow(self):
        """Verify file upload via /api/cluster-chat/upload, message delivery, and safe streaming."""
        test_file_content = b"Cluster chat web room attachment test content \xd8\xb3\xd9\x84\xd8\xa7\xd9\x85"
        test_filename = "cluster_spec.txt"

        status, _, body = self._http_post_multipart(
            "/api/cluster-chat/upload",
            fields={
                "room": "workers",
                "sender": "cookie",
                "text": "Attached test document for cluster mesh",
            },
            files=[
                ("file", test_filename, test_file_content, "text/plain"),
            ],
        )
        self.assertEqual(status, 201)
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("message", {}).get("room"), "workers")

        # Query messages in workers room
        status_msgs, _, msgs_body = self._http_get("/api/cluster-chat/rooms/workers/messages")
        self.assertEqual(status_msgs, 200)
        messages = json.loads(msgs_body.decode("utf-8")).get("messages", [])
        self.assertTrue(len(messages) > 0)
        latest = messages[-1]
        self.assertEqual(latest.get("sender"), "cookie")
        self.assertEqual(latest.get("text"), "Attached test document for cluster mesh")

        # Verify attachments metadata
        attachments = latest.get("attachments", [])
        self.assertEqual(len(attachments), 1)
        att = attachments[0]
        self.assertEqual(att.get("filename"), test_filename)
        self.assertEqual(att.get("size_bytes"), len(test_file_content))
        self.assertTrue(att.get("public_url"))

        # Verify safe download streaming of the uploaded file
        status_dl, headers_dl, dl_bytes = self._http_get(att["public_url"])
        self.assertEqual(status_dl, 200)
        self.assertEqual(dl_bytes, test_file_content)

    def test_room_switching_and_message_delivery_all_rooms(self):
        """Verify message posting, retrieval, and room isolation across all standard rooms."""
        rooms_to_test = ["main", "dm-argon", "workers"]
        for room in rooms_to_test:
            msg_text = f"Message test verification for room {room}"
            status, _, body = self._http_post_json(
                f"/api/cluster-chat/rooms/{room}/messages",
                {"sender": "mohammad", "text": msg_text},
            )
            self.assertEqual(status, 201)
            self.assertTrue(body.get("ok"))

            # Retrieve messages for this room
            _, _, room_resp = self._http_get(f"/api/cluster-chat/rooms/{room}/messages")
            msgs = json.loads(room_resp.decode("utf-8")).get("messages", [])
            self.assertTrue(any(msg_text in m.get("text", "") for m in msgs))

        # Check workers-room alias maps to workers messages
        _, _, alias_resp = self._http_get("/api/cluster-chat/rooms/workers-room/messages")
        alias_msgs = json.loads(alias_resp.decode("utf-8")).get("messages", [])
        self.assertTrue(any("Message test verification for room workers" in m.get("text", "") for m in alias_msgs))

    def test_app_js_null_guards_and_safety(self):
        """Verify client-side app.js contains safe null guards and auto-resize listeners."""
        _, _, js_body = self._http_get("/app.js")
        js = js_body.decode("utf-8")

        # Verify safe element guards
        self.assertIn("if (elements.messageText)", js)
        self.assertIn("if (elements.composerForm)", js)
        self.assertIn("if (elements.attachBtn", js)
        self.assertIn("if (elements.fileInput)", js)
        self.assertIn("if (elements.sendBtn)", js)
        self.assertTrue("elements.stagedTray" in js)

        # Verify textarea auto-resize handling
        self.assertIn("elements.messageText.style.height", js)
        self.assertIn("Math.min(elements.messageText.scrollHeight, 120)", js)


if __name__ == "__main__":
    unittest.main()
