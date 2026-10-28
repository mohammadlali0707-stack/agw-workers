"""tests/test_cluster_chat_web_ui.py

Automated tests for Cluster Chat Web UI:
- Relative URLs everywhere & zero runtime dependencies on airboxvip.top
- Dynamic WebSocket/SSE origin derivation from window.location
- Resilient Connection Manager with automatic 4-second timeout & HTTP polling fallback (?since=)
- Client-side message deduplication via Set of renderedMessageIds
- Plain HTTP direct-IP compatibility (document.execCommand clipboard fallback on insecure context)
- Legacy attachment URL and message body link rewriting into relative paths
"""

import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import urllib.request

from cluster_chat.attachments import AttachmentStorageManager
from cluster_chat.rooms import RoomManager
from cluster_chat.server import ClusterChatServer


class TestClusterChatWebUI(unittest.TestCase):
    """Test suite validating Web UI frontend relative paths, resilient fallback, and clipboard."""

    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parent.parent
        cls.web_dir = cls.repo_root / "cluster_chat" / "web"
        cls.app_js_path = cls.web_dir / "app.js"
        cls.index_html_path = cls.web_dir / "index.html"
        cls.style_css_path = cls.web_dir / "style.css"

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmpdir.name)
        self.upload_dir = self.tmp_path / "uploads"
        self.data_dir = self.tmp_path / "data"

        self.storage = AttachmentStorageManager(upload_dir=self.upload_dir)
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

    def test_static_files_exist_and_served(self):
        """Web UI static assets must exist and be served properly over HTTP."""
        self.assertTrue(self.index_html_path.is_file(), "index.html missing")
        self.assertTrue(self.app_js_path.is_file(), "app.js missing")
        self.assertTrue(self.style_css_path.is_file(), "style.css missing")

        # 1. GET /
        req = urllib.request.Request(f"{self.base_url}/")
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/html", resp.headers.get("Content-Type", ""))
            body = resp.read().decode("utf-8")
            self.assertIn("Cluster Chat", body)
            self.assertIn('id="connection-status"', body)
            self.assertIn('id="header-status-badge"', body)
            self.assertIn('id="status-indicator"', body)
            self.assertIn('id="poll-interval"', body)

        # 2. GET /app.js
        req_js = urllib.request.Request(f"{self.base_url}/app.js")
        with urllib.request.urlopen(req_js, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("javascript", resp.headers.get("Content-Type", ""))
            body_js = resp.read().decode("utf-8")
            self.assertIn("ResilientConnectionManager", body_js)
            self.assertIn("normalizeAttachmentUrl", body_js)
            self.assertIn("ClusterChatUI", body_js)

        # 3. GET /style.css
        req_css = urllib.request.Request(f"{self.base_url}/style.css")
        with urllib.request.urlopen(req_css, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/css", resp.headers.get("Content-Type", ""))
            body_css = resp.read().decode("utf-8")
            self.assertIn(".connection-badge", body_css)
            self.assertIn(".status-indicator.polling", body_css)

    def test_zero_runtime_destinations_to_airboxvip_in_web(self):
        """Frontend files must have zero runtime destination URLs pointing to airboxvip.top."""
        app_js = self.app_js_path.read_text(encoding="utf-8")
        index_html = self.index_html_path.read_text(encoding="utf-8")
        style_css = self.style_css_path.read_text(encoding="utf-8")

        # HTML and CSS should contain zero occurrences of airboxvip
        self.assertNotIn("airboxvip", index_html.lower())
        self.assertNotIn("airboxvip", style_css.lower())

        # In app.js, ensure all network endpoints (fetch, WebSocket, EventSource) use relative/dynamic
        # Search for any active fetch("http...airboxvip") or wss://airboxvip
        active_airboxvip_calls = re.findall(r'(?:fetch|WebSocket|EventSource)\s*\(\s*["\']https?://[^"\']*airboxvip', app_js)
        self.assertEqual(active_airboxvip_calls, [], f"Found active network call to airboxvip: {active_airboxvip_calls}")

        # WebSocket must be dynamically constructed from window.location
        self.assertIn("window.location.protocol === \"https:\" ? \"wss:\" : \"ws:\"", app_js)
        self.assertIn("window.location.host", app_js)

    def test_node_legacy_attachment_url_normalization(self):
        """Node evaluation of normalizeAttachmentUrl and normalizeMessageText."""
        node_script = """
        const fs = require('fs');
        const vm = require('vm');
        const code = fs.readFileSync(process.argv[1], 'utf8');

        const mockDocument = {
          getElementById: () => ({ classList: { add: ()=>{}, remove: ()=>{} }, addEventListener: ()=>{}, innerHTML: '', textContent: '' }),
          querySelectorAll: () => [],
          addEventListener: () => {},
          createElement: () => ({ dataset: {}, classList: { add: ()=>{}, remove: ()=>{} }, innerHTML: '', setAttribute: ()=>{} }),
          body: { appendChild: ()=>{}, removeChild: ()=>{} }
        };
        const mockWindow = {
          location: { protocol: 'http:', host: '203.0.113.10:8080' },
          addEventListener: () => {},
          document: mockDocument,
          isSecureContext: false
        };
        const context = {
          window: mockWindow,
          document: mockDocument,
          navigator: {},
          console: console,
          setTimeout: () => 1,
          clearTimeout: () => {},
          setInterval: () => 1,
          clearInterval: () => {},
          fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
          FormData: class {},
          URL: URL,
          URLSearchParams: URLSearchParams
        };
        vm.createContext(context);
        vm.runInContext(code, context);

        const UI = context.window.ClusterChatUI;

        const testCases = [
          ['https://airboxvip.top/api/cluster-chat/files/room/2026-10-28/p.png', '/api/cluster-chat/files/room/2026-10-28/p.png'],
          ['https://airboxvip.top/uploads/room/2026-10-28/d.pdf', '/uploads/room/2026-10-28/d.pdf'],
          ['http://status.airboxvip.top:8080/files/f.txt', '/files/f.txt'],
          ['//airboxvip.top/api/attachments/att_1', '/api/attachments/att_1'],
          ['/api/cluster-chat/files/room/p.png', '/api/cluster-chat/files/room/p.png'],
          ['http://203.0.113.10:8080/api/cluster-chat/files/p.png', '/api/cluster-chat/files/p.png'],
          ['https://airboxvip.top', '/']
        ];

        for (const [input, expected] of testCases) {
          const actual = UI.normalizeAttachmentUrl(input);
          if (actual !== expected) {
            console.error(`Mismatch for input ${input}: expected ${expected}, got ${actual}`);
            process.exit(1);
          }
        }

        const textCase = UI.normalizeMessageText('See https://airboxvip.top/uploads/guide.pdf for docs');
        if (textCase !== 'See /uploads/guide.pdf for docs') {
          console.error(`Message text normalization failed: got ${textCase}`);
          process.exit(1);
        }

        console.log('SUCCESS');
        process.exit(0);
        """

        res = subprocess.run(
            ["node", "-e", node_script, str(self.app_js_path)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"Node script error: {res.stderr}\n{res.stdout}")
        self.assertIn("SUCCESS", res.stdout)

    def test_node_rendered_message_zero_airboxvip_and_relative_dom(self):
        """Node evaluation: rendering older message containing airboxvip.top yields zero airboxvip in DOM."""
        node_script = """
        const fs = require('fs');
        const vm = require('vm');
        const code = fs.readFileSync(process.argv[1], 'utf8');

        const mockDocument = {
          getElementById: () => ({ classList: { add: ()=>{}, remove: ()=>{} }, addEventListener: ()=>{}, innerHTML: '', textContent: '' }),
          querySelectorAll: () => [],
          addEventListener: () => {},
          createElement: (tag) => ({
            tagName: tag,
            dataset: {},
            classList: { add: ()=>{}, remove: ()=>{} },
            innerHTML: '',
            setAttribute: ()=>{}
          }),
          body: { appendChild: ()=>{}, removeChild: ()=>{} }
        };
        const mockWindow = {
          location: { protocol: 'http:', host: '203.0.113.10:8080' },
          addEventListener: () => {},
          document: mockDocument,
          isSecureContext: false
        };
        const context = {
          window: mockWindow,
          document: mockDocument,
          navigator: {},
          console: console,
          setTimeout: () => 1,
          clearTimeout: () => {},
          setInterval: () => 1,
          clearInterval: () => {},
          fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
          FormData: class {},
          URL: URL,
          URLSearchParams: URLSearchParams
        };
        vm.createContext(context);
        vm.runInContext(code, context);

        const UI = context.window.ClusterChatUI;
        const msg = {
          message_id: 'msg_test_legacy',
          room: 'main',
          sender: 'mohammad',
          text: 'Check attachment https://airboxvip.top/uploads/report.pdf please',
          attachments: [
            {
              attachment_id: 'att_photo_1',
              kind: 'photo',
              filename: 'screen.png',
              public_url: 'https://airboxvip.top/api/cluster-chat/files/main/2026-10-28/screen.png'
            },
            {
              attachment_id: 'att_doc_1',
              kind: 'document',
              filename: 'report.pdf',
              local_path: '/tmp/uploads/report.pdf',
              public_url: 'https://airboxvip.top/uploads/main/2026-10-28/report.pdf'
            }
          ]
        };

        const elem = UI.createMessageElement(msg);
        const html = elem.innerHTML;

        if (html.includes('airboxvip.top')) {
          console.error('DOM contains hardcoded airboxvip.top:\\n' + html);
          process.exit(1);
        }

        if (!html.includes('/api/cluster-chat/files/main/2026-10-28/screen.png')) {
          console.error('Missing normalized photo path');
          process.exit(1);
        }

        if (!html.includes('/uploads/main/2026-10-28/report.pdf')) {
          console.error('Missing normalized document path');
          process.exit(1);
        }

        console.log('SUCCESS');
        process.exit(0);
        """

        res = subprocess.run(
            ["node", "-e", node_script, str(self.app_js_path)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"Node script error: {res.stderr}\n{res.stdout}")
        self.assertIn("SUCCESS", res.stdout)

    def test_node_message_deduplication(self):
        """Node evaluation: appendNewMessages drops duplicate message IDs using client-side Set."""
        node_script = """
        const fs = require('fs');
        const vm = require('vm');
        const code = fs.readFileSync(process.argv[1], 'utf8');

        let appended = [];
        const mockList = {
          innerHTML: '',
          appendChild: (el) => appended.push(el),
          querySelector: () => null
        };
        const mockDocument = {
          getElementById: (id) => {
            if (id === 'message-list') return mockList;
            return { id, classList: { add: ()=>{}, remove: ()=>{} }, addEventListener: ()=>{}, innerHTML: '', textContent: '' };
          },
          querySelectorAll: () => [],
          addEventListener: () => {},
          createElement: () => ({ dataset: {}, classList: { add: ()=>{}, remove: ()=>{} }, innerHTML: '' }),
          body: { appendChild: ()=>{}, removeChild: ()=>{} }
        };
        const mockWindow = {
          location: { protocol: 'http:', host: '203.0.113.10:8080' },
          addEventListener: () => {},
          document: mockDocument,
          isSecureContext: false
        };
        const context = {
          window: mockWindow,
          document: mockDocument,
          navigator: {},
          console: console,
          setTimeout: () => 1,
          clearTimeout: () => {},
          setInterval: () => 1,
          clearInterval: () => {},
          fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
          FormData: class {},
          URL: URL,
          URLSearchParams: URLSearchParams
        };
        vm.createContext(context);
        vm.runInContext(code, context);

        const UI = context.window.ClusterChatUI;
        const msg1 = { message_id: 'm1', text: '1' };
        const msg2 = { message_id: 'm2', text: '2' };
        const msg3 = { message_id: 'm3', text: '3' };

        UI.appendNewMessages([msg1, msg2]);
        if (appended.length !== 2) {
          console.error(`Initial count failed: got ${appended.length}`);
          process.exit(1);
        }

        // Re-append m1 and m2 with new m3
        UI.appendNewMessages([msg1, msg2, msg3]);
        if (appended.length !== 3) {
          console.error(`Deduplication failed: expected 3, got ${appended.length}`);
          process.exit(1);
        }

        console.log('SUCCESS');
        process.exit(0);
        """

        res = subprocess.run(
            ["node", "-e", node_script, str(self.app_js_path)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"Node script error: {res.stderr}\n{res.stdout}")
        self.assertIn("SUCCESS", res.stdout)

    def test_node_clipboard_fallback_insecure_context(self):
        """Node evaluation: plain HTTP direct IP uses document.execCommand('copy') textarea fallback."""
        node_script = """
        const fs = require('fs');
        const vm = require('vm');
        const code = fs.readFileSync(process.argv[1], 'utf8');

        let textareaCreated = null;
        let execCommands = [];

        const mockDocument = {
          getElementById: () => ({ classList: { add: ()=>{}, remove: ()=>{} }, addEventListener: ()=>{}, innerHTML: '', textContent: '' }),
          querySelectorAll: () => [],
          addEventListener: () => {},
          createElement: (tag) => {
            const el = {
              tagName: tag,
              value: '',
              style: {},
              setAttribute: () => {},
              select: () => { el.selected = true; },
              focus: () => { el.focused = true; }
            };
            if (tag === 'textarea') textareaCreated = el;
            return el;
          },
          body: {
            appendChild: (el) => { el.appended = true; },
            removeChild: (el) => { el.removed = true; }
          },
          execCommand: (cmd) => {
            execCommands.push(cmd);
            return true;
          }
        };

        const mockWindow = {
          location: { protocol: 'http:', host: '203.0.113.10:8080' },
          addEventListener: () => {},
          document: mockDocument,
          isSecureContext: false // Plain HTTP
        };

        const context = {
          window: mockWindow,
          document: mockDocument,
          navigator: {},
          console: console,
          setTimeout: () => 1,
          clearTimeout: () => {},
          setInterval: () => 1,
          clearInterval: () => {},
          fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
          FormData: class {},
          URL: URL,
          URLSearchParams: URLSearchParams
        };

        vm.createContext(context);
        vm.runInContext(code, context);

        (async () => {
          const UI = context.window.ClusterChatUI;
          const ok = await UI.copyToClipboard('/path/to/copied/file.txt');
          if (!ok) {
            console.error('copyToClipboard returned false');
            process.exit(1);
          }
          if (!textareaCreated || textareaCreated.value !== '/path/to/copied/file.txt') {
            console.error('Textarea fallback did not populate correct value');
            process.exit(1);
          }
          if (!textareaCreated.selected) {
            console.error('Textarea fallback did not select text');
            process.exit(1);
          }
          if (execCommands.length !== 1 || execCommands[0] !== 'copy') {
            console.error('document.execCommand(copy) was not called: ' + JSON.stringify(execCommands));
            process.exit(1);
          }
          console.log('SUCCESS');
          process.exit(0);
        })();
        """

        res = subprocess.run(
            ["node", "-e", node_script, str(self.app_js_path)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"Node script error: {res.stderr}\n{res.stdout}")
        self.assertIn("SUCCESS", res.stdout)

    def test_node_connection_manager_resilience_and_timeout(self):
        """Node evaluation: connection manager falls back to polling on 4s timeout or realtime error."""
        node_script = """
        const fs = require('fs');
        const vm = require('vm');
        const code = fs.readFileSync(process.argv[1], 'utf8');

        let statusKeyReported = null;
        let labelReported = null;
        let timeoutTriggered = false;

        const mockDocument = {
          getElementById: (id) => ({
            id,
            classList: { add: ()=>{}, remove: ()=>{} },
            addEventListener: ()=>{},
            innerHTML: '',
            textContent: ''
          }),
          querySelectorAll: () => [],
          addEventListener: () => {},
          createElement: () => ({ dataset: {}, classList: { add: ()=>{}, remove: ()=>{} }, innerHTML: '', setAttribute: ()=>{} }),
          body: { appendChild: ()=>{}, removeChild: ()=>{} }
        };

        const mockWindow = {
          location: { protocol: 'http:', host: '203.0.113.10:8080' },
          addEventListener: () => {},
          document: mockDocument,
          isSecureContext: false
        };

        let scheduledTimeouts = [];
        let createdWebSockets = [];

        class MockWebSocket {
          constructor(url) {
            this.url = url;
            createdWebSockets.push(this);
          }
        }

        const context = {
          window: mockWindow,
          document: mockDocument,
          WebSocket: MockWebSocket,
          navigator: {},
          console: console,
          setTimeout: (fn, delay) => {
            const id = scheduledTimeouts.length + 1;
            scheduledTimeouts.push({ id, fn, delay });
            return id;
          },
          clearTimeout: () => {},
          setInterval: () => 1,
          clearInterval: () => {},
          fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, messages: [] }) }),
          FormData: class {},
          URL: URL,
          URLSearchParams: URLSearchParams
        };

        vm.createContext(context);
        vm.runInContext(code, context);

        const UI = context.window.ClusterChatUI;
        const cm = UI.getConnectionManager();

        // 1. Initial mode should be connecting during realtime attempt
        cm.attemptRealtimeTransport();
        if (cm.mode !== 'CONNECTING') {
          console.error('Expected mode CONNECTING during realtime attempt, got: ' + cm.mode);
          process.exit(1);
        }

        // Verify WebSocket URL was constructed dynamically
        if (createdWebSockets.length === 0 || createdWebSockets[0].url !== 'ws://203.0.113.10:8080/ws') {
          console.error('WebSocket URL was not constructed dynamically: ' + (createdWebSockets[0] ? createdWebSockets[0].url : 'none'));
          process.exit(1);
        }

        // 2. Find the 4000ms fallback timeout
        const fallbackTimer = scheduledTimeouts.find(t => t.delay === 4000);
        if (!fallbackTimer) {
          console.error('No 4000ms realtime fallback timeout was registered');
          process.exit(1);
        }

        // 3. Trigger timeout to test polling fallback activation
        fallbackTimer.fn();
        if (cm.mode !== 'POLLING') {
          console.error('Expected mode POLLING after 4s timeout, got: ' + cm.mode);
          process.exit(1);
        }

        if (cm.basePollIntervalMs !== 2500) {
          console.error('Expected basePollIntervalMs to be 2500, got: ' + cm.basePollIntervalMs);
          process.exit(1);
        }

        console.log('SUCCESS');
        process.exit(0);
        """

        res = subprocess.run(
            ["node", "-e", node_script, str(self.app_js_path)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"Node script error: {res.stderr}\n{res.stdout}")
        self.assertIn("SUCCESS", res.stdout)


if __name__ == "__main__":
    unittest.main()
