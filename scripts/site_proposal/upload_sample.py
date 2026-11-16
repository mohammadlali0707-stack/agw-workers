#!/usr/bin/env python3
"""Upload a portfolio sample to a freelance profile (ParsCoders / KarLancer).

Runs on the GitHub runner with Playwright, using the same pre-authenticated
session as the proposal runner (SITE_SESSION_<SITE> secret, cookies +
localStorage). Same job/result pattern as scripts/site_proposal/runner.py:
the caller puts a job in hq-ai-results, dispatches profile-sample-upload.yml,
and reads jobs/<job_id>/result.json back.

Modes:
  probe  — find the profile/portfolio page, screenshot it, dump the form
           structure. Submits NOTHING. Run this first on a new site/profile:
           the exact portfolio form is UNVERIFIED until probe reports it.
  upload — fill the portfolio form (title, description, sample link) and
           submit, then re-load the profile and verify the sample appears.

Usage (on the runner):
    python3 -m scripts.site_proposal.upload_sample \
        --site parscoders --sample-url <url> --title <t> --description <d> \
        --mode probe --job-id <jid> --results-dir hq-ai-results [--screenshot <png>]

Exit 0 when a result.json was written (even on failure — the failure is IN the
result); exit 1 only when nothing could be written at all.
"""
import argparse
import asyncio
import json
import os
import sys
import time

SITE_HOME = {"parscoders": "https://parscoders.com", "karlancer": "https://karlancer.com"}
PROFILE_WORDS = ("پروفایل", "نمونه کار", "نمونه‌کار", "portfolio", "پروفایل من", "سوابق")
# Words that mark a portfolio ADD/MANAGE link (as opposed to a sample display page).
# Probe 2026-11-16 found the old logic returning a /resume/portfolio/show/<id>/ display
# page (an existing sample), whose only form is a feedback form — no title field.
ADD_WORDS = ("add", "create", "new", "manage", "edit", "افزودن", "ایجاد", "جدید", "مدیریت", "ویرایش", "اضافه")
# Account holder's name — the logged-in header shows it (proven by the proposal
# preview screenshot, run 43859946135: «محمد لالی» in the header). Used to find
# the username/panel link and as a positive logged-in signal.
USERNAME = "محمد"
# Words that mark the logged-in user panel/dashboard link.
PANEL_WORDS = ("panel", "dashboard", "پنل", "داشبورد", "کاربری", "my-account")
TITLE_WORDS = ("عنوان", "title", "نام نمونه")
DESC_WORDS = ("توضیح", "شرح", "description", "توضیحات")
LINK_WORDS = ("لینک", "آدرس", "url", "link", "پیوند")


def load_session(site):
    """Mirror of runner.py: per-site env wins, else SITE_SESSIONS. -> (session, error)."""
    one = os.environ.get("SITE_SESSION_" + site.strip().upper(), "").strip()
    if one:
        try:
            sess = json.loads(one)
            if isinstance(sess, dict) and (sess.get("cookies") or sess.get("localStorage")):
                return sess, None
        except Exception:
            return {}, "SITE_SESSION_<SITE> is not valid JSON"
    raw = os.environ.get("SITE_SESSIONS", "").strip()
    if not raw:
        return {}, "SITE_SESSIONS environment variable is not configured"
    try:
        root = json.loads(raw)
    except Exception:
        return {}, "Failed to parse SITE_SESSIONS JSON"
    if not isinstance(root, dict):
        return {}, "SITE_SESSIONS root must be a JSON object"
    sess = root.get(site)
    if not isinstance(sess, dict) or not (sess.get("cookies") or sess.get("localStorage")):
        return {}, f"No session found for site '{site}' in SITE_SESSIONS"
    return sess, None


def sanitize_cookies(cookies, default_domain=None):
    """Clean cookie dicts for Playwright context.add_cookies (mirror of runner.py)."""
    out = []
    now = time.time()
    for c in cookies or []:
        if not isinstance(c, dict) or not c.get("name") or c.get("value") is None:
            continue
        if c.get("expires") and c["expires"] < now:
            continue
        nc = {"name": c["name"], "value": str(c["value"]),
              "domain": c.get("domain") or default_domain,
              "path": c.get("path") or "/"}
        if c.get("secure") is not None:
            nc["secure"] = bool(c["secure"])
        if c.get("httpOnly") is not None:
            nc["httpOnly"] = bool(c["httpOnly"])
        if c.get("sameSite") in ("Strict", "Lax", "None"):
            nc["sameSite"] = c["sameSite"]
        out.append(nc)
    return out


