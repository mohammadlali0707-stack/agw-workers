"""Unit tests for scripts/site_proposal/parscoders_profile_probe.py.

Verifies mock timeout recovery, exponential backoff, and retry behavior offline
without live network dependencies or external services.
"""

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from scripts.site_proposal.parscoders_profile_probe import (
    CANDIDATES,
    EMAIL_RE,
    PROFILE_HREF_RE,
    SITE,
    goto_with_retry,
    main,
    probe_actions_menu,
    probe_portfolio,
    probe_sample_details,
    sanitize_cookies,
    scroll_page,
)


def _build_playwright_mock(page_goto_fn=None, body_text="محمد لعلی", header_text="محمد لعلی", page_url="https://parscoders.com/"):
    page = MagicMock()
    page.url = page_url
    page.title.return_value = "ParsCoders"
    page.goto.side_effect = page_goto_fn if page_goto_fn else (lambda *a, **kw: MagicMock())

    def locator_dispatch(sel):
        loc = MagicMock()
        if "body" in sel:
            loc.first.inner_text.return_value = body_text
        elif "header" in sel:
            loc.first.inner_text.return_value = header_text
        elif "login" in sel:
            loc.first.is_visible.return_value = False
        else:
            loc.first.inner_text.return_value = ""
            loc.all.return_value = []
        return loc

    page.locator.side_effect = locator_dispatch
    page.evaluate.return_value = "Contact: testuser@example.com"

    ctx = MagicMock()
    ctx.new_page.return_value = page
    browser = MagicMock()
    browser.new_context.return_value = ctx
    pw = MagicMock()
    pw.chromium.launch.return_value = browser

    pw_cm = MagicMock()
    pw_cm.__enter__.return_value = pw
    pw_cm.__exit__.return_value = False

    mod_sync = types.ModuleType("playwright.sync_api")
    mod_sync.sync_playwright = MagicMock(return_value=pw_cm)
    modules = {"playwright": types.ModuleType("playwright"), "playwright.sync_api": mod_sync}
    return modules, page


class TestCookieSanitization(unittest.TestCase):
    """Test cookie sanitization logic."""

    def test_sanitize_cookies_valid_and_defaults(self):
        cookies = [
            {"name": "sess", "value": "123", "sameSite": "lax", "expires": "1735689600.5"},
            {"name": "token", "value": "abc", "sameSite": "invalid", "expires": "not-a-number"},
            {"invalid": "entry"},
            "not-a-dict",
        ]
        sanitized = sanitize_cookies(cookies, default_domain="parscoders.com")
        self.assertEqual(len(sanitized), 2)
        c0, c1 = sanitized[0], sanitized[1]
        self.assertEqual((c0["name"], c0["value"], c0["sameSite"], c0["expires"], c0["domain"], c0["path"]),
                         ("sess", "123", "Lax", 1735689600.5, "parscoders.com", "/"))
        self.assertEqual((c1["name"], c1["value"], c1["domain"]), ("token", "abc", "parscoders.com"))
        self.assertNotIn("sameSite", c1)
        self.assertNotIn("expires", c1)


