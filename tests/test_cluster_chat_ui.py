"""tests/test_cluster_chat_ui.py

Automated unit and UI verification tests for Cluster-Chat Web Room UI redesign (P4-3).
Validates 4 core categories:
1. Maximized Viewport Layout (100dvh flex column shell, flex: 1 1 auto, min-height: 0, overflow-y: auto)
2. Compact & Hidden Agent Status (cards excluded from chat flow, default-hidden modal, 48px-56px header)
3. Clean Compose Bar & No Overlap (static textarea, flex row, zero chips/floating badges, staged tray above)
4. Preserved Core Features (room switching isolation, Persian RTL layout/logical CSS, multipart upload streaming)
"""

from html.parser import HTMLParser
import json
from pathlib import Path
import re
import tempfile
import unittest
import urllib.request

from cluster_chat.attachments import AttachmentStorageManager
from cluster_chat.rooms import RoomManager
from cluster_chat.server import ClusterChatServer


# --- CSS & DOM Helpers ---

def strip_css(c: str) -> str:
    """Strip CSS comments to avoid false-positive selector matches."""
    return re.sub(r'/\*[\s\S]*?\*/', '', c)


def extract_css(c: str, sel: str) -> str:
    """Extract CSS rule block matching exact selector boundary with balanced braces."""
    cl = strip_css(c)
    m = re.search(r'(?:^|[\s,{}])' + re.escape(sel.strip()) + r'(?:\s*\{|\s*,\s*)', cl)
    if not m:
        return ""
    o = cl.find("{", m.start())
    if o == -1:
        return ""
    d, i = 1, o + 1
    while i < len(cl) and d > 0:
        d += (1 if cl[i] == "{" else -1 if cl[i] == "}" else 0)
        i += 1
    return cl[o + 1 : i - 1]


def css_props(b: str) -> dict[str, str]:
    """Parse top-level CSS declarations from a block, ignoring nested at-rules or child blocks."""
    cl = strip_css(b)
    while "{" in cl:
        cl = re.sub(r'\{[^{}]*\}', '', cl)
    return {k.strip().lower(): v.strip().lower() for stmt in cl.split(";") if ":" in stmt for k, v in [stmt.split(":", 1)]}


class DOMNode:
    """In-memory DOM tree node for structural assertions."""

    def __init__(self, tag: str, attrs: list[tuple[str, str | None]], parent: "DOMNode | None" = None):
        self.tag = tag.lower()
        self.attrs = {k.lower(): (v or "") for k, v in attrs}
        self.children, self.parent = [], parent

    def find_all(self, tag: str | None = None, **kw) -> list["DOMNode"]:
        res = [self] if self._m(tag, kw) else []
        for c in self.children:
            res.extend(c.find_all(tag, **kw))
        return res

    def find(self, tag: str | None = None, **kw) -> "DOMNode | None":
        if self._m(tag, kw):
            return self
        for c in self.children:
            f = c.find(tag, **kw)
            if f:
                return f
        return None

    def _m(self, tag: str | None, kw: dict) -> bool:
        if tag and self.tag != tag.lower():
            return False
        for k, v in kw.items():
            if k == "id" and self.attrs.get("id") != v:
                return False
            if k == "class_name" and v not in self.attrs.get("class", "").split():
                return False
            if k.startswith("attr_") and self.attrs.get(k[5:].replace("_", "-")) != v:
                return False
        return True


class SimpleHTMLTreeParser(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__()
        self.root = DOMNode("document", [])
        self.cur = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        n = DOMNode(tag, attrs, parent=self.cur)
        self.cur.children.append(n)
        if tag.lower() not in self.VOID:
            self.cur = n

    def handle_endtag(self, tag: str):
        if tag.lower() in self.VOID:
            return
        c = self.cur
        while c is not self.root:
            if c.tag == tag.lower():
                self.cur = c.parent or self.root
                break
            c = c.parent or self.root


def parse_html(s: str) -> DOMNode:
    p = SimpleHTMLTreeParser()
    p.feed(s)
    return p.root


def build_multipart(fields: dict[str, str], files: list[tuple[str, str, bytes, str]]) -> tuple[bytes, str]:
    boundary = "----ClusterChatUIBoundaryZZ"
    body = bytearray()
    for k, v in fields.items():
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8"))
    for fn, name, data, mime in files:
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{fn}\"; filename=\"{name}\"\r\nContent-Type: {mime}\r\n\r\n".encode("latin-1") + data + b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("latin-1"))
    return bytes(body), f"multipart/form-data; boundary={boundary}"


# --- Base Test Server Fixture ---

class BaseUITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        p = Path(cls.tmpdir.name)
        cls.storage = AttachmentStorageManager(upload_dir=p / "uploads", max_upload_mb=5)
        cls.room_mgr = RoomManager(data_dir=p / "data")
        cls.server = ClusterChatServer(host="127.0.0.1", port=0, storage=cls.storage, room_manager=cls.room_mgr)
        cls.server.start(daemon=True)
        cls.base_url = cls.server.get_url()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        cls.tmpdir.cleanup()

    def get(self, path: str) -> tuple[int, dict[str, str], bytes]:
        req = urllib.request.Request(f"{self.base_url}{path}", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()

    def post_json(self, path: str, payload: dict) -> tuple[int, dict]:
        req = urllib.request.Request(f"{self.base_url}{path}", data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def post_mp(self, path: str, fields: dict, files: list) -> tuple[int, dict]:
        data, ctype = build_multipart(fields, files)
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, headers={"Content-Type": ctype}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))


# --- Category 1: Maximized Viewport Layout ---

class TestMaximizedViewportLayout(BaseUITest):
    """Verify 100dvh flex column shell and flex: 1 1 auto scrollable chat container."""

    def test_root_shell_100dvh_and_flex_column_css(self):
        _, _, body = self.get("/style.css")
        css = body.decode("utf-8")
        html_p = css_props(extract_css(css, "html, body"))
        self.assertEqual(html_p.get("height"), "100%")
        self.assertEqual(html_p.get("overflow"), "hidden")

        app_block = extract_css(css, ".chat-app")
        app_p = css_props(app_block)
        self.assertEqual(app_p.get("display"), "flex")
        self.assertEqual(app_p.get("flex-direction"), "column")
        self.assertEqual(app_p.get("overflow"), "hidden")
        self.assertIn("100vh", app_block)
        self.assertIn("100dvh", app_block)

    def test_chat_messages_viewport_flex_maximization_css(self):
        _, _, body = self.get("/style.css")
        css = body.decode("utf-8")
        ws_p = css_props(extract_css(css, ".chat-workspace"))
        self.assertEqual(ws_p.get("flex"), "1 1 auto")
        self.assertEqual(ws_p.get("min-height"), "0")

        chat_p = css_props(extract_css(css, ".chat-container"))
        self.assertEqual(chat_p.get("flex"), "1 1 auto")
        self.assertEqual(chat_p.get("min-height"), "0")
        self.assertEqual(chat_p.get("overflow-y"), "auto")

    def test_mobile_media_query_viewport_and_flex_maximization(self):
        _, _, body = self.get("/style.css")
        css = body.decode("utf-8")
        media = extract_css(css, "@media (max-width: 768px)")
        self.assertTrue(media)
        self.assertIn("100dvh", extract_css(media, ".chat-app"))
        chat_p = css_props(extract_css(media, ".chat-container"))
        self.assertEqual(chat_p.get("flex"), "1 1 auto")
        self.assertEqual(chat_p.get("min-height"), "0")

    def test_dom_hierarchy_viewport_structure(self):
        _, _, body = self.get("/")
        root = parse_html(body.decode("utf-8"))
        app = root.find("div", id="chat-app")
        self.assertIsNotNone(app)
        child_tags = [c.tag for c in app.children]
        self.assertIn("header", child_tags)
        self.assertIn("main", child_tags)
        main = root.find("main", class_name="chat-workspace")
        self.assertIsNotNone(main.find("div", id="chat-container"))
        self.assertIsNotNone(main.find("footer", class_name="composer-container"))


# --- Category 2: Compact & Hidden Agent Status ---

