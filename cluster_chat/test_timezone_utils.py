"""cluster_chat/test_timezone_utils.py

Automated unit tests for Asia/Tehran timezone formatting and UTC ISO preservation (P9).
Validates:
1. Python timezone helpers: format_tehran_time, format_tehran_display, ensure_utc_iso.
2. Acceptance criteria: '2026-10-28T10:00:00Z' formats to '13:30' in Asia/Tehran.
3. Subsecond precision preservation (microseconds).
4. Safe edge-case handling: no silent data corruption on invalid inputs, boolean rejection, non-finite rejection.
5. JavaScript timezone utilities executed via Node.js subprocess.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess
import unittest
from zoneinfo import ZoneInfo

from cluster_chat.timezone_utils import (
    TEHRAN_TZ,
    ensure_utc_iso,
    format_tehran_datetime,
    format_tehran_display,
    format_tehran_time,
    parse_utc_datetime,
    to_tehran_datetime,
    utc_now_iso,
)


class TestTehranTimezonePython(unittest.TestCase):
    """Test suite for Python Asia/Tehran timezone formatting and UTC ISO helpers."""

    def test_tehran_timezone_definition(self):
        self.assertEqual(TEHRAN_TZ.key, "Asia/Tehran")

    def test_utc_to_tehran_time_acceptance_criteria(self):
        """Acceptance Criteria: '2026-10-28T10:00:00Z' formats to '13:30' in Asia/Tehran."""
        iso_utc = "2026-10-28T10:00:00Z"
        formatted = format_tehran_time(iso_utc)
        self.assertEqual(formatted, "13:30")

    def test_naive_iso_treated_as_utc(self):
        """Naive ISO strings without 'Z' must be treated as UTC, not local time."""
        naive_iso1 = "2026-10-28T10:00:00"
        naive_iso2 = "2026-10-28 10:00:00"
        self.assertEqual(format_tehran_time(naive_iso1), "13:30")
        self.assertEqual(format_tehran_time(naive_iso2), "13:30")

    def test_offset_iso_converted_accurately(self):
        """ISO strings with offsets like +03:30 convert accurately."""
        tehran_iso = "2026-10-28T13:30:00+03:30"
        self.assertEqual(format_tehran_time(tehran_iso), "13:30")
        self.assertEqual(ensure_utc_iso(tehran_iso), "2026-10-28T10:00:00Z")

    def test_datetime_object_input(self):
        """Datetime objects (aware and naive) are correctly handled."""
        dt_utc = datetime(2026, 10, 28, 10, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(format_tehran_time(dt_utc), "13:30")

        dt_naive = datetime(2026, 10, 28, 10, 0, 0)
        self.assertEqual(format_tehran_time(dt_naive), "13:30")

    def test_subsecond_precision_preserved(self):
        """Microseconds are preserved in ensure_utc_iso without being stripped."""
        ts_with_ms = "2026-10-28T10:00:00.123456Z"
        self.assertEqual(ensure_utc_iso(ts_with_ms), "2026-10-28T10:00:00.123456Z")

        dt_ms = datetime(2026, 10, 28, 10, 0, 0, 987654, tzinfo=timezone.utc)
        self.assertEqual(ensure_utc_iso(dt_ms), "2026-10-28T10:00:00.987654Z")

    def test_no_silent_corruption_on_invalid_data(self):
        """ensure_utc_iso must raise ValueError on unparseable data rather than overwriting with now()."""
        with self.assertRaises(ValueError):
            ensure_utc_iso("not-a-valid-timestamp")
        with self.assertRaises(ValueError):
            ensure_utc_iso("2026-99-99T99:99:99")

    def test_boolean_rejection(self):
        """Boolean True/False must not be parsed into 1970 epoch timestamps."""
        self.assertIsNone(parse_utc_datetime(True))
        self.assertIsNone(parse_utc_datetime(False))
        with self.assertRaises(ValueError):
            ensure_utc_iso(True)

    def test_non_finite_and_arbitrary_numeric_rejection(self):
        """Strings like 'inf', 'nan', or arbitrary numbers like '2026' must not be parsed into 1970."""
        self.assertIsNone(parse_utc_datetime("inf"))
        self.assertIsNone(parse_utc_datetime("-inf"))
        self.assertIsNone(parse_utc_datetime("nan"))
        self.assertIsNone(parse_utc_datetime("2026"))

    def test_format_tehran_display_options(self):
        iso_utc = "2026-10-28T10:00:00Z"
        self.assertEqual(format_tehran_display(iso_utc, include_date=False), "13:30")
        self.assertEqual(format_tehran_display(iso_utc, include_date=True), "2026-10-28 13:30")
        self.assertEqual(
            format_tehran_display(iso_utc, include_date=True, include_seconds=True),
            "2026-10-28 13:30:00",
        )
        self.assertEqual(format_tehran_datetime(iso_utc), "2026-10-28 13:30:00")

    def test_ensure_utc_iso_defaults(self):
        """Ensures None or empty string defaults to current UTC time ending with 'Z'."""
        self.assertTrue(ensure_utc_iso(None).endswith("Z"))
        self.assertTrue(ensure_utc_iso("").endswith("Z"))
        self.assertEqual(ensure_utc_iso("2026-10-28 10:00:00"), "2026-10-28T10:00:00Z")
        self.assertEqual(ensure_utc_iso("2026-10-28T10:00:00Z"), "2026-10-28T10:00:00Z")

        # Unix epoch timestamp (10 digits)
        self.assertEqual(ensure_utc_iso(1793181600), "2026-10-28T10:00:00Z")


class TestTehranTimezoneJavaScript(unittest.TestCase):
    """Test suite validating JavaScript timezone helpers via Node.js."""

    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parent.parent
        cls.js_utils_path = cls.repo_root / "cluster_chat" / "timezone_utils.js"
        cls.app_js_path = cls.repo_root / "cluster_chat" / "web" / "app.js"

    def test_node_js_timezone_utils(self):
        """Validate timezone_utils.js under Node.js."""
        node_script = """
        const utils = require(process.argv[1]);
        const assert = require('assert');

        // 1. Acceptance Criteria: '2026-10-28T10:00:00Z' -> '13:30'
        const t1 = utils.formatTehranTime('2026-10-28T10:00:00Z');
        assert.strictEqual(t1, '13:30', `Expected 13:30, got ${t1}`);

        // 2. Naive ISO strings treated as UTC
        const t2 = utils.formatTehranTime('2026-10-28 10:00:00');
        assert.strictEqual(t2, '13:30', `Expected 13:30 for naive string, got ${t2}`);

        const t3 = utils.formatTehranTime('2026-10-28T10:00:00');
        assert.strictEqual(t3, '13:30', `Expected 13:30 for naive T string, got ${t3}`);

        // 3. Subsecond precision handling in JS parse
        const dSub = utils.parseUtcDate('2026-10-28T10:00:00.500Z');
        assert.strictEqual(dSub.getUTCMilliseconds(), 500);

        // 4. Boolean rejection in JS
        assert.strictEqual(utils.parseUtcDate(true), null);
        assert.strictEqual(utils.parseUtcDate(false), null);

        // 5. Arbitrary numeric string rejection in JS
        assert.strictEqual(utils.parseUtcDate('2026'), null);
        assert.strictEqual(utils.parseUtcDate('inf'), null);

        // 6. Full date time formatting
        const dt1 = utils.formatTehranDateTime('2026-10-28T10:00:00Z');
        assert.ok(dt1.includes('2026-10-28'), `Expected 2026-10-28 in ${dt1}`);
        assert.ok(dt1.includes('13:30:00'), `Expected 13:30:00 in ${dt1}`);

        // 7. Invalid or null inputs handled gracefully
        assert.strictEqual(utils.formatTehranTime(null), '');
        assert.strictEqual(utils.formatTehranTime(''), '');
        assert.strictEqual(utils.formatTehranTime('invalid-date'), 'invalid-date');

        console.log('NODE_JS_UTILS_OK');
        """
        proc = subprocess.run(
            ["node", "-e", node_script, str(self.js_utils_path)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, f"Node error: {proc.stderr}\n{proc.stdout}")
        self.assertIn("NODE_JS_UTILS_OK", proc.stdout)

    def test_app_js_inlined_timezone_helpers(self):
        """Validate that cluster_chat/web/app.js exposes formatTehranTime and uses it."""
        node_script = """
        const fs = require('fs');
        const vm = require('vm');
        const code = fs.readFileSync(process.argv[1], 'utf-8');

        const mockDocument = {
          getElementById: () => null,
          createElement: (tag) => ({
            className: '',
            dataset: {},
            classList: { add: () => {} },
            innerHTML: '',
            appendChild: () => {}
          }),
          addEventListener: () => {},
          readyState: 'complete'
        };

        const context = {
          window: { location: { host: 'localhost:8080', protocol: 'http:' } },
          document: mockDocument,
          WebSocket: class {},
          navigator: {},
          console: console,
          setTimeout: () => 1,
          clearTimeout: () => {},
          setInterval: () => 1,
          clearInterval: () => {},
          fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) }),
          URL: URL,
          URLSearchParams: URLSearchParams,
          Intl: Intl,
          Date: Date
        };
        context.window.window = context.window;

        vm.createContext(context);
        vm.runInContext(code, context);

        const UI = context.window.ClusterChatUI;
        if (!UI) {
          console.error('ClusterChatUI not defined');
          process.exit(1);
        }

        // Acceptance Criteria: 2026-10-28T10:00:00Z -> 13:30
        const formatted = UI.formatTehranTime('2026-10-28T10:00:00Z');
        if (formatted !== '13:30') {
          console.error(`Expected 13:30 from UI.formatTehranTime, got ${formatted}`);
          process.exit(1);
        }

        // Test message element creation renders Tehran time
        const msgElem = UI.createMessageElement({
          message_id: 'msg_1',
          sender: 'argon',
          text: 'Salam',
          created_at: '2026-10-28T10:00:00Z'
        });

        if (!msgElem.innerHTML.includes('13:30')) {
          console.error('Message element HTML does not contain 13:30: ' + msgElem.innerHTML);
          process.exit(1);
        }

        console.log('APP_JS_OK');
        process.exit(0);
        """
        proc = subprocess.run(
            ["node", "-e", node_script, str(self.app_js_path)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, f"Node error: {proc.stderr}\n{proc.stdout}")
        self.assertIn("APP_JS_OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