class TestGotoWithRetry(unittest.TestCase):
    """Test resilient goto_with_retry logic, retries, and backoff."""

    def test_goto_success_first_attempt(self):
        page = MagicMock()
        mock_resp = MagicMock()
        page.goto.return_value = mock_resp
        sleep_mock = MagicMock()

        resp = goto_with_retry(page, "https://parscoders.com/", timeout=5000, sleep_fn=sleep_mock)
        self.assertEqual(resp, mock_resp)
        page.goto.assert_called_once_with("https://parscoders.com/", wait_until="domcontentloaded", timeout=5000)
        sleep_mock.assert_not_called()

    def test_goto_recovers_after_transient_timeout(self):
        page = MagicMock()
        mock_resp = MagicMock()
        page.goto.side_effect = [
            Exception("page.goto: net::ERR_TIMED_OUT at https://parscoders.com/"),
            Exception("TimeoutError: Timeout 60000ms exceeded"),
            mock_resp,
        ]
        sleep_mock = MagicMock()

        resp = goto_with_retry(page, "https://parscoders.com/profile", max_retries=3,
                               initial_backoff=1.0, backoff_factor=2.0, sleep_fn=sleep_mock)
        self.assertEqual(resp, mock_resp)
        self.assertEqual(page.goto.call_count, 3)
        self.assertEqual(sleep_mock.call_count, 2)
        sleep_mock.assert_has_calls([call(1.0), call(2.0)])

    def test_goto_exhausts_retries_and_raises(self):
        page = MagicMock()
        timeout_err = Exception("page.goto: net::ERR_TIMED_OUT")
        page.goto.side_effect = timeout_err
        sleep_mock = MagicMock()

        with self.assertRaises(Exception) as cm:
            goto_with_retry(page, "https://parscoders.com/", max_retries=2,
                            initial_backoff=0.5, backoff_factor=3.0, sleep_fn=sleep_mock)
        self.assertIs(cm.exception, timeout_err)
        self.assertEqual(page.goto.call_count, 3)
        self.assertEqual(sleep_mock.call_count, 2)
        sleep_mock.assert_has_calls([call(0.5), call(1.5)])

    def test_goto_env_var_configuration(self):
        page = MagicMock()
        page.goto.side_effect = [Exception("net::ERR_TIMED_OUT"), MagicMock()]
        sleep_mock = MagicMock()

        env = {
            "PROBE_NAV_RETRIES": "4",
            "PROBE_NAV_INITIAL_BACKOFF": "0.25",
            "PROBE_NAV_BACKOFF": "4.0",
            "PROBE_NAV_TIMEOUT_MS": "45000",
        }
        with patch.dict(os.environ, env):
            goto_with_retry(page, "https://parscoders.com/panel", sleep_fn=sleep_mock)

        page.goto.assert_called_with("https://parscoders.com/panel", wait_until="domcontentloaded", timeout=45000)
        self.assertEqual(page.goto.call_count, 2)
        sleep_mock.assert_called_once_with(0.25)

    def test_goto_invalid_env_var_fallback(self):
        page = MagicMock()
        page.goto.return_value = MagicMock()
        env = {
            "PROBE_NAV_RETRIES": "bad_int",
            "PROBE_NAV_INITIAL_BACKOFF": "bad_float",
            "PROBE_NAV_BACKOFF": "bad_float",
            "PROBE_NAV_TIMEOUT_MS": "bad_int",
        }
        with patch.dict(os.environ, env):
            goto_with_retry(page, "https://parscoders.com/")
        page.goto.assert_called_once_with("https://parscoders.com/", wait_until="domcontentloaded", timeout=60000)