async def dump_forms(page):
    """Return a JSON-able description of every form on the page."""
    forms = []
    for form in await page.query_selector_all("form"):
        fields = []
        for el in await form.query_selector_all("input, textarea, select"):
            tag = await el.evaluate("e => e.tagName.toLowerCase()")
            info = {"tag": tag,
                    "type": await el.get_attribute("type"),
                    "name": await el.get_attribute("name"),
                    "placeholder": await el.get_attribute("placeholder"),
                    "id": await el.get_attribute("id")}
            label = await el.evaluate(
                "e => { const l = e.closest('label'); if (l) return l.innerText.trim().slice(0,80);"
                " const id = e.id; if (id) { const lab = document.querySelector(`label[for='${id}']`);"
                " if (lab) return lab.innerText.trim().slice(0,80); } return ''; }")
            info["label"] = label
            fields.append(info)
        buttons = []
        for b in await form.query_selector_all("button, input[type=submit]"):
            buttons.append((await b.inner_text()).strip()[:60] or (await b.get_attribute("value") or ""))
        forms.append({"fields": fields, "buttons": buttons})
    return forms


async def collect_links(page):
    """All <a href> links on the page, with text and whether inside <header>."""
    try:
        return await page.evaluate(
            """() => [...document.querySelectorAll('a[href]')].map(a => ({
                href: a.getAttribute('href') || '',
                text: (a.innerText || '').trim().slice(0, 80),
                in_header: !!a.closest('header')
            })).filter(l => l.href)""")
    except Exception:
        return []


async def detect_logged_in(page, links):
    """Logged-in indicator, so a probe can tell logged-in from logged-out
    instead of inferring it from footer links (probe 2026-11-16 misread this:
    the session was valid all along — the proposal preview proved it).
    Positive signals: the username in the header, a logout link. Negative:
    a login/register prompt or a /login URL."""
    header_text = ""
    try:
        header_text = await page.evaluate(
            "() => { const h = document.querySelector('header'); return h ? h.innerText : ''; }") or ""
    except Exception:
        pass
    url = page.url or ""
    username_in_header = USERNAME in header_text
    logout = any(("خروج" in (l.get("text") or "")) or ("logout" in (l.get("href") or "").lower()) for l in links)
    login_prompt = any(("ورود" in (l.get("text") or "")) or ("ثبت نام" in (l.get("text") or ""))
                    or ("ثبت‌نام" in (l.get("text") or "")) for l in links) or ("/login" in url.lower())
    return {"username_in_header": username_in_header, "logout_link": logout,
            "login_prompt": login_prompt, "url": url,
            "logged_in": bool(username_in_header or logout) and not login_prompt}


def _abs_url(home, href):
    return home.rstrip("/") + href if href.startswith("/") else href


def _is_profile(l):
    return any(word in (l.get("text") or "") for word in PROFILE_WORDS)


def _is_add(l):
    hay = ((l.get("href") or "") + " " + (l.get("text") or "")).lower()
    return any(w.lower() in hay for w in ADD_WORDS)


def _pick_add_link(links, home):
    """A portfolio ADD link: portfolio-ish (text or href) + add word, never a
    /show/ display page."""
    for l in links:
        href = (l.get("href") or "").lower()
        if "/show/" in href:
            continue
        portfolioish = _is_profile(l) or ("portfolio" in href) or ("نمونه" in href)
        if portfolioish and _is_add(l):
            return _abs_url(home, l["href"])
    return None


def _pick_panel_link(links, home):
    """The logged-in user panel/dashboard URL: the username link (header first),
    or a link whose href/text mentions panel/dashboard. Returns (url, is_dropdown):
    is_dropdown True when the username link is a menu trigger (href '#'/javascript)
    that must be clicked before its menu links exist."""
    is_dropdown = False
    for prefer_header in (True, False):
        for l in links:
            if prefer_header and not l.get("in_header"):
                continue
            if USERNAME in (l.get("text") or ""):
                href = (l.get("href") or "").strip()
                if href and href not in ("#", "javascript:void(0)", "javascript:;", "javascript:"):
                    return _abs_url(home, href), False
                is_dropdown = True
    for l in links:
        hay = ((l.get("href") or "") + " " + (l.get("text") or "")).lower()
        if any(w.lower() in hay for w in PANEL_WORDS):
            return _abs_url(home, l["href"]), is_dropdown
    return None, is_dropdown


