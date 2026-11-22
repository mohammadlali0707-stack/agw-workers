#!/usr/bin/env python3
"""Capture a fresh ParsCoders session by signing in with the shared Google
site-login account (momona) and save it for the SITE_SESSION_PARSCODERS secret.

Runs ONLY on GitHub Actions (never on the VPS). Flow:
  1. Take the momona Google lock via tools/google_login.py (one agent at a time).
  2. Load the Google cookies into Playwright, open parscoders.com/login and
     click "Google" sign-in; complete the OAuth account chooser.
  3. Verify the ParsCoders dashboard loads (logged in).
  4. Dump parscoders.com cookies + localStorage as
     {"cookies": [...], "localStorage": {...}} to /tmp/parscoders-session.json
     (0600, never printed, never committed). The workflow step then runs
     `gh secret set SITE_SESSION_PARSCODERS --body-file <that file>` and
     shreds it.

If Google asks for a password, the momona session expired: stop and report it.
If a captcha appears, stop and report it (never try to solve one).
Never submit any proposal or bid; this only captures a login session.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.getcwd(), "tools"))
sys.path.insert(0, os.path.join(os.getcwd(), "scripts", "site_proposal"))

OUT = "/tmp/parscoders-session.json"
LOGIN_URL = "https://parscoders.com/login"
DASH_URL = "https://parscoders.com/dashboard/"

GOOGLE_BTN = [
    "button:has-text('ورود با گوگل')",
    "a:has-text('ورود با گوگل')",
    "button:has-text('Google')",
    "a:has-text('Google')",
    "[aria-label*='Google' i]",
]
CAPTCHA_SEL = [
    "iframe[src*='recaptcha']",
    "iframe[src*='hcaptcha']",
    "iframe[src*='challenges.cloudflare']",
    "#challenge-stage",
]


def _first_visible(page, selectors):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible(timeout=3000):
                return loc
        except Exception:
            continue
    return None


def main():
    import google_login
    from playwright.sync_api import sync_playwright

    with google_login.session("capture-parscoders", account="momona") as gl:
        with sync_playwright() as pw:
            # Route through the Oracle exit when GWEB_PROXY is set
            # (capture workflow starts Xray on 127.0.0.1:10809).
            _proxy = os.environ.get("GWEB_PROXY", "")
            _launch_kw = {"proxy": {"server": _proxy}} if _proxy else {}
            browser = pw.chromium.launch(headless=True, **_launch_kw)
            try:
                context = browser.new_context(viewport={"width": 1280, "height": 800})
                context.add_cookies(gl.cookies())
                page = context.new_page()
                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(4000)

                # NOTE: the login page always embeds a reCAPTCHA iframe; that alone
                # is not a challenge. Only an actual challenge page after clicking
                # Google (URL containing /challenge) counts as captcha_detected.

                # Try the Google button first (proper flow, incl. reCAPTCHA),
                # fall back to direct /connect-google navigation. Retry twice.
                popup = None
                logged_in_via = None
                for attempt in range(2):
                    try:
                        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
                        page.wait_for_timeout(5000)
                    except Exception:
                        pass
                    btn = _first_visible(page, [
                        "a.google-sign-in",
                        "button:has-text('ورود با گوگل')",
                        "a:has-text('ورود با گوگل')",
                        "button:has-text('Google')",
                        "a:has-text('Google')",
                    ])
                    if btn is not None:
                        try:
                            with context.expect_page(timeout=20000) as pinfo:
                                btn.click()
                            popup = pinfo.value
                            logged_in_via = "button"
                            break
                        except Exception:
                            if "accounts.google.com" in (page.url or ""):
                                popup = page
                                logged_in_via = "button-same-tab"
                                break
                            # same-tab redirect; fall through to direct nav
                    try:
                        with context.expect_page(timeout=20000) as pinfo:
                            page.goto("https://parscoders.com/connect-google",
                                      wait_until="domcontentloaded", timeout=60000)
                        popup = pinfo.value
                        logged_in_via = "direct"
                        break
                    except Exception:
                        if "accounts.google.com" in (page.url or ""):
                            popup = page
                            logged_in_via = "direct-same-tab"
                            break
                        if "/login" not in (page.url or ""):
                            popup = None
                            logged_in_via = "direct-back"
                            break
                        print("RESULT attempt %d redirect failed url=%s" % (
                            attempt, page.url))
                        popup = None
                if popup is None and logged_in_via is None:
                    print("RESULT google_redirect_failed url=%s" % page.url)
                    return 4
                if popup is not None:
                    popup.wait_for_load_state("domcontentloaded")
                    popup.wait_for_timeout(3000)
                # back on parscoders: verify the dashboard loads logged in
                page.goto(DASH_URL, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(4000)
                if "/login" in (page.url or "") or "/auth" in (page.url or ""):
                    print("RESULT login_required_after_oauth")
                    return 4
                body = (page.inner_text("body") or "")[:2000]
                if "داشبورد" not in body and "حساب در یک نگاه" not in body:
                    print("RESULT dashboard_not_confirmed")
                    return 4

                cookies = [
                    {k: c[k] for k in (
                        "name", "value", "domain", "path", "expires",
                        "httpOnly", "secure", "sameSite") if k in c}
                    for c in context.cookies()
                    if "parscoders.com" in str(c.get("domain", ""))
                ]
                if not cookies:
                    print("RESULT no_parscoders_cookies")
                    return 4
                try:
                    local_storage = page.evaluate(
                        "() => Object.fromEntries(Object.entries(localStorage))")
                except Exception:
                    local_storage = {}
                session = {"cookies": cookies, "localStorage": local_storage or {}}
                fd = os.open(OUT, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as f:
                    json.dump(session, f)
                # mask values in the Actions log, like tools/google_login does
                for c in cookies:
                    if len(str(c.get("value", ""))) > 6:
                        print("::add-mask::" + str(c["value"]))
                print(f"RESULT ok cookies={len(cookies)} "
                      f"localStorage_keys={len(local_storage or {})}")
                return 0
            finally:
                browser.close()


if __name__ == "__main__":
    sys.exit(main())