class TestProbePageHelpers(unittest.TestCase):
    """Test probe helpers with mocked page calls."""

    def test_scroll_page(self):
        page = MagicMock()
        scroll_page(page)
        self.assertEqual(page.evaluate.call_count, 2)
        self.assertEqual(page.wait_for_timeout.call_count, 2)

    def test_probe_portfolio(self):
        page = MagicMock()
        page.url = "https://parscoders.com/resume/portfolio/"
        page.evaluate.return_value = {
            "samples": [{"title": "Sample 1", "show_url": "/show/123/", "edit_url": "/edit/123", "card_text": "Sample card"}],
            "add_links": [{"href": "/resume/portfolio/new", "text": "افزودن"}],
            "buttons": ["دکمه"],
            "page_text": "نمونه کارها",
        }
        loc = MagicMock()
        loc.first.inner_text.return_value = "محمد لعلی خوش آمدید"
        page.locator.return_value = loc

        with tempfile.TemporaryDirectory() as tmpdir:
            data = probe_portfolio(page, Path(tmpdir))
            self.assertEqual(data["url"], "https://parscoders.com/resume/portfolio/")
            self.assertTrue(data["has_display_name"])
            self.assertFalse(data["on_login_page"])
            self.assertEqual(len(data["samples"]), 1)

    def test_probe_sample_details(self):
        page = MagicMock()
        page.title.return_value = "Sample Title"
        page.url = "https://parscoders.com/resume/portfolio/123/edit"
        loc = MagicMock()
        loc.first.inner_text.return_value = "Sample body text"
        page.locator.return_value = loc
        page.evaluate.side_effect = [
            [{"href": "/edit", "text": "ویرایش"}],
            [{"action": "/save", "method": "post", "fields": [], "buttons": []}],
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            samples = [{"show_url": "/resume/portfolio/show/123/", "title": "Test"}]
            details = probe_sample_details(page, Path(tmpdir), samples)
            self.assertEqual(len(details), 1)
            self.assertEqual(details[0]["sample_id"], "123")
            self.assertEqual(details[0]["show_title"], "Sample Title")
            self.assertEqual(details[0]["edit_final_url"], "https://parscoders.com/resume/portfolio/123/edit")

    def test_probe_actions_menu(self):
        page = MagicMock()
        page.url = "https://parscoders.com/resume/portfolio/"
        page.evaluate.side_effect = [
            [{"href": "/portfolio", "text": "نمونه", "visible": True, "cls": ""}],
            {"found": True, "row_html": "<tr>...</tr>", "controls": []},
            "clicked toggle: button",
            [{"tag": "a", "href": "/edit", "text": "ویرایش", "onclick": ""}],
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            out = probe_actions_menu(page, Path(tmpdir))
            self.assertEqual(out["toggle_clicked"], "clicked toggle: button")
            self.assertEqual(len(out["open_menu_items"]), 1)


class TestMainResilience(unittest.TestCase):
    """Test full probe workflow and timeout recovery in main()."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.results_dir = Path(self.tmpdir.name)
        self.session_data = json.dumps({
            "cookies": [{"name": "auth", "value": "xyz", "domain": "parscoders.com"}],
            "localStorage": {"user_id": "999"},
        })

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_main_homepage_transient_timeout_recovery(self):
        attempts = {"count": 0}

        def mock_goto(url, **kwargs):
            if url == "https://parscoders.com/":
                attempts["count"] += 1
                if attempts["count"] == 1:
                    raise Exception("page.goto: net::ERR_TIMED_OUT at https://parscoders.com/")
            return MagicMock()

        modules, _ = _build_playwright_mock(page_goto_fn=mock_goto)

        env = {
            "SITE_SESSION_PARSCODERS": self.session_data,
            "PROBE_RESULTS_DIR": str(self.results_dir),
            "PROBE_JOB_ID": "job-recover",
            "PROBE_NAV_INITIAL_BACKOFF": "0.001",
            "PROBE_NAV_RETRIES": "2",
        }
        with patch.dict(sys.modules, modules):
            with patch.dict(os.environ, env):
                with patch("scripts.site_proposal.parscoders_profile_probe.probe_portfolio", return_value={}):
                    with patch("scripts.site_proposal.parscoders_profile_probe.probe_sample_details", return_value=[]):
                        with patch("scripts.site_proposal.parscoders_profile_probe.probe_actions_menu", return_value={}):
                            exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertGreaterEqual(attempts["count"], 2)
        res_file = self.results_dir / "jobs" / "job-recover" / "result.json"
        self.assertTrue(res_file.exists())
        with open(res_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertTrue(data["logged_in"])
        self.assertEqual(data["display_name"], "محمد لعلی")
        self.assertEqual(data["email"], "testuser@example.com")

    def test_main_homepage_fatal_timeout_exits_cleanly(self):
        def fatal_goto(url, **kwargs):
            if url == "https://parscoders.com/":
                raise Exception("page.goto: net::ERR_TIMED_OUT fatal")
            return MagicMock()

        modules, _ = _build_playwright_mock(page_goto_fn=fatal_goto)

        env = {
            "SITE_SESSION_PARSCODERS": self.session_data,
            "PROBE_RESULTS_DIR": str(self.results_dir),
            "PROBE_JOB_ID": "job-fatal",
            "PROBE_NAV_INITIAL_BACKOFF": "0.001",
            "PROBE_NAV_RETRIES": "1",
        }
        with patch.dict(sys.modules, modules):
            with patch.dict(os.environ, env):
                exit_code = main()

        self.assertEqual(exit_code, 1)
        res_file = self.results_dir / "jobs" / "job-fatal" / "result.json"
        self.assertTrue(res_file.exists())
        with open(res_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("homepage_error", data)
        self.assertIn("net::ERR_TIMED_OUT fatal", data["homepage_error"])

    def test_main_profile_page_timeout_continues(self):
        def mock_goto(url, **kwargs):
            if "/user-info/update" in url:
                raise Exception("page.goto: net::ERR_TIMED_OUT")
            return MagicMock()

        modules, _ = _build_playwright_mock(page_goto_fn=mock_goto)

        env = {
            "SITE_SESSION_PARSCODERS": self.session_data,
            "PROBE_RESULTS_DIR": str(self.results_dir),
            "PROBE_JOB_ID": "job-candidate-timeout",
            "PROBE_NAV_INITIAL_BACKOFF": "0.001",
            "PROBE_NAV_RETRIES": "1",
        }
        with patch.dict(sys.modules, modules):
            with patch.dict(os.environ, env):
                with patch("scripts.site_proposal.parscoders_profile_probe.probe_portfolio", return_value={}):
                    with patch("scripts.site_proposal.parscoders_profile_probe.probe_sample_details", return_value=[]):
                        with patch("scripts.site_proposal.parscoders_profile_probe.probe_actions_menu", return_value={}):
                            exit_code = main()

        self.assertEqual(exit_code, 0)
        res_file = self.results_dir / "jobs" / "job-candidate-timeout" / "result.json"
        self.assertTrue(res_file.exists())
        with open(res_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        failed_pages = [p for p in data["pages"] if p.get("error")]
        self.assertGreaterEqual(len(failed_pages), 1)
        self.assertEqual(failed_pages[0]["url"], "https://parscoders.com/user-info/update")


if __name__ == "__main__":
    unittest.main()
