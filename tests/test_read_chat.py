"""Targeted unit tests for scripts/site_proposal/read_chat.py without network calls."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from scripts.site_proposal.read_chat import (
    CHAT_LINK_TEXT,
    TIME_RE,
    _body,
    _extract_messages,
    _find_chat_url,
    _links,
    main,
    parse_args,
    run,
)


def _setup_mock_playwright(eval_fn=None, body_text="خروج", links=None, messages=None,
                           page_url="https://www.karlancer.com/panel/dashboard",
                           dash_login_form=0, enter_error=None):
    """Create a lightweight mock Playwright hierarchy."""
    async def default_eval(js):
        if "class*='message'" in js:
            return messages or []
        if "querySelectorAll" in js:
            return links or []
        if "document.body.innerText" in js:
            return body_text
        return None

    page = MagicMock()
    page.url = page_url
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.evaluate = AsyncMock(side_effect=eval_fn or default_eval)
    loc = MagicMock()
    loc.count = AsyncMock(return_value=dash_login_form)
    page.locator = MagicMock(return_value=loc)
    page.screenshot = AsyncMock()

    ctx = MagicMock()
    ctx.add_cookies = AsyncMock()
    ctx.add_init_script = AsyncMock()
    ctx.new_page = AsyncMock(return_value=page)
    ctx.close = AsyncMock()

    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=ctx)
    browser.close = AsyncMock()

    pw_obj = MagicMock()
    pw_obj.chromium.launch = AsyncMock(return_value=browser)
    pw_ctx = MagicMock()
    if enter_error:
        pw_ctx.__aenter__ = AsyncMock(side_effect=enter_error)
    else:
        pw_ctx.__aenter__ = AsyncMock(return_value=pw_obj)
    pw_ctx.__aexit__ = AsyncMock(return_value=None)

    mod = types.ModuleType("playwright.async_api")
    mod.async_playwright = MagicMock(return_value=pw_ctx)
    return {"modules": {"playwright": types.ModuleType("playwright"), "playwright.async_api": mod},
            "page": page, "context": ctx, "browser": browser}


class TestReadChatArgumentParsing(unittest.TestCase):
    """Test CLI argument parsing and validation for read_chat."""

    def test_parse_args_all_fields_and_defaults(self):
        full = parse_args([
            "--site", "karlancer", "--project-url", "https://karlancer.com/projects/p-10",
            "--project-title", "عنوان", "--job-id", "job-10", "--results-dir", "/tmp/res",
        ])
        self.assertEqual((full.site, full.project_url, full.project_title, full.job_id, full.results_dir),
                         ("karlancer", "https://karlancer.com/projects/p-10", "عنوان", "job-10", "/tmp/res"))

        defs = parse_args(["--site", "karlancer", "--project-url", "https://karlancer.com/projects/p-10", "--job-id", "job-11"])
        self.assertEqual((defs.project_title, defs.results_dir), ("", "hq-ai-results"))

    def test_parse_args_missing_required_flags(self):
        with patch("sys.stderr", new_callable=MagicMock):
            for bad_args in [["--site", "karlancer", "--job-id", "j1"],
                             ["--project-url", "https://karlancer.com/p", "--job-id", "j1"],
                             ["--site", "karlancer", "--project-url", "https://karlancer.com/p"]]:
                with self.assertRaises(SystemExit):
                    parse_args(bad_args)


class TestReadChatHelpers(unittest.TestCase):
    """Test regex matchers and page extraction helpers without network calls."""

    def test_regex_matchers(self):
        for term in ["گفتگو", "چت", "پیام", "مکالمه", "مشاهده گفتگو"]:
            self.assertIsNotNone(CHAT_LINK_TEXT.search(term))
        self.assertIsNone(CHAT_LINK_TEXT.search("تماس با پشتیبانی"))

        for ts in ["14:30", "2026/11/20", "۱۴۰۳", "امروز", "دیروز"]:
            self.assertIsNotNone(TIME_RE.search(ts))
        self.assertIsNone(TIME_RE.search("متن تصادفی"))

    def test_page_eval_helpers(self):
        page = MagicMock()
        page.evaluate = AsyncMock(return_value="محتوای تستی")
        self.assertEqual(asyncio.run(_body(page)), "محتوای تستی")
        page.evaluate = AsyncMock(side_effect=RuntimeError("err"))
        self.assertEqual(asyncio.run(_body(page)), "")

        sample_links = [{"href": "/panel/chat/1", "text": "گفتگو"}]
        page.evaluate = AsyncMock(return_value=sample_links)
        self.assertEqual(asyncio.run(_links(page)), sample_links)
        page.evaluate = AsyncMock(side_effect=RuntimeError("err"))
        self.assertEqual(asyncio.run(_links(page)), [])

        sample_msgs = [{"text": "سلام", "time": "12:00", "side": "me", "cls": "msg-me"}]
        page.evaluate = AsyncMock(return_value=sample_msgs)
        self.assertEqual(asyncio.run(_extract_messages(page)), sample_msgs)
        page.evaluate = AsyncMock(side_effect=RuntimeError("err"))
        self.assertEqual(asyncio.run(_extract_messages(page)), [])


class TestReadChatDiscovery(unittest.TestCase):
    """Test _find_chat_url discovery logic across panel bids, project page, and hub."""

    def setUp(self):
        self.page = MagicMock()
        self.page.url = "https://www.karlancer.com/panel/bids"
        self.page.goto = AsyncMock()
        self.page.wait_for_timeout = AsyncMock()

    def test_find_chat_url_bids_slug_and_single_link(self):
        # 1. Match by slug
        async def eval_bids_slug(js):
            if "document.body.innerText" in js:
                return "پیشنهادات من پروژه ادمین اینستاگرام"
            if "querySelectorAll" in js:
                return [{"href": "/panel/chat/bid-88888", "text": "گفتگو"},
                        {"href": "/panel/chat/bid-99999", "text": "چت دیگر"}]
            return []

        self.page.evaluate = AsyncMock(side_effect=eval_bids_slug)
        url, _ = asyncio.run(_find_chat_url(
            self.page, "https://www.karlancer.com/projects/admin-instagram-88888", "پروژه ادمین اینستاگرام",
        ))
        self.assertEqual(url, "https://www.karlancer.com/panel/chat/bid-88888")

        # 2. Single chat link fallback
        async def eval_single_link(js):
            if "document.body.innerText" in js:
                return "پیشنهادات من پروژه منحصر به فرد"
            if "querySelectorAll" in js:
                return [{"href": "/panel/chat/single-conv", "text": "گفتگو"}]
            return []

        self.page.evaluate = AsyncMock(side_effect=eval_single_link)
        url, _ = asyncio.run(_find_chat_url(
            self.page, "https://www.karlancer.com/projects/other-12345", "پروژه منحصر به فرد",
        ))
        self.assertEqual(url, "https://www.karlancer.com/panel/chat/single-conv")

    def test_find_chat_url_project_page_and_hub_fallbacks(self):
        # 1. Project page fallback
        async def eval_project(js):
            if "querySelectorAll" in js and self.page.url == "https://www.karlancer.com/projects/p-777":
                return [{"href": "/panel/messages/chat-777", "text": "ارسال پیام"}]
            return []

        async def mock_goto(dest, wait_until="domcontentloaded"):
            self.page.url = dest

        self.page.goto = AsyncMock(side_effect=mock_goto)
        self.page.evaluate = AsyncMock(side_effect=eval_project)
        url, _ = asyncio.run(_find_chat_url(self.page, "https://www.karlancer.com/projects/p-777", "عنوان"))
        self.assertEqual(url, "https://www.karlancer.com/panel/messages/chat-777")

        # 2. Not found
        self.page.evaluate = AsyncMock(return_value=[])
        url_none, log = asyncio.run(_find_chat_url(self.page, "https://www.karlancer.com/projects/missing", "پروژه"))
        self.assertIsNone(url_none)
        self.assertTrue(len(log) > 0)


class TestReadChatExecutionAndOutput(unittest.TestCase):
    """Test full execution of run(), session handling, secret masking, and JSON output."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.results_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_missing_session_records_login_required(self):
        with patch.dict(os.environ, {}, clear=True):
            res = asyncio.run(run("karlancer", "https://karlancer.com/projects/t1", "تست", "j_no_sess", str(self.results_dir)))
        self.assertEqual(res["status"], "login_required")
        self.assertFalse(res["logged_in"])
        self.assertIn("SITE_SESSIONS environment variable is not configured", res["error_message"])

        out_file = self.results_dir / "jobs" / "j_no_sess" / "result.json"
        self.assertTrue(out_file.exists())
        saved = json.loads(out_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["job_id"], "j_no_sess")

    def test_secret_masking_in_session_error_and_exception(self):
        secret1 = "SUPER_SECRET_TOKEN_99999"
        with patch("scripts.site_proposal.read_chat.load_site_sessions", return_value=({}, f"Fail token={secret1}")):
            res = asyncio.run(run("karlancer", "https://karlancer.com/p", "ت", "j_s1", str(self.results_dir)))
        self.assertEqual(res["status"], "login_required")
        self.assertNotIn(secret1, res["error_message"])
        self.assertIn("[REDACTED]", res["error_message"])

        secret2 = "PASSWORD_SECRET_88888"
        mock_pw = _setup_mock_playwright(enter_error=RuntimeError(f"Network error pass={secret2}"))
        env = {"SITE_SESSION_KARLANCER": json.dumps({"cookies": [{"name": "a", "value": secret2, "domain": "karlancer.com"}],
                                                     "password": secret2})}
        with patch.dict(os.environ, env, clear=True), patch.dict(sys.modules, mock_pw["modules"]):
            res2 = asyncio.run(run("karlancer", "https://karlancer.com/p", "ت", "j_s2", str(self.results_dir)))
        self.assertEqual(res2["status"], "error")
        self.assertNotIn(secret2, res2["error_message"])
        self.assertIn("[REDACTED]", res2["error_message"])
        disk_content = (self.results_dir / "jobs" / "j_s2" / "result.json").read_text(encoding="utf-8")
        self.assertNotIn(secret2, disk_content)

    def test_run_success_flow(self):
        msgs = [{"text": "سلام وقت بخیر", "time": "14:00", "side": "sender", "cls": "msg-sender"},
                {"text": "سلام، پروژه تحویل شد", "time": "14:15", "side": "me", "cls": "msg-me"}]
        mock_pw = _setup_mock_playwright(
            body_text="داشبورد خروج گفتگو با کارفرما",
            links=[{"href": "/panel/chat/proj-101", "text": "گفتگو"}],
            messages=msgs,
        )
        session = {
            "cookies": [{"name": "auth", "value": "val1", "domain": "karlancer.com", "sameSite": "strict", "storeId": "0"}],
            "localStorage": {"uid": "42"},
        }

        with patch.dict(os.environ, {"SITE_SESSION_KARLANCER": json.dumps(session)}, clear=True), \
             patch.dict(sys.modules, mock_pw["modules"]):
            res = asyncio.run(run("karlancer", "https://www.karlancer.com/projects/proj-101", "گفتگو", "job_ok", str(self.results_dir)))

        self.assertEqual(res["status"], "ok")
        self.assertTrue(res["logged_in"])
        self.assertEqual(res["chat_url"], "https://www.karlancer.com/panel/chat/proj-101")
        self.assertEqual(len(res["messages"]), 2)
        self.assertEqual(res["messages"][0]["text"], "سلام وقت بخیر")

        mock_pw["context"].add_cookies.assert_called_once()
        sanitized = mock_pw["context"].add_cookies.call_args[0][0]
        self.assertEqual(sanitized[0]["name"], "auth")
        self.assertNotIn("storeId", sanitized[0])
        mock_pw["context"].add_init_script.assert_called_once()

        out_file = self.results_dir / "jobs" / "job_ok" / "result.json"
        self.assertTrue(out_file.exists())
        saved = json.loads(out_file.read_text(encoding="utf-8"))
        for k in ["job_id", "site", "project_url", "mode", "status", "logged_in",
                  "chat_url", "messages", "body_excerpt", "discovery", "error_message", "updated_at"]:
            self.assertIn(k, saved)

    def test_run_session_expired_dashboard(self):
        mock_pw = _setup_mock_playwright(body_text="فرم ورود به سامانه", page_url="https://www.karlancer.com/login", dash_login_form=1)
        sess = {"cookies": [{"name": "auth", "value": "exp", "domain": "karlancer.com"}]}
        with patch.dict(os.environ, {"SITE_SESSION_KARLANCER": json.dumps(sess)}, clear=True), \
             patch.dict(sys.modules, mock_pw["modules"]):
            res = asyncio.run(run("karlancer", "https://karlancer.com/p", "", "job_exp", str(self.results_dir)))

        self.assertEqual(res["status"], "login_required")
        self.assertFalse(res["logged_in"])
        self.assertIn("Karlancer session expired", res["error_message"])
        self.assertTrue(res["dash_has_login_form"])
        self.assertEqual(res["screenshot_path"], "jobs/job_exp/dashboard.png")

    def test_run_chat_not_found(self):
        mock_pw = _setup_mock_playwright(body_text="داشبورد خروج کاربر", links=[])
        sess = {"cookies": [{"name": "auth", "value": "ok", "domain": "karlancer.com"}]}
        with patch.dict(os.environ, {"SITE_SESSION_KARLANCER": json.dumps(sess)}, clear=True), \
             patch.dict(sys.modules, mock_pw["modules"]):
            res = asyncio.run(run("karlancer", "https://karlancer.com/projects/no-chat", "", "job_nf", str(self.results_dir)))

        self.assertEqual(res["status"], "not_found")
        self.assertTrue(res["logged_in"])
        self.assertIn("No conversation link found", res["error_message"])

    def test_main_cli_execution(self):
        mock_res = {"status": "ok", "job_id": "cli_job_1"}
        with patch("scripts.site_proposal.read_chat.run", AsyncMock(return_value=mock_res)):
            with patch("sys.stdout", new_callable=MagicMock):
                code = main(["--site", "karlancer", "--project-url", "https://karlancer.com/p",
                             "--job-id", "cli_job_1", "--results-dir", str(self.results_dir)])
                self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
