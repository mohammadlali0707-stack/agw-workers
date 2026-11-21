#!/usr/bin/env python3
"""Read-only Karlancer project chat (one-off check, 2026-11-20).

Reads the conversation for one project the user already bid on and reports
every message (sender, text, timestamp) as JSON. NEVER sends a message,
NEVER fills or submits any form, NEVER spends tokens.

Reuses the proposal runner's session loading (scripts.site_proposal.runner:
load_site_sessions / sanitize_cookies) so the saved SITE_SESSION_KARLANCER
cookies + localStorage are applied exactly like the proposal flow.

Usage (from the agw-workers checkout, PYTHONPATH=.):
  python3 -m scripts.site_proposal.read_chat --site karlancer \
      --project-url "https://www.karlancer.com/projects/<slug>" \
      --project-title "جذب ادمین پیگیری لید و پاسخگویی به مشتریان" \
      --job-id chat-kar-341835-20261120 \
      --results-dir hq-ai-results

Writes <results-dir>/jobs/<job-id>/result.json (+ chat.png screenshot).
"""
import argparse
import asyncio
import json
import re
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")
from scripts.site_proposal.runner import (  # noqa: E402
    load_site_sessions,
    mask_secrets,
    sanitize_cookies,
)

CHAT_LINK_TEXT = re.compile(r"گفتگو|چت|پیام|مکالمه|مشاهده.*گفتگو", re.I)
TIME_RE = re.compile(r"\d{1,2}:\d{2}|\d{4}/\d{1,2}/\d{1,2}|۱۴۰[0-9۰-۹]|امروز|دیروز", re.I)


async def _body(page):
    try:
        res = await page.evaluate("() => document.body.innerText")
        return res if isinstance(res, str) else ""
    except Exception:
        return ""


async def _links(page):
    """All links on the page: [{href, text}]."""
    try:
        res = await page.evaluate(
            """() => [...document.querySelectorAll('a[href]')].map(a => ({
                href: a.getAttribute('href') || '',
                text: (a.innerText || '').trim().slice(0, 120)}))"""
        )
        return res if isinstance(res, list) else []
    except Exception:
        return []


async def _extract_messages(page):
    """Best-effort structured extraction of chat messages from the page.

    Returns a list of {text, time, side, cls}. Falls back to [] when the
    page has no recognisable message bubbles; the caller keeps the full
    body text as well so nothing is lost.
    """
    js = """() => {
      const out = [];
      const sels = ["[class*='message']", "[class*='chat-bubble']", "[class*='bubble']",
                    "[class*='msg-']", "[class*='conversation'] [class*='item']"];
      const seen = new Set();
      for (const sel of sels) {
        for (const el of document.querySelectorAll(sel)) {
          const t = (el.innerText || '').trim();
          if (!t || t.length < 2 || t.length > 4000) continue;
          // skip containers that merely wrap several bubbles
          if (el.querySelector(sel)) continue;
          if (seen.has(t)) continue;
          seen.add(t);
          const cls = (el.className || '').toString();
          const timeEl = el.querySelector('time, [class*="time"], [class*="date"]');
          out.push({text: t.slice(0, 2000),
                    time: timeEl ? (timeEl.innerText || '').trim().slice(0, 80) : '',
                    side: /me|own|sent|sender|right|left/.test(cls) ? cls.slice(0, 120) : '',
                    cls: cls.slice(0, 120)});
        }
      }
      return out;
    }"""
    try:
        res = await page.evaluate(js)
        return res if isinstance(res, list) else []
    except Exception:
        return []


