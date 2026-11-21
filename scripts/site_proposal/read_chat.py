#!/usr/bin/env python3
"""Read-only project chat reader (Karlancer + ParsCoders).

Reads the conversation for one project the user already bid on and reports
every message (sender, text, timestamp) as JSON. NEVER sends a message,
NEVER fills or submits any form, NEVER spends tokens.

Reuses the proposal runner's session loading (scripts.site_proposal.runner:
load_site_sessions / sanitize_cookies) so the saved SITE_SESSION_<SITE>
cookies + localStorage are applied exactly like the proposal flow.
For ParsCoders the login check reuses the proposal adapter's
_detect_login_required (scripts.site_proposal.sites.parscoders).

Usage (from the agw-workers checkout, PYTHONPATH=.):
  python3 -m scripts.site_proposal.read_chat --site karlancer \
      --project-url "https://www.karlancer.com/projects/<slug>" \
      --project-title "جذب ادمین پیگیری لید و پاسخگویی به مشتریان" \
      --job-id chat-kar-341835-20261121 \
      --results-dir chat-results

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

try:
    from scripts.site_proposal.sites.parscoders import (  # noqa: E402
        _detect_login_required as _pars_detect_login_required,
    )
except Exception:  # noqa: BLE001
    _pars_detect_login_required = None

CHAT_LINK_TEXT = re.compile(r"گفتگو|چت|پیام|مکالمه|مشاهده.*گفتگو", re.I)
NAV_LINK_TEXT = re.compile(r"پیشنهادهای من|پیشنهادهای ارسالی|پروژه‌های من|پیام|گفتگو|داشبورد|پنل کاربری", re.I)
TIME_RE = re.compile(r"\d{1,2}:\d{2}|\d{4}/\d{1,2}/\d{1,2}|۱۴۰[0-9]|امروز|دیروز", re.I)

SITE_BASE = {
    "karlancer": "https://www.karlancer.com",
    "parscoders": "https://parscoders.com",
}


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


def _is_chat_link(link):
    text = link.get("text") or ""
    href = link.get("href") or ""
    return bool(CHAT_LINK_TEXT.search(text) or re.search(r"chat|message|conversation", href, re.I))


def _project_id_from_url(project_url):
    """Numeric project id from the URL path (e.g. 623370 from /project/623370/...)."""
    try:
        parts = urllib.parse.urlsplit(project_url).path.strip("/").split("/")
        for part in parts:
            if part.isdigit():
                return part
    except Exception:
        pass
    return ""


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


async def _find_chat_url_karlancer(page, project_url, project_title):
    """Karlancer discovery (unchanged from the 2026-11-20 version).

    1. /panel/bids -> bid card/row chat links.
    2. Project page -> chat-ish links.
    3. /panel/messages (and siblings).
    """
    base = SITE_BASE["karlancer"]
    log = []
    slug = ""
    try:
        slug = urllib.parse.urlsplit(project_url).path.rstrip("/").split("-")[-1]
    except Exception:
        pass
    title_frag = (project_title or "")[:20]

    # 1+2. bids page
    await page.goto(f"{base}/panel/bids", wait_until="domcontentloaded")
    await page.wait_for_timeout(4000)
    bids_body = await _body(page)
    log.append({"step": "bids", "url": page.url, "has_title": bool(title_frag and title_frag in bids_body)})
    links = await _links(page)
    chat_links = [l for l in links if _is_chat_link(l)]
    log.append({"step": "bids", "chat_links": chat_links[:20], "n_links": len(links)})
    if title_frag and title_frag in bids_body:
        # links near the project row: prefer a chat link whose href carries the slug
        for l in chat_links:
            if slug and slug in l["href"]:
                return urllib.parse.urljoin(base, l["href"]), log
        if chat_links:
            # single conversation for this bid is the most likely chat link
            # only when the bids page shows exactly one chat link
            if len(chat_links) == 1:
                return urllib.parse.urljoin(base, chat_links[0]["href"]), log

    # 3. project page
    await page.goto(project_url, wait_until="domcontentloaded")
    await page.wait_for_timeout(4000)
    links = await _links(page)
    chat_links = [l for l in links if _is_chat_link(l)]
    log.append({"step": "project", "url": page.url, "chat_links": chat_links[:20]})
    for l in chat_links:
        if l["href"] and not l["href"].startswith(("javascript:", "#")):
            return urllib.parse.urljoin(base, l["href"]), log

    # 4. messages hub
    for hub in (f"{base}/panel/messages",
                f"{base}/panel/chat",
                f"{base}/panel/conversations"):
        await page.goto(hub, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        b = await _body(page)
        log.append({"step": "hub", "url": page.url, "has_title": bool(title_frag and title_frag in b),
                    "is_login": "login" in page.url.lower() or bool(re.search(r"ورود|ثبت نام", b[:500]))})
        if title_frag and title_frag in b:
            links = await _links(page)
            for l in links:
                if slug and slug in l["href"]:
                    return urllib.parse.urljoin(base, l["href"]), log
            break
    return None, log


async def _find_chat_url_parscoders(page, project_url, project_title):
    """Parscoders discovery (new 2026-11-21).

    1. Project page -> chat-ish links (also records the already-bid state).
    2. Homepage nav harvest -> "my proposals / my projects / messages"
       pages -> the row for this project -> its chat link.
    3. Candidate message hubs.
    """
    base = SITE_BASE["parscoders"]
    log = []
    proj_id = _project_id_from_url(project_url)
    title_frag = (project_title or "")[:20]

    # 1. project page (already visited for the login check; revisit for a
    #    clean read and harvest its links)
    await page.goto(project_url, wait_until="domcontentloaded")
    await page.wait_for_timeout(4000)
    body = await _body(page)
    links = await _links(page)
    chat_links = [l for l in links if _is_chat_link(l)]
    log.append({"step": "project", "url": page.url,
                "has_title": bool(title_frag and title_frag in body),
                "already_bid_hint": bool(re.search(r"پیشنهاد شما|پیشنهاد ثبت|پیشنهاد داده", body)),
                "chat_links": chat_links[:20], "n_links": len(links)})
    for l in chat_links:
        href = l.get("href") or ""
        if href and not href.startswith(("javascript:", "#")):
            if (proj_id and proj_id in href) or not proj_id:
                return urllib.parse.urljoin(base, href), log
    if chat_links:
        # a chat link exists on the project page but carries no project id;
        # take the first one (the page is scoped to this project)
        return urllib.parse.urljoin(base, chat_links[0]["href"]), log

    # 2. homepage nav harvest -> my-proposals / messages pages
    await page.goto(base + "/", wait_until="domcontentloaded")
    await page.wait_for_timeout(4000)
    links = await _links(page)
    nav_links = [l for l in links
                 if NAV_LINK_TEXT.search(l.get("text") or "")
                 and (l.get("href") or "").startswith(("/", base))]
    log.append({"step": "home-nav", "url": page.url, "nav_links": nav_links[:20], "n_links": len(links)})
    visited = set()
    for l in nav_links[:4]:
        href = urllib.parse.urljoin(base, l["href"])
        if href in visited or href == base + "/":
            continue
        visited.add(href)
        await page.goto(href, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        b = await _body(page)
        has = bool(title_frag and title_frag in b) or bool(proj_id and proj_id in b)
        sub = await _links(page)
        sub_chat = [x for x in sub if _is_chat_link(x)]
        log.append({"step": "nav-page", "url": page.url, "has_project": has,
                    "chat_links": sub_chat[:20]})
        if has:
            for x in sub_chat:
                if proj_id and proj_id in (x.get("href") or ""):
                    return urllib.parse.urljoin(base, x["href"]), log
            if len(sub_chat) == 1:
                return urllib.parse.urljoin(base, sub_chat[0]["href"]), log
            # row found but no chat link on this page; keep looking
            break

    # 3. candidate message hubs
    for hub in (f"{base}/user/messages",
                f"{base}/panel/messages",
                f"{base}/messages",
                f"{base}/user/panel/messages"):
        await page.goto(hub, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        b = await _body(page)
        has = bool(title_frag and title_frag in b) or bool(proj_id and proj_id in b)
        log.append({"step": "hub", "url": page.url, "has_project": has,
                    "is_login": "login" in page.url.lower() or bool(re.search(r"ورود|ثبت نام", b[:500])),
                    "body_excerpt": b[:600]})
        if has:
            links = await _links(page)
            for l in links:
                if proj_id and proj_id in (l.get("href") or "") and _is_chat_link(l):
                    return urllib.parse.urljoin(base, l["href"]), log
            break
    return None, log


async def _find_chat_url(page, site, project_url, project_title):
    if site == "parscoders":
        return await _find_chat_url_parscoders(page, project_url, project_title)
    return await _find_chat_url_karlancer(page, project_url, project_title)


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

                    # --- login check (per site) ---
                    if site == "parscoders":
                        await page.goto(project_url, wait_until="domcontentloaded")
                        await page.wait_for_timeout(5000)
                        proj_body = await _body(page)
                        login_required = False
                        if _pars_detect_login_required is not None:
                            try:
                                login_required = await _pars_detect_login_required(page)
                            except Exception:
                                login_required = False
                        else:
                            login_required = bool(
                                re.search(r"برای ثبت پیشنهاد وارد شوید|ورود به حساب کاربری", proj_body)
                                or await page.locator("input[type='password']").count())
                        if login_required:
                            result["status"] = "login_required"
                            result["error_message"] = "Parscoders session expired (login prompt on project page)"
                            result["dash_url"] = page.url
                            result["dash_body_excerpt"] = proj_body[:1500]
                            result["dash_has_login_form"] = bool(await page.locator("input[type='password']").count())
                            try:
                                await page.screenshot(path=str(jobs_dir / "dashboard.png"), full_page=True)
                                result["screenshot_path"] = f"jobs/{job_id}/dashboard.png"
                            except Exception:
                                pass
                            return result
                        result["logged_in"] = True
                        result["project_body_excerpt"] = proj_body[:1500]
                    else:
                        # Karlancer: login check via dashboard (same signal as
                        # the proposal adapter)
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

                    chat_url, discovery = await _find_chat_url(page, site, project_url, project_title)
                    result["discovery"] = discovery
                    if not chat_url:
                        result["status"] = "not_found"
                        result["error_message"] = "No conversation link found for this project (project page / nav / message hubs checked)"
                        return result
                    result["chat_url"] = chat_url

                    await page.goto(chat_url, wait_until="domcontentloaded")
                    await page.wait_for_timeout(5000)
                    # If the link opened a modal instead of a page, the URL
                    # stays on the listing page; messages are still extractable.
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
    p = argparse.ArgumentParser(description="Read-only project chat messages (karlancer / parscoders)")
    p.add_argument("--site", required=True, help="Site name (karlancer or parscoders)")
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
