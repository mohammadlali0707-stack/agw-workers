"""tests/test_web_relay_static_ui.py

Contract, structural, and cross-runtime integration tests for the static Web Relay client
(web_relay/index.html, styles.css, crypto.js, app.js).
Verifies file existence, room tabs, ETag header integration, WebCrypto AES-GCM calls,
workflow dispatch endpoints, and cross-language cryptographic compatibility via Node.js.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import unittest

import cluster_chat.relay_protocol as rp

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_RELAY_DIR = REPO_ROOT / "web_relay"


class TestWebRelayStaticUIContracts(unittest.TestCase):
    """Verify presence and contractual contents of static UI assets."""

    def test_required_files_exist(self):
        expected_files = ("index.html", "styles.css", "crypto.js", "app.js", "README.md")
        for filename in expected_files:
            target = WEB_RELAY_DIR / filename
            self.assertTrue(target.exists(), f"Missing required file: {target}")
            self.assertGreater(target.stat().st_size, 0, f"File {target} is empty")

    def test_index_html_contracts(self):
        html_text = (WEB_RELAY_DIR / "index.html").read_text(encoding="utf-8")

        # Persian RTL structure
        self.assertIn('dir="rtl"', html_text)
        self.assertIn('lang="fa"', html_text)

        # Room tabs for all 3 canonical rooms
        self.assertIn('data-room="main"', html_text)
        self.assertIn('data-room="dm-argon"', html_text)
        self.assertIn('data-room="workers"', html_text)

        # Critical UI elements & IDs
        self.assertIn('id="message-stream"', html_text)
        self.assertIn('id="message-input"', html_text)
        self.assertIn('id="send-btn"', html_text)
        self.assertIn('id="attach-btn"', html_text)
        self.assertIn('id="file-input"', html_text)
        self.assertIn('id="staged-tray"', html_text)
        self.assertIn('id="open-web-bridge-btn"', html_text)
        self.assertIn('id="status-pill"', html_text)
        self.assertIn('id="manual-poll-btn"', html_text)
        self.assertIn('id="open-settings-btn"', html_text)

        # Settings inputs
        self.assertIn('id="cfg-pat"', html_text)
        self.assertIn('id="cfg-passphrase"', html_text)
        self.assertIn('id="cfg-owner"', html_text)
        self.assertIn('id="cfg-repo"', html_text)
        self.assertIn('id="cfg-branch"', html_text)

        # Web Bridge modal
        self.assertIn('id="web-bridge-modal"', html_text)
        self.assertIn('id="bridge-url"', html_text)
        self.assertIn('id="bridge-mode"', html_text)
        self.assertIn('id="bridge-room"', html_text)

        # Script tags include crypto.js and app.js
        self.assertIn('src="crypto.js"', html_text)
        self.assertIn('src="app.js"', html_text)

    def test_styles_css_contracts(self):
        css_text = (WEB_RELAY_DIR / "styles.css").read_text(encoding="utf-8")

        # Color system variables
        self.assertIn("--bg-app", css_text)
        self.assertIn("--bg-surface", css_text)
        self.assertIn("--text-main", css_text)
        self.assertIn("--accent-primary", css_text)

        # Responsive media query for mobile (POCO Android)
        self.assertIn("@media", css_text)
        self.assertIn("max-width", css_text)

        # Room tab active state and message bubble styles
        self.assertIn(".room-tab", css_text)
        self.assertIn(".message-bubble", css_text)

    def test_app_js_contracts(self):
        js_text = (WEB_RELAY_DIR / "app.js").read_text(encoding="utf-8")

        # Room management
        self.assertIn("'main'", js_text)
        self.assertIn("'dm-argon'", js_text)
        self.assertIn("'workers'", js_text)

        # Conditional ETag caching (If-None-Match)
        self.assertIn("If-None-Match", js_text)
        self.assertIn("304", js_text)

        # Queue layout paths
        self.assertIn("relay_queue/inbox", js_text)
        self.assertIn("relay_queue/outbox", js_text)
        self.assertIn("snapshot_", js_text)
        self.assertIn("relay_queue/attachments", js_text)
        self.assertIn("relay_queue/web_requests", js_text)

        # Workflow dispatch endpoint
        self.assertIn("actions/workflows/", js_text)
        self.assertIn("dispatches", js_text)
        self.assertIn("dispatchWebBridge", js_text)

        # WebCrypto & Secret Scrubbing references
        self.assertIn("RelayCrypto", js_text)

    def test_crypto_js_contracts(self):
        crypto_text = (WEB_RELAY_DIR / "crypto.js").read_text(encoding="utf-8")

        # AES-GCM and PBKDF2 WebCrypto parameters
        self.assertIn("AES-GCM", crypto_text)
        self.assertIn("PBKDF2", crypto_text)
        self.assertIn("SHA-256", crypto_text)
        self.assertIn("100000", crypto_text)

        # Functions exported
        self.assertIn("encryptText", crypto_text)
        self.assertIn("decryptText", crypto_text)
        self.assertIn("computeMsgId", crypto_text)
        self.assertIn("scrubSecrets", crypto_text)
        self.assertIn("parseWebCommand", crypto_text)


class TestNodeCrossLanguageCrypto(unittest.TestCase):
    """Execute Node.js to verify bitwise crypto & protocol parity with Python."""

    @classmethod
    def setUpClass(cls):
        cls.has_node = shutil.which("node") is not None

    def setUp(self):
        if not self.has_node:
            self.skipTest("Node.js is not installed on this system")

    def test_node_msg_id_matches_python(self):
        # Compare compute_msg_id in Python with computeMsgId in Node.js
        test_cases = [
            ("main", "mohammad", "2026-10-28T08:00:00Z", "Hello world!", "nonce1"),
            ("dm-argon", "argon", "2026-10-28T08:05:00Z", "Persian message: سلام آرگون", "nonce2"),
            ("workers-room", "worker", "2026-10-28T08:10:00Z", "Task done", "nonce3"),
        ]

        node_script = """
        const RelayCrypto = require('./web_relay/crypto.js');
        async function run() {
            const cases = JSON.parse(process.argv[1]);
            const results = [];
            for (const c of cases) {
                const id = await RelayCrypto.computeMsgId(c[0], c[1], c[2], c[3], c[4]);
                results.push(id);
            }
            console.log(JSON.stringify(results));
        }
        run();
        """

        node_proc = subprocess.run(
            ["node", "-e", node_script, json.dumps(test_cases)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        node_results = json.loads(node_proc.stdout.strip())

        for idx, (room, sender, created_at, body, nonce) in enumerate(test_cases):
            py_id = rp.compute_msg_id(room, sender, created_at, body, nonce)
            self.assertEqual(node_results[idx], py_id, f"Mismatch at case {idx}: {room}")

    def test_node_scrub_secrets_matches_python(self):
        test_text = (
            "PAT1: ghp_1234567890abcdef1234567890ABCDEF\n"
            "PAT2: github_pat_11AAAAAAA0000000000000_12345678901234567890abcdefghijklmnopqrstuv\n"
            "Gemini: AIzaSyD1234567890abcdefghijklmnopqrstuv\n"
            "Telegram: 123456789:" + ("A" * 35) + "\n"
            "Normal text: سلام محمد!"
        )

        node_script = """
        const RelayCrypto = require('./web_relay/crypto.js');
        const text = process.argv[1];
        console.log(RelayCrypto.scrubSecrets(text));
        """

        node_proc = subprocess.run(
            ["node", "-e", node_script, test_text],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        node_scrubbed = node_proc.stdout.strip()
        py_scrubbed = rp.scrub_secrets(test_text).strip()

        self.assertNotIn("ghp_", node_scrubbed)
        self.assertNotIn("github_pat_", node_scrubbed)
        self.assertNotIn("AIzaSyD", node_scrubbed)
        self.assertNotIn("123456789:", node_scrubbed)
        self.assertIn("سلام محمد!", node_scrubbed)
        self.assertEqual(node_scrubbed, py_scrubbed)

    def test_node_encrypt_python_decrypt(self):
        passphrase = "Cross-Language-Key-2026!"
        plaintext = "Testing Node.js encryption to Python decryption roundtrip: 🚀 رمزنگاری موفق"

        node_script = """
        const RelayCrypto = require('./web_relay/crypto.js');
        async function run() {
            const pass = process.argv[1];
            const text = process.argv[2];
            const enc = await RelayCrypto.encryptText(text, pass);
            console.log(JSON.stringify(enc));
        }
        run();
        """

        node_proc = subprocess.run(
            ["node", "-e", node_script, passphrase, plaintext],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        enc_payload = json.loads(node_proc.stdout.strip())

        # Decrypt in Python
        decrypted_py = rp.decrypt_payload(enc_payload, passphrase)
        self.assertEqual(decrypted_py, plaintext)

    def test_python_encrypt_node_decrypt(self):
        passphrase = "Cross-Language-Key-2026!"
        plaintext = "Testing Python encryption to Node.js decryption roundtrip: ⚡️ ارتباط پایدار"

        # Encrypt in Python
        py_enc = rp.encrypt_payload(plaintext, passphrase)

        node_script = """
        const RelayCrypto = require('./web_relay/crypto.js');
        async function run() {
            const pass = process.argv[1];
            const enc = JSON.parse(process.argv[2]);
            const dec = await RelayCrypto.decryptText(enc, pass);
            console.log(dec);
        }
        run();
        """

        node_proc = subprocess.run(
            ["node", "-e", node_script, passphrase, json.dumps(py_enc)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        node_decrypted = node_proc.stdout.strip()
        self.assertEqual(node_decrypted, plaintext)


if __name__ == "__main__":
    unittest.main()
