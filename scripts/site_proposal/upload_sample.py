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


async def find_profile_url(page, home):
    """From the logged-in home page, find the best profile/portfolio link.

    Preference order (probe 2026-11-16): (1) an ADD/MANAGE link (a portfolio
    add page, not a sample display page); (2) any profile/portfolio link that
    is not a /show/ display page; (3) the first profile/portfolio link.
    """
    links = []
    for a in await page.query_selector_all("a[href]"):
        try:
            href = await a.get_attribute("href")
            text = (await a.inner_text()).strip()
        except Exception:
            continue
        if href:
            links.append({"href": href, "text": text[:80]})

    def _abs(href):
        return home.rstrip("/") + href if href.startswith("/") else href

    def _is_profile(l):
        return any(word in l["text"] for word in PROFILE_WORDS)

    def _is_add(l):
        hay = (l["href"] + " " + l["text"]).lower()
        return any(w.lower() in hay for w in ADD_WORDS)

    # (1) add/manage link
    for l in links:
        if _is_profile(l) and _is_add(l):
            return _abs(l["href"]), links
    # (2) profile link that is not a sample display page
    for l in links:
        if _is_profile(l) and "/show/" not in l["href"]:
            return _abs(l["href"]), links
    # (3) first profile link (original behaviour)
    for word in PROFILE_WORDS:
        for l in links:
            if word in l["text"]:
                return _abs(l["href"]), links
    return None, links


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
            links = []
            if not profile_url:
                profile_url, links = await find_profile_url(page, home)
            result["links_found"] = links[:40]
            if not profile_url:
                result["error"] = "could not find a profile/portfolio link from the home page (not logged in?)"
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
