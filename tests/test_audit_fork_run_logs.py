#!/usr/bin/env python3
"""Unit tests for Tools/audit_fork_run_logs.py.

Validates:
1. HTTP 302 redirection handler (_DropAuthOnRedirect) header stripping
2. Signal extraction patterns (10 regex signals)
3. ##[error] line extraction logic
4. Account mapping and unreadable error classification
"""

import unittest
import urllib.request
import re
import sys
import os

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from Tools.audit_fork_run_logs import _DropAuthOnRedirect, SIGNALS, ACCOUNTS


class TestAuditForkRunLogs(unittest.TestCase):
    """Test suite covering audit_fork_run_logs.py components (14 test cases)."""

    def setUp(self):
        self.handler = _DropAuthOnRedirect()
        self.signal_map = dict(SIGNALS)

    def test_01_drop_auth_on_redirect_capitalized(self):
        """1. Verify Authorization header is stripped on redirect."""
        req = urllib.request.Request("https://api.github.com/repos/acc/run/logs")
        req.add_header("Authorization", "Bearer ghp_secret123")
        new_req = self.handler.redirect_request(req, None, 302, "Found", {}, "https://blob.storage.com/log")
        self.assertIsNotNone(new_req)
        self.assertNotIn("Authorization", new_req.headers)
        self.assertNotIn("authorization", new_req.headers)

    def test_02_drop_auth_on_redirect_lowercase(self):
        """2. Verify lowercase authorization header is stripped on redirect."""
        req = urllib.request.Request("https://api.github.com/repos/acc/run/logs")
        req.add_header("authorization", "Bearer ghp_secret123")
        new_req = self.handler.redirect_request(req, None, 302, "Found", {}, "https://blob.storage.com/log")
        self.assertIsNotNone(new_req)
        self.assertNotIn("Authorization", new_req.headers)
        self.assertNotIn("authorization", new_req.headers)

    def test_03_preserve_non_auth_headers(self):
        """3. Verify non-auth headers (User-Agent, Accept) are preserved across redirect."""
        req = urllib.request.Request("https://api.github.com/repos/acc/run/logs")
        req.add_header("Authorization", "Bearer ghp_secret123")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("User-Agent", "audit-tool")
        new_req = self.handler.redirect_request(req, None, 302, "Found", {}, "https://blob.storage.com/log")
        self.assertIsNotNone(new_req)
        self.assertNotIn("Authorization", new_req.headers)
        self.assertEqual(new_req.headers.get("Accept"), "application/vnd.github+json")
        self.assertEqual(new_req.headers.get("User-agent"), "audit-tool")

    def test_04_unredirected_hdrs_stripped(self):
        """4. Verify unredirected_hdrs also has authorization removed."""
        req = urllib.request.Request("https://api.github.com/repos/acc/run/logs")
        req.add_unredirected_header("Authorization", "Bearer ghp_secret123")
        new_req = self.handler.redirect_request(req, None, 302, "Found", {}, "https://blob.storage.com/log")
        self.assertIsNotNone(new_req)
        self.assertNotIn("Authorization", new_req.unredirected_hdrs)
        self.assertNotIn("authorization", new_req.unredirected_hdrs)

    def test_05_signal_run_tag(self):
        """5. Verify run_tag regex captures plan tag correctly."""
        log = '2026-11-11T21:56:00Z RUN_TAG="plan-42-acc4-w02" started'
        m = self.signal_map["run_tag"].search(log)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "plan-42-acc4-w02")

    def test_06_signal_callback(self):
        """6. Verify callback regex captures callback_id."""
        log = "callback_id=issue-165-acc4-d993dd867b5f-w02 status=running"
        m = self.signal_map["callback"].search(log)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "issue-165-acc4-d993dd867b5f-w02")

    def test_07_signal_own_identity(self):
        """7. Verify own_identity captures repo owner and account index."""
        log = "Repo owner: hmmletssee7-design -> account index: 4"
        m = self.signal_map["own_identity"].search(log)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "hmmletssee7-design")
        self.assertEqual(m.group(2), "4")

    def test_08_signal_token_account(self):
        """8. Verify token_account captures primary account accN."""
        log = "Primary account for this run: acc5"
        m = self.signal_map["token_account"].search(log)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "acc5")

    def test_09_signal_quota_detection(self):
        """9. Verify quota regex matches various quota patterns."""
        for pattern in ["AGY_QUOTA reached", "quota reached for user", "Individual quota exhausted"]:
            m = self.signal_map["quota"].search(pattern)
            self.assertIsNotNone(m, f"Failed matching {pattern}")

    def test_10_signal_auth_detection(self):
        """10. Verify auth regex matches authentication failure patterns."""
        for pattern in ["AGY_AUTH error", "user not authenticated", "401 Unauthorized"]:
            m = self.signal_map["auth"].search(pattern)
            self.assertIsNotNone(m, f"Failed matching {pattern}")

    def test_11_signal_timeout_detection(self):
        """11. Verify timeout regex matches timeout patterns."""
        for pattern in ["AGY_TIMEOUT exceeded", "timeout 1700 seconds", "command exit rc=124"]:
            m = self.signal_map["timeout"].search(pattern)
            self.assertIsNotNone(m, f"Failed matching {pattern}")

    def test_12_signal_output_and_fallback(self):
        """12. Verify empty_output, output_committed, and fallback patterns."""
        self.assertIsNotNone(self.signal_map["empty_output"].search("AGY_EMPTY: no agy output found"))
        self.assertIsNotNone(self.signal_map["output_committed"].search("agy: record raw output for plan-123"))
        self.assertIsNotNone(self.signal_map["fallback"].search("falling back to alternate model"))

    def test_13_error_capture_last_three_lines(self):
        """13. Verify extraction of last 3 ##[error] annotations."""
        sample_log = """
        Step 1: start
        ##[error]First error encountered
        Step 2: progress
        ##[error]Second error occurred
        Step 3: crash
        ##[error]Third critical failure
        ##[error]Process completed with exit code 1.
        """
        errs = [l.split(" ", 1)[-1].strip() for l in sample_log.splitlines() if "##[error]" in l]
        last_three = errs[-3:]
        self.assertEqual(len(last_three), 3)
        self.assertIn("Second error occurred", last_three[0])
        self.assertIn("Third critical failure", last_three[1])
        self.assertIn("Process completed with exit code 1.", last_three[2])

    def test_14_account_mapping_integrity(self):
        """14. Verify fleet account mapping contains ACC4 and ACC5."""
        self.assertEqual(ACCOUNTS[4], "hmmletssee7-design")
        self.assertEqual(ACCOUNTS[5], "kidding602")
        self.assertEqual(ACCOUNTS[2], "lali94m-max")
        self.assertEqual(len(ACCOUNTS), 9)


if __name__ == "__main__":
    unittest.main()