async def find_profile_url(page, home):
    """Find the portfolio ADD page.

    The add-sample link lives in the logged-in user panel/dashboard, not on the
    home page (probes 2026-11-16: the home page only links to a /show/ display
    page and the public /skill-portfolio listing). So: (1) an add link on the
    home page if one exists; (2) go to the panel (via the username/header link,
    opening its dropdown menu first if it is one) and search for the add link
    there; (3) fall back to any non-/show/ profile link on the home page, then
    the first profile link. Returns (url, info) where info carries the
    logged-in indicator, the panel URL and the link lists. No URL is ever
    guessed — every URL comes from a link on the actual logged-in page.
    """
    info = {"panel_url": None, "logged_in": {}, "home_links": [], "panel_links": []}
    home_links = await collect_links(page)
    info["home_links"] = home_links
    info["logged_in"] = await detect_logged_in(page, home_links)

    # (1) add link right on the home page
    add = _pick_add_link(home_links, home)
    if add:
        return add, info

    # (2) panel: find its URL, or open the username dropdown and re-collect
    panel_url, is_dropdown = _pick_panel_link(home_links, home)
    if is_dropdown:
        try:
            loc = page.locator("header a:has-text('%s')" % USERNAME)
            if await loc.count() == 0:
                loc = page.locator("a:has-text('%s')" % USERNAME)
            if await loc.count() > 0:
                await loc.first.click(timeout=5000)
                await page.wait_for_timeout(1500)
                menu_links = await collect_links(page)
                info["menu_links"] = menu_links
                add = _pick_add_link(menu_links, home)
                if add:
                    return add, info
                panel_url, _ = _pick_panel_link(menu_links, home)
        except Exception as e:  # noqa: BLE001 - record and fall through
            info["menu_error"] = f"{type(e).__name__}: {e}"
    if panel_url:
        info["panel_url"] = panel_url
        try:
            await page.goto(panel_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2000)
            panel_links = await collect_links(page)
            info["panel_links"] = panel_links
            add = _pick_add_link(panel_links, home)
            if add:
                return add, info
            info["panel_note"] = "panel reached but no add-sample link found there"
        except Exception as e:  # noqa: BLE001 - record and fall through
            info["panel_error"] = f"{type(e).__name__}: {e}"
        # fall through to home-page fallbacks; reload home for a fresh link list
        try:
            await page.goto(home, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2000)
            home_links = await collect_links(page)
            info["home_links"] = home_links
        except Exception:
            pass

    # (3) any profile link that is not a /show/ display page
    for l in home_links:
        if _is_profile(l) and "/show/" not in (l.get("href") or ""):
            return _abs_url(home, l["href"]), info
    # (4) first profile link (original behaviour)
    for word in PROFILE_WORDS:
        for l in home_links:
            if word in (l.get("text") or ""):
                return _abs_url(home, l["href"]), info
    return None, info


def pick_field(fields, words):
    for f in fields:
        hay = " ".join(str(f.get(k) or "") for k in ("name", "placeholder", "label", "id")).lower()
        if any(w.lower() in hay for w in words):
            return f
    return None


async def fill_field(page, field, value):
    sel = None
    if field.get("id"):
        sel = "#" + field["id"]
    elif field.get("name"):
        sel = f"[name='{field['name']}']"
    if not sel:
        return False
    try:
        await page.fill(sel, value, timeout=10000)
        return True
    except Exception:
        return False