async def _find_chat_url(page, project_url, project_title):
    """Discover the conversation URL for this project.

    Strategy (all GET navigations / link inspections only):
      1. /panel/bids -> find the bid card/row for this project, take its
         chat/message links.
      2. Any chat-ish link on /panel/bids.
      3. Project page -> chat-ish links.
      4. /panel/messages (if it exists / is not a login redirect).
    Returns (chat_url_or_None, discovery_log).
    """
    log = []
    slug = ""
    try:
        slug = urllib.parse.urlsplit(project_url).path.rstrip("/").split("-")[-1]
    except Exception:
        pass
    title_frag = (project_title or "")[:20]

    # 1+2. bids page
    await page.goto("https://www.karlancer.com/panel/bids", wait_until="domcontentloaded")
    await page.wait_for_timeout(4000)
    bids_body = await _body(page)
    log.append({"step": "bids", "url": page.url, "has_title": bool(title_frag and title_frag in bids_body)})
    links = await _links(page)
    chat_links = [l for l in links if CHAT_LINK_TEXT.search(l["text"] or "") or re.search(r"chat|message|conversation", l["href"], re.I)]
    log.append({"step": "bids", "chat_links": chat_links[:20], "n_links": len(links)})
    if title_frag and title_frag in bids_body:
        # links near the project row: prefer a chat link whose href carries the slug
        for l in chat_links:
            if slug and slug in l["href"]:
                return urllib.parse.urljoin("https://www.karlancer.com", l["href"]), log
        if chat_links:
            # single conversation for this bid is the most likely chat link
            # only when the bids page shows exactly one chat link
            if len(chat_links) == 1:
                return urllib.parse.urljoin("https://www.karlancer.com", chat_links[0]["href"]), log

    # 3. project page
    await page.goto(project_url, wait_until="domcontentloaded")
    await page.wait_for_timeout(4000)
    links = await _links(page)
    chat_links = [l for l in links if CHAT_LINK_TEXT.search(l["text"] or "") or re.search(r"chat|message|conversation", l["href"], re.I)]
    log.append({"step": "project", "url": page.url, "chat_links": chat_links[:20]})
    for l in chat_links:
        if l["href"] and not l["href"].startswith(("javascript:", "#")):
            return urllib.parse.urljoin("https://www.karlancer.com", l["href"]), log

    # 4. messages hub
    for hub in ("https://www.karlancer.com/panel/messages",
                "https://www.karlancer.com/panel/chat",
                "https://www.karlancer.com/panel/conversations"):
        await page.goto(hub, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        b = await _body(page)
        log.append({"step": "hub", "url": page.url, "has_title": bool(title_frag and title_frag in b),
                    "is_login": "login" in page.url.lower() or bool(re.search(r"ورود|ثبت نام", b[:500]))})
        if title_frag and title_frag in b:
            links = await _links(page)
            for l in links:
                if slug and slug in l["href"]:
                    return urllib.parse.urljoin("https://www.karlancer.com", l["href"]), log
            break
    return None, log


async def run(site, project_url, project_title, job_id, results_dir):
    jobs_dir = Path(results_dir) / "jobs" / job_id
    jobs_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "job_id": job_id, "site": site, "project_url": project_url,
        "mode": "read_chat", "status": "error", "logged_in": False,
        "chat_url": None, "messages": [], "body_excerpt": "",
        "discovery": [], "error_message": None,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    session, session_err = load_site_sessions(site)
    if session_err:
        result["status"] = "login_required"
        result["error_message"] = mask_secrets(session_err)
        (jobs_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(viewport={"width": 1280, "height": 800})
                try:
                    cookies = session.get("cookies", [])
                    if cookies:
                        target_domain = urllib.parse.urlparse(project_url).hostname
                        clean = sanitize_cookies(cookies, default_domain=target_domain)
                        if clean:
                            await context.add_cookies(clean)
                    ls_data = session.get("localStorage", {})
                    if isinstance(ls_data, dict) and ls_data:
                        ls_json = json.dumps(ls_data)
                        await context.add_init_script(
                            f"""(() => {{ try {{ const data = {ls_json};
                            for (const [k, v] of Object.entries(data)) {{
                                window.localStorage.setItem(k, typeof v === 'string' ? v : JSON.stringify(v)); }}
                            }} catch (e) {{}} }})();"""
                        )
                    page = await context.new_page()

                    # login check via dashboard (same signal as the proposal adapter)
                    await page.goto("https://www.karlancer.com/panel/dashboard", wait_until="domcontentloaded")
                    await page.wait_for_timeout(5000)
                    dash = await _body(page)
                    if "خروج" not in dash:
                        result["status"] = "login_required"
                        result["error_message"] = "Karlancer session expired (no logout link on dashboard)"
                        result["dash_url"] = page.url
                        result["dash_body_excerpt"] = dash[:1500]
                        result["dash_has_login_form"] = bool(await page.locator("input[type='password']").count())
                        try:
                            await page.screenshot(path=str(jobs_dir / "dashboard.png"), full_page=True)
                            result["screenshot_path"] = f"jobs/{job_id}/dashboard.png"
                        except Exception:
                            pass
                        return result
                    result["logged_in"] = True

                    chat_url, discovery = await _find_chat_url(page, project_url, project_title)
                    result["discovery"] = discovery
                    if not chat_url:
                        result["status"] = "not_found"
                        result["error_message"] = "No conversation link found for this project (bids/project/messages checked)"
                        return result
                    result["chat_url"] = chat_url

                    await page.goto(chat_url, wait_until="domcontentloaded")
                    await page.wait_for_timeout(5000)
                    # If the link opened a modal instead of a page, the URL
                    # stays on the bids page; messages are still extractable.
                    msgs = await _extract_messages(page)
                    body = await _body(page)
                    result["messages"] = msgs
                    result["body_excerpt"] = body[:8000]
                    result["chat_page_url"] = page.url
                    try:
                        await page.screenshot(path=str(jobs_dir / "chat.png"), full_page=True)
                        result["screenshot_path"] = f"jobs/{job_id}/chat.png"
                    except Exception:
                        pass
                    result["status"] = "ok"
                    return result
                finally:
                    await context.close()
            finally:
                await browser.close()
    except Exception as e:  # noqa: BLE001
        result["status"] = "error"
        result["error_message"] = mask_secrets(f"{type(e).__name__}: {e}")[:400]
        return result
    finally:
        (jobs_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Read-only Karlancer project chat messages")
    p.add_argument("--site", required=True, help="Site name (e.g. karlancer)")
    p.add_argument("--project-url", required=True, help="Target project URL")
    p.add_argument("--project-title", default="", help="Project title for bid matching")
    p.add_argument("--job-id", required=True, help="Unique job ID")
    p.add_argument("--results-dir", default="hq-ai-results", help="Directory for output results")
    return p.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    res = asyncio.run(run(a.site, a.project_url, a.project_title, a.job_id, a.results_dir))
    print(json.dumps(res, ensure_ascii=False)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