class TestCompactAndHiddenAgentStatus(BaseUITest):
    """Verify agent cards are outside visible stream, hidden modal, and compact header."""

    def test_bulky_agent_cards_not_in_visible_chat_flow(self):
        _, _, body = self.get("/")
        root = parse_html(body.decode("utf-8"))
        main = root.find("main")
        self.assertEqual(len(main.find_all("div", class_name="agent-card")), 0)
        self.assertEqual(len(main.find_all("div", class_name="agent-cards-grid")), 0)

    def test_agent_status_modal_hidden_by_default(self):
        _, _, html_body = self.get("/")
        root = parse_html(html_body.decode("utf-8"))
        modal = root.find("div", id="agent-status-modal")
        self.assertIsNotNone(modal)
        self.assertIn("hidden", modal.attrs.get("class", "").split())
        self.assertEqual(modal.attrs.get("role"), "dialog")
        self.assertIsNotNone(modal.find("div", id="agent-modal-backdrop"))
        self.assertIsNotNone(modal.find("button", id="agent-modal-close"))

        _, _, css_body = self.get("/style.css")
        self.assertIn("display: none !important", extract_css(css_body.decode("utf-8"), ".hidden"))

    def test_compact_header_and_agent_status_toggle_trigger(self):
        _, _, html_body = self.get("/")
        root = parse_html(html_body.decode("utf-8"))
        hdr = root.find("header", class_name="chat-header")
        toggle = hdr.find("button", id="agent-status-toggle")
        self.assertIsNotNone(toggle)
        self.assertEqual(toggle.attrs.get("aria-controls"), "agent-status-modal")
        self.assertEqual(toggle.attrs.get("aria-expanded"), "false")

        _, _, css_body = self.get("/style.css")
        hdr_p = css_props(extract_css(css_body.decode("utf-8"), ".chat-header"))
        self.assertEqual(hdr_p.get("flex"), "0 0 auto")
        self.assertIn(hdr_p.get("min-height"), ["48px", "52px", "56px"])

    def test_client_js_agent_status_modal_toggle_logic(self):
        _, _, body = self.get("/app.js")
        js = body.decode("utf-8")
        self.assertIn("function toggleAgentStatusModal", js)
        self.assertIn('elements.agentStatusModal.classList.remove("hidden")', js)
        self.assertIn('elements.agentStatusModal.classList.add("hidden")', js)
        self.assertIn('elements.agentStatusToggle.setAttribute("aria-expanded", "true")', js)
        self.assertIn('elements.agentStatusToggle.setAttribute("aria-expanded", "false")', js)

    def test_agents_api_schema_contract_dynamic(self):
        status, _, body = self.get("/api/cluster-chat/agents")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        agents = data.get("agents")
        self.assertIsInstance(agents, list)
        self.assertEqual(data.get("count"), len(agents))
        for a in agents:
            self.assertIn("name", a)
            self.assertIn("role", a)
            self.assertIn("status", a)
            self.assertTrue(a["name"] and a["status"])


# --- Category 3: Clean Compose Bar & No Overlap ---

class TestCleanComposeBarNoOverlap(BaseUITest):
    """Verify clean compose bar without overlapping chips, static textarea, and staging strip."""

    def test_compose_bar_css_flex_row_and_static_textarea(self):
        _, _, body = self.get("/style.css")
        css = body.decode("utf-8")
        c_p = css_props(extract_css(css, ".composer-container"))
        self.assertEqual(c_p.get("flex"), "0 0 auto")
        self.assertEqual(c_p.get("width"), "100%")

        f_p = css_props(extract_css(css, ".composer-form"))
        self.assertEqual(f_p.get("display"), "flex")
        self.assertEqual(f_p.get("align-items"), "center")
        self.assertEqual(f_p.get("gap"), "8px")

        in_p = css_props(extract_css(css, ".message-input"))
        self.assertEqual(in_p.get("position"), "static")
        self.assertEqual(in_p.get("flex"), "1 1 auto")
        self.assertEqual(in_p.get("min-width"), "0")

    def test_compose_bar_dom_strict_elements_and_no_chips(self):
        _, _, body = self.get("/")
        root = parse_html(body.decode("utf-8"))
        form = root.find("form", id="composer-form")
        self.assertIsNotNone(form)
        child_ids = [c.attrs.get("id") for c in form.children if c.attrs.get("id")]
        self.assertEqual(child_ids, ["attach-btn", "message-text", "send-btn"])

        for n in form.find_all():
            cls, id_ = n.attrs.get("class", "").lower(), n.attrs.get("id", "").lower()
            self.assertNotIn("chip", cls)
            self.assertNotIn("preset", cls)
            self.assertNotIn("quick", cls)
            self.assertNotIn("chip", id_)

        txt = form.find("textarea", id="message-text")
        self.assertIsNotNone(txt)
        self.assertEqual(txt.attrs.get("dir"), "auto")

    def test_staging_preview_strip_cleanly_above_input_row(self):
        _, _, body = self.get("/")
        root = parse_html(body.decode("utf-8"))
        footer = root.find("footer", class_name="composer-container")
        tray = footer.find("div", id="staged-tray")
        self.assertIsNotNone(tray)
        self.assertIn("hidden", tray.attrs.get("class", "").split())
        form = footer.find("form", id="composer-form")
        self.assertLess(footer.children.index(tray), footer.children.index(form))

        fin = footer.find("input", id="file-input")
        self.assertIsNotNone(fin)
        self.assertEqual(fin.attrs.get("type"), "file")
        self.assertIn("hidden", fin.attrs.get("class", "").split())

    def test_composer_javascript_null_guards_and_autoresize(self):
        _, _, body = self.get("/app.js")
        js = body.decode("utf-8")
        self.assertIn("if (elements.messageText)", js)
        self.assertIn("if (elements.composerForm)", js)
        self.assertIn("if (elements.attachBtn && elements.fileInput)", js)
        self.assertIn("if (elements.sendBtn)", js)
        self.assertIn("Math.min(elements.messageText.scrollHeight, 120)", js)


# --- Category 4: Preserved Core Features ---