async def main_async(args):
    result = {"ok": False, "mode": args.mode, "site": args.site, "submitted": False,
              "job_id": args.job_id, "sample_url": args.sample_url}
    out_dir = os.path.join(args.results_dir, "jobs", args.job_id)
    os.makedirs(out_dir, exist_ok=True)

    def save():
        with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        return 0

    session, err = load_session(args.site)
    if err:
        result["error"] = err
        return save()
    home = SITE_HOME[args.site]
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        result["error"] = "playwright is not installed"
        return save()
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            ctx = await browser.new_context()
            cookies = sanitize_cookies(session.get("cookies"), home.split("//")[1])
            if cookies:
                await ctx.add_cookies(cookies)
            page = await ctx.new_page()
            await page.goto(home, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2000)
            for k, v in (session.get("localStorage") or {}).items():
                try:
                    await page.evaluate("(kv) => localStorage.setItem(kv[0], kv[1])", [k, v])
                except Exception:
                    pass
            await page.reload(wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            profile_url = args.profile_url
            info = {}
            if not profile_url:
                profile_url, info = await find_profile_url(page, home)
            result["links_found"] = (info.get("home_links") or [])[:40]
            result["logged_in"] = info.get("logged_in", {})
            if info.get("panel_url"):
                result["panel_url"] = info["panel_url"]
            if info.get("panel_links"):
                result["panel_links"] = info["panel_links"][:40]
            if info.get("menu_links"):
                result["menu_links"] = info["menu_links"][:40]
            for key in ("panel_note", "panel_error", "menu_error"):
                if info.get(key):
                    result[key] = info[key]
            if not profile_url:
                li = info.get("logged_in", {})
                result["error"] = (
                    "could not find a profile/portfolio link"
                    + (" (not logged in)" if not li.get("logged_in") else
                       " (logged in, but no portfolio link found on home page or panel)")
                )
                await page.screenshot(path=os.path.join(out_dir, "home.png"))
                return save()
            result["profile_url"] = profile_url
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2000)
            await page.screenshot(path=os.path.join(out_dir, "profile.png"))
            forms = await dump_forms(page)
            result["forms"] = forms
            with open(os.path.join(out_dir, "forms.json"), "w", encoding="utf-8") as f:
                json.dump(forms, f, ensure_ascii=False, indent=2)
            if args.mode == "probe":
                result["ok"] = True
                result["note"] = "probe only: form structure recorded, nothing submitted"
                return save()
            # ---- upload mode: capture the sample page itself for an image field ----
            sample_png = os.path.join(out_dir, "sample.png")
            try:
                shot = await ctx.new_page()
                await shot.goto(args.sample_url, wait_until="domcontentloaded", timeout=60000)
                await shot.wait_for_timeout(2500)
                await shot.screenshot(path=sample_png)
                await shot.close()
                result["sample_screenshot"] = True
            except Exception:
                result["sample_screenshot"] = False
            # ---- upload mode: fill the portfolio form ----
            target = None
            for form in forms:
                if pick_field(form["fields"], TITLE_WORDS):
                    target = form
                    break
            if not target:
                result["error"] = "no portfolio form with a title field found on the profile page"
                return save()
            fields = target["fields"]
            filled = {}
            tf = pick_field(fields, TITLE_WORDS)
            df = pick_field(fields, DESC_WORDS)
            lf = pick_field(fields, LINK_WORDS)
            if tf:
                filled["title"] = await fill_field(page, tf, args.title)
            if df:
                filled["description"] = await fill_field(page, df, args.description)
            if lf:
                filled["link"] = await fill_field(page, lf, args.sample_url)
            result["filled"] = filled
            if args.screenshot and os.path.isfile(args.screenshot):
                shot_file = args.screenshot
            elif os.path.isfile(sample_png):
                shot_file = sample_png
            else:
                shot_file = None
            if shot_file:
                for f in fields:
                    if (f.get("type") or "") == "file":
                        try:
                            sel = ("#" + f["id"]) if f.get("id") else (f"[name='{f['name']}']" if f.get("name") else None)
                            if sel:
                                await page.set_input_files(sel, shot_file)
                                filled["image"] = True
                        except Exception:
                            filled["image"] = False
            if not any(filled.values()):
                result["error"] = "could not fill any portfolio field"
                return save()
            submitted = False
            for sel in ("button[type=submit]", "input[type=submit]", "button:has-text('ثبت')", "button:has-text('ذخیره')"):
                try:
                    await page.click(sel, timeout=5000)
                    submitted = True
                    break
                except Exception:
                    continue
            result["submitted"] = submitted
            if not submitted:
                result["error"] = "could not click a submit button"
                return save()
            await page.wait_for_timeout(4000)
            await page.screenshot(path=os.path.join(out_dir, "after_submit.png"))
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2000)
            body = await page.inner_text("body")
            result["verified"] = args.title in body
            result["ok"] = bool(result["verified"])
            if not result["ok"]:
                result["error"] = "submitted but the sample title was not found on the reloaded profile"
            return save()
    except Exception as e:  # noqa: BLE001 - every failure must land in result.json
        result["error"] = f"{type(e).__name__}: {e}"
        try:
            return save()
        except Exception:
            return 1
    return save()


def main():
    ap = argparse.ArgumentParser(description="Upload a portfolio sample to a freelance profile.")
    ap.add_argument("--site", required=True, choices=list(SITE_HOME))
    ap.add_argument("--sample-url", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--description", default="")
    ap.add_argument("--mode", required=True, choices=["probe", "upload"])
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--results-dir", required=True)
    ap.add_argument("--profile-url", default="")
    ap.add_argument("--screenshot", default="")
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