class TestPreservedCoreFeatures(BaseUITest):
    """Verify room switching isolation, Persian RTL layout, and upload streaming."""

    def test_persian_rtl_layout_preserved(self):
        _, _, html_body = self.get("/")
        root = parse_html(html_body.decode("utf-8"))
        html = root.find("html")
        self.assertEqual(html.attrs.get("lang"), "fa")
        self.assertEqual(html.attrs.get("dir"), "rtl")

        _, _, css_body = self.get("/style.css")
        css = css_body.decode("utf-8")
        rtl_p = css_props(extract_css(css, 'html[dir="rtl"]'))
        self.assertEqual(rtl_p.get("direction"), "rtl")
        self.assertEqual(rtl_p.get("text-align"), "right")
        self.assertIn("margin-inline-start", css)
        self.assertIn("border-inline-start", css)

    def test_room_switching_dom_and_ui_wiring(self):
        _, _, body = self.get("/")
        root = parse_html(body.decode("utf-8"))
        nav = root.find("nav", id="room-list")
        self.assertIsNotNone(nav)
        rooms = [b.attrs.get("data-room") for b in nav.find_all("button", class_name="room-btn")]
        for r in ["main", "dm-argon", "workers"]:
            self.assertIn(r, rooms)
        w_btn = nav.find("button", attr_data_room="workers")
        self.assertEqual(w_btn.attrs.get("data-room-alt"), "workers-room")
        m_btn = nav.find("button", attr_data_room="main")
        self.assertIn("active", m_btn.attrs.get("class", "").split())

    def test_bidirectional_room_isolation_live(self):
        st1, _ = self.post_json("/api/cluster-chat/rooms/main/messages", {"sender": "u", "text": "IsoMsgMain991"})
        self.assertEqual(st1, 201)
        st2, _ = self.post_json("/api/cluster-chat/rooms/dm-argon/messages", {"sender": "u", "text": "IsoMsgArgon992"})
        self.assertEqual(st2, 201)
        st3, _ = self.post_json("/api/cluster-chat/rooms/workers/messages", {"sender": "u", "text": "IsoMsgWorkers993"})
        self.assertEqual(st3, 201)

        _, _, r1 = self.get("/api/cluster-chat/rooms/main/messages")
        m1 = [m["text"] for m in json.loads(r1.decode("utf-8"))["messages"]]
        self.assertTrue(any("IsoMsgMain991" in t for t in m1))
        self.assertFalse(any("IsoMsgArgon992" in t for t in m1))
        self.assertFalse(any("IsoMsgWorkers993" in t for t in m1))

        _, _, r2 = self.get("/api/cluster-chat/rooms/dm-argon/messages")
        m2 = [m["text"] for m in json.loads(r2.decode("utf-8"))["messages"]]
        self.assertTrue(any("IsoMsgArgon992" in t for t in m2))
        self.assertFalse(any("IsoMsgMain991" in t for t in m2))
        self.assertFalse(any("IsoMsgWorkers993" in t for t in m2))

        _, _, r3 = self.get("/api/cluster-chat/rooms/workers/messages")
        m3 = [m["text"] for m in json.loads(r3.decode("utf-8"))["messages"]]
        self.assertTrue(any("IsoMsgWorkers993" in t for t in m3))
        self.assertFalse(any("IsoMsgMain991" in t for t in m3))
        self.assertFalse(any("IsoMsgArgon992" in t for t in m3))

    def test_file_and_image_multipart_upload_live(self):
        png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        st_i, b_i = self.post_mp(
            "/api/cluster-chat/upload",
            fields={"room": "dm-argon", "sender": "m", "text": "Screenshot"},
            files=[("file", "snapshot.png", png_bytes, "image/png")],
        )
        self.assertEqual(st_i, 201)
        att_i = b_i["message"]["attachments"][0]
        self.assertEqual(att_i["filename"], "snapshot.png")
        self.assertEqual(att_i["kind"], "photo")
        st_dl, hd_dl, dl_bytes = self.get(att_i["public_url"])
        self.assertEqual(st_dl, 200)
        self.assertEqual(dl_bytes, png_bytes)
        self.assertIn("image/png", hd_dl.get("content-type", ""))

        doc_bytes = "Persian doc: تست بارگذاری سند".encode("utf-8")
        st_d, b_d = self.post_mp(
            "/api/cluster-chat/upload",
            fields={"room": "workers", "sender": "c", "text": "Document"},
            files=[("file", "manual.txt", doc_bytes, "text/plain; charset=utf-8")],
        )
        self.assertEqual(st_d, 201)
        att_d = b_d["message"]["attachments"][0]
        self.assertEqual(att_d["filename"], "manual.txt")
        self.assertEqual(att_d["kind"], "document")
        st_dl2, _, dl_bytes2 = self.get(att_d["public_url"])
        self.assertEqual(st_dl2, 200)
        self.assertEqual(dl_bytes2, doc_bytes)


if __name__ == "__main__":
    unittest.main()
