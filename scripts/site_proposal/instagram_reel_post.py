#!/usr/bin/env python3
"""Post an Instagram Reel from a saved session (Playwright on a GitHub runner).

PRIVACY RULE (user order, 2026-11-18 — non-negotiable):
  The Instagram session is used ONLY for posting his Reels — nothing else on
  his Instagram account, ever. No reading DMs or messages, no reading the
  feed, no follows, likes, comments, or browsing his account, no profile
  changes. The session file/secret stays completely private: stored sealed as
  a GitHub secret (SITE_SESSION_INSTAGRAM), never printed, never logged,
  never copied anywhere else, never shared with anyone.
  This script performs ONLY: (1) a login check on instagram.com, (2) the
  Reel upload flow (probe = inspect only, post = publish). It navigates to
  no other Instagram page — see _goto_allowed() for the enforced allowlist
  (home, the create dialog, the published post URL for verification).
  Any navigation outside that list is refused by the code, not by convention.

AGENT-INDEPENDENCE: this file + instagram-reel-post.yml + README_instagram.md
in this repo are the whole system. No Muse knowledge is needed to run it:
see README_instagram.md (runbook) in the same directory.

Runs on the GitHub runner with Playwright, mirroring the freelance site
automation (scripts/site_proposal/runner.py + upload_sample.py):
  - session from GitHub secret SITE_SESSION_INSTAGRAM (per-site wins) or
    SITE_SESSIONS["instagram"]: {"cookies": [...], "localStorage": {...}},
    injected into Playwright via context.add_cookies + add_init_script.
    No username/password anywhere in the automation.
  - same job/result pattern: the caller dispatches instagram-reel-post.yml
    with a job_id; this script writes jobs/<job_id>/result.json into the
    checked-out hq-ai-results repo and the workflow commits + pushes it.

Modes:
  probe (default) — verify the session logs in, open the Create dialog,
    attach the video file, and REPORT what the uploader says (duration it
    shows, any length limit / error text, whether a Reel choice appears),
    then close the dialog. Submits NOTHING. Run this first: Instagram's
    web UI and its length limits are UNVERIFIED until a live probe reports.
  post — the full flow: upload -> Next -> caption -> Share -> verify the
    post URL. ONLY runs when dry_run is false AND mode is post. Anything
    else refuses with status "dry_run" before any browser work.

  dry_run (default true) is a second, independent gate: even mode=post
  refuses to publish while dry_run is true.

Usage (on the runner):
    python3 -m scripts.site_proposal.instagram_reel_post \
        --video-url <https://...> --caption <text> --job-id <jid> \
        --mode probe --dry-run true --results-dir hq-ai-results

Exit 0 when a result.json was written (even on failure — the failure is IN
the result); exit 1 only when nothing could be written at all.
"""
import argparse
import asyncio
import json
import os
import re
import struct
import sys
import time
import urllib.request

HOME = "https://www.instagram.com/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# --- navigation allowlist (privacy rule). Nothing else is ever opened. ---
# home page (login check + create dialog live here), the published post URL
# (verification after Share), and the owner's /reels/ page (fallback check
# that the Reel exists). DMs (/direct), feed (/explore), following lists,
# settings (/accounts/edit) and every other path are refused.
_ALLOWED_RE = re.compile(
    r"^https://www\.instagram\.com/(?:$|reel/[\w-]+/?$|p/[\w-]+/?$|[\w.\-]+/reels/?$)")


def _goto_allowed(url):
    return bool(_ALLOWED_RE.match(url or ""))


# ---------------------------------------------------------------------------
# session handling (mirror of runner.py / upload_sample.py)
# ---------------------------------------------------------------------------
def load_session(site="instagram"):
    """Per-site env wins, else SITE_SESSIONS. -> (session, error)."""
    one = os.environ.get("SITE_SESSION_" + site.strip().upper(), "").strip()
    if one:
        try:
            sess = json.loads(one)
            if isinstance(sess, dict) and (sess.get("cookies") or sess.get("localStorage")):
                return sess, None
        except Exception:
            return {}, "SITE_SESSION_INSTAGRAM is not valid JSON"
    raw = os.environ.get("SITE_SESSIONS", "").strip()
    if not raw:
        return {}, "no Instagram session configured"
    try:
        root = json.loads(raw)
    except Exception:
        return {}, "Failed to parse SITE_SESSIONS JSON"
    if not isinstance(root, dict):
        return {}, "SITE_SESSIONS root must be a JSON object"
    sess = root.get(site)
    if not isinstance(sess, dict) or not (sess.get("cookies") or sess.get("localStorage")):
        return {}, "no Instagram session configured"
    return sess, None


def sanitize_cookies(cookies, default_domain=".instagram.com"):
    """Clean cookie dicts for Playwright context.add_cookies.

    Mirrors upload_sample.py exactly, including the deliberate choice NOT to
    drop cookies whose `expires` is in the past: session cookies are stored
    with expires=-1, and an expiry filter would drop the very session cookie
    the site needs.
    """
    out = []
    for c in cookies or []:
        if not isinstance(c, dict):
            continue
        clean = {}
        for key in ("name", "value", "url", "domain", "path", "expires",
                    "httpOnly", "secure", "sameSite"):
            if key in c and c[key] is not None:
                clean[key] = c[key]
        if "name" not in clean or "value" not in clean:
            continue
        clean["name"] = str(clean["name"])
        clean["value"] = str(clean["value"])
        if "sameSite" in clean:
            s = str(clean["sameSite"]).capitalize()
            if s in ("Strict", "Lax", "None"):
                clean["sameSite"] = s
            else:
                del clean["sameSite"]
        if "expires" in clean:
            try:
                clean["expires"] = float(clean["expires"])
            except (ValueError, TypeError):
                del clean["expires"]
        if "url" not in clean:
            clean.setdefault("path", "/")
            if "domain" not in clean and default_domain:
                clean["domain"] = default_domain
        out.append(clean)
    return out


# ---------------------------------------------------------------------------
# secret masking (never print/log a cookie value, token, or password)
# ---------------------------------------------------------------------------
def _secret_values(session):
    vals = []
    for c in (session or {}).get("cookies", []) or []:
        if isinstance(c, dict) and c.get("value") and len(str(c["value"])) > 4:
            vals.append(str(c["value"]))
    for v in ((session or {}).get("localStorage", {}) or {}).values():
        if v and len(str(v)) > 4:
            vals.append(str(v))
    return vals


class Masker:
    def __init__(self, session):
        self.values = sorted(set(_secret_values(session)), key=len, reverse=True)

    def __call__(self, text):
        if not text:
            return text
        out = str(text)
        for s in self.values:
            if s and len(s) > 3:
                out = out.replace(s, "[REDACTED]")
        out = re.sub(r"(bearer\s+)[A-Za-z0-9_\-\.]{8,}", r"\1[REDACTED]", out, flags=re.I)
        out = re.sub(r"((?:[?&]|\\b)(?:token|auth|key|secret|password|access_token|cookie)=)[^& \t\n\r]+",
                     r"\1[REDACTED]", out, flags=re.I)
        return out


def mask_obj(data, mask):
    if isinstance(data, str):
        return mask(data)
    if isinstance(data, dict):
        return {k: mask_obj(v, mask) for k, v in data.items()}
    if isinstance(data, list):
        return [mask_obj(v, mask) for v in data]
    return data


# ---------------------------------------------------------------------------
# result writing (mirror of runner.py save_result)
# ---------------------------------------------------------------------------
def save_result(results_dir, job_id, result, mask):
    jobs_dir = os.path.join(results_dir, "jobs", job_id)
    os.makedirs(jobs_dir, exist_ok=True)
    tmp_path = os.path.join(jobs_dir, "result.json.tmp")
    final_path = os.path.join(jobs_dir, "result.json")
    clean = mask_obj(result, mask)
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(clean, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, final_path)
    return final_path


# ---------------------------------------------------------------------------
# video helpers
# ---------------------------------------------------------------------------
def download_video(url, dest, timeout=300):
    """Download the Reel file. Returns (bytes, error)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "video" not in ctype and "octet-stream" not in ctype:
            return 0, f"video URL did not return a video (Content-Type: {ctype})"
        total = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
                total += len(chunk)
                if total > 500 * 1024 * 1024:
                    return total, "video file too large (>500MB)"
    return total, None


def mp4_duration_seconds(path):
    """Pure-python MP4 duration from moov/mvhd (no ffmpeg on the runner).
    Returns float seconds, or None when it cannot be read."""
    try:
        with open(path, "rb") as f:
            data = f.read(8 * 1024 * 1024)
        pos = 0
        moov = None
        while pos + 8 <= len(data):
            size, typ = struct.unpack(">I4s", data[pos:pos + 8])
            if typ == b"moov":
                moov = data[pos + 8:pos + size] if size > 8 else data[pos + 8:]
                break
            if size < 8:
                break
            pos += size
        if moov is None:
            return None
        pos = 0
        while pos + 8 <= len(moov):
            size, typ = struct.unpack(">I4s", moov[pos:pos + 8])
            if typ == b"mvhd":
                body = moov[pos + 8:pos + size]
                version = body[0]
                if version == 1:
                    timescale, duration = struct.unpack(">IQ", body[20:32])
                else:
                    timescale, duration = struct.unpack(">II", body[12:20])
                return duration / timescale if timescale else None
            if size < 8:
                break
            pos += size
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# page helpers
# ---------------------------------------------------------------------------
async def _dialog_text(page):
    try:
        return await page.evaluate(
            """() => {
              const d = document.querySelector('[role="dialog"]');
              return d ? d.innerText.slice(0, 3000) : '';
            }""")
    except Exception:
        return ""


async def _body_text(page, limit=3000):
    try:
        return ((await page.inner_text("body")) or "")[:limit]
    except Exception:
        return ""


async def _click_visible_text(page, texts, timeout_ms=8000):
    """Click the first visible button/div whose text contains one of `texts`.
    Returns the matched text, or None."""
    for text in texts:
        try:
            loc = page.locator(f"button:has-text('{text}'), div[role='button']:has-text('{text}')")
            for i in range(await loc.count()):
                el = loc.nth(i)
                try:
                    if await el.is_visible(timeout=1000):
                        await el.click(timeout=timeout_ms)
                        return text
                except Exception:
                    continue
        except Exception:
            continue
    return None


async def _wait_dialog(page, timeout_ms=15000):
    try:
        await page.wait_for_selector("[role='dialog']", timeout=timeout_ms)
        return True
    except Exception:
        return False


CREATE_SELECTORS = [
    "svg[aria-label='New post']",
    "[aria-label='New post']",
    "a[href='/create/']",
    "div[role='button']:has-text('Create')",
    "a:has-text('Create')",
]


async def _open_create(page):
    """Click the Create control. Returns True when a dialog opened."""
    for sel in CREATE_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible(timeout=3000):
                await loc.click(timeout=8000)
                await page.wait_for_timeout(1500)
                if await _wait_dialog(page, timeout_ms=10000):
                    return True
        except Exception:
            continue
    return await _wait_dialog(page, timeout_ms=5000)


async def _file_input(page):
    """The video file input inside the create dialog (or anywhere on page)."""
    for sel in ("[role='dialog'] input[type='file']", "input[type='file']"):
        try:
            loc = page.locator(sel).first
            if await loc.count():
                return loc
        except Exception:
            continue
    return None


async def detect_login(page):
    """-> dict(logged_in, username, checkpoint, login_wall, url).
    Positive signals: the Create control or the profile avatar link in the
    header. Negative: /accounts/login URL or a Log in prompt. A /challenge/
    URL or 'suspicious login' text means Instagram challenged the session."""
    url = page.url or ""
    body = await _body_text(page)
    links = []
    try:
        links = await page.evaluate(
            "() => [...document.querySelectorAll('a[href]')].map(a => ({"
            "href: a.getAttribute('href') || '', "
            "text: (a.innerText || '').trim().slice(0, 60)}))")
    except Exception:
        pass
    checkpoint = ("/challenge/" in url or "challenge" in url.lower()
                  or "suspicious" in body.lower()
                  or "Confirm your identity" in body
                  or "confirm your identity" in body.lower())
    login_wall = ("/accounts/login" in url
                  or any("Log in" in (l.get("text") or "") for l in links[:40]))
    create_present = False
    for sel in CREATE_SELECTORS:
        try:
            if await page.locator(sel).count() > 0:
                create_present = True
                break
        except Exception:
            continue
    username = None
    try:
        for l in links:
            href = l.get("href") or ""
            m = re.fullmatch(r"/([\w.\-]+)/", href)
            if m and m.group(1) not in ("explore", "direct", "reels", "accounts"):
                username = m.group(1)
                break
    except Exception:
        pass
    logged_in = bool(create_present) and not login_wall and not checkpoint
    return {"logged_in": logged_in, "username": username, "checkpoint": checkpoint,
            "login_wall": login_wall, "url": url}


# ---------------------------------------------------------------------------
# probe / post flows
# ---------------------------------------------------------------------------
async def run_probe(page, video_path, out_dir, log):
    """Attach the video, report what the uploader says, close the dialog.
    Submits NOTHING."""
    res = {"dialog_text": "", "upload_dialog_text": "", "duration_shown": None,
           "limit_error": None, "reel_choice": None, "file_input_found": False}
    if not await _open_create(page):
        log("open_create", False, "Create dialog did not open")
        res["error"] = "create dialog did not open"
        return res
    log("open_create", True, "Create dialog opened")
    try:
        await page.screenshot(path=os.path.join(out_dir, "create_dialog.png"))
    except Exception:
        pass
    res["dialog_text"] = await _dialog_text(page)
    fi = await _file_input(page)
    if fi is None:
        log("file_input", False, "no file input found in the create dialog")
        res["error"] = "no file input in create dialog"
        return res
    res["file_input_found"] = True
    try:
        await fi.set_input_files(video_path, timeout=30000)
        log("attach", True, "video file selected in the uploader")
    except Exception as e:
        log("attach", False, f"{type(e).__name__}")
        res["error"] = "could not select the video file in the uploader"
        return res
    # Wait for the video to process; capture whatever the UI reports.
    deadline = time.time() + 90
    seen = ""
    while time.time() < deadline:
        await page.wait_for_timeout(3000)
        seen = await _dialog_text(page)
        if seen and ("Next" in seen or "Crop" in seen or "too long" in seen.lower()
                     or "maximum" in seen.lower() or "90" in seen or ":" in seen):
            break
    res["upload_dialog_text"] = seen
    try:
        await page.screenshot(path=os.path.join(out_dir, "upload.png"))
    except Exception:
        pass
    m = re.search(r"(\d{1,2}):(\d{2})", seen or "")
    if m:
        res["duration_shown"] = int(m.group(1)) * 60 + int(m.group(2))
    low = (seen or "").lower()
    if any(w in low for w in ("too long", "maximum", "exceeds", "90 seconds", "shorter")):
        res["limit_error"] = (seen or "")[:500]
    if "reel" in low:
        res["reel_choice"] = True
    log("upload_report", True,
        f"duration_shown={res['duration_shown']} limit_error={'yes' if res['limit_error'] else 'no'}")
    # Close the dialog WITHOUT proceeding: Escape, then a Discard/close click.
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(1000)
        await _click_visible_text(page, ("Discard", "Close", "Cancel"), timeout_ms=3000)
        await page.wait_for_timeout(1000)
    except Exception:
        pass
    log("close_dialog", True, "dialog closed without publishing anything")
    return res


async def run_post(page, video_path, caption, out_dir, log):
    """Full publish flow. ONLY called when dry_run is false and mode is post."""
    if not await _open_create(page):
        return {"ok": False, "status": "post_failed",
                "error_message": "create dialog did not open"}
    try:
        await page.screenshot(path=os.path.join(out_dir, "create_dialog.png"))
    except Exception:
        pass
    fi = await _file_input(page)
    if fi is None:
        return {"ok": False, "status": "post_failed",
                "error_message": "no file input in create dialog"}
    try:
        await fi.set_input_files(video_path, timeout=30000)
    except Exception:
        return {"ok": False, "status": "post_failed",
                "error_message": "could not select the video file in the uploader"}
    log("attach", True, "video file selected")
    # Wait for processing to finish (Next appears, or an error).
    deadline = time.time() + 120
    ready = False
    limit_err = None
    while time.time() < deadline:
        await page.wait_for_timeout(3000)
        txt = await _dialog_text(page)
        low = txt.lower()
        if any(w in low for w in ("too long", "maximum", "exceeds", "90 seconds")):
            limit_err = txt[:500]
            break
        if "Next" in txt:
            ready = True
            break
    if limit_err:
        return {"ok": False, "status": "post_failed",
                "error_message": f"uploader rejected the video: {limit_err}"}
    if not ready:
        return {"ok": False, "status": "post_failed",
                "error_message": "video did not finish processing in 120s"}
    try:
        await page.screenshot(path=os.path.join(out_dir, "upload.png"))
    except Exception:
        pass
    # Step through: crop -> cover/edit -> caption. Each screen has Next.
    for step_name in ("crop", "cover"):
        hit = await _click_visible_text(page, ("Next",), timeout_ms=15000)
        if not hit:
            return {"ok": False, "status": "post_failed",
                    "error_message": f"no Next button on the {step_name} screen"}
        await page.wait_for_timeout(2000)
        log("next", True, step_name)
    try:
        await page.screenshot(path=os.path.join(out_dir, "caption.png"))
    except Exception:
        pass
    # Caption field (placeholder/aria-label varies; try several).
    filled = False
    for sel in ("textarea[aria-label*='caption' i]",
                "textarea[placeholder*='caption' i]",
                "[role='dialog'] textarea",
                "textarea"):
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible(timeout=3000):
                await loc.fill(caption or "", timeout=10000)
                filled = True
                break
        except Exception:
            continue
    if not filled:
        return {"ok": False, "status": "post_failed",
                "error_message": "caption field not found"}
    log("caption", True, f"{len(caption or '')} chars")
    hit = await _click_visible_text(page, ("Share",), timeout_ms=15000)
    if not hit:
        return {"ok": False, "status": "post_failed",
                "error_message": "no Share button on the caption screen"}
    log("share_clicked", True, "Share clicked; waiting for publish confirmation")
    # Wait for the shared confirmation and capture the post URL.
    deadline = time.time() + 180
    post_url = None
    while time.time() < deadline:
        await page.wait_for_timeout(3000)
        body = await _body_text(page, limit=2000)
        if "shared" in body.lower() or "Your reel" in body:
            try:
                links = await page.evaluate(
                    "() => [...document.querySelectorAll('a[href]')]"
                    ".map(a => a.getAttribute('href') || '')"
                    ".filter(h => h.includes('/p/') || h.includes('/reel/'))")
                for href in links or []:
                    cand = "https://www.instagram.com" + href if href.startswith("/") else href
                    if _goto_allowed(cand):
                        post_url = cand
                        break
            except Exception:
                pass
            if post_url:
                break
            # Confirmation shown but no link yet; keep waiting a little.
            if time.time() > deadline - 30:
                break
    try:
        await page.screenshot(path=os.path.join(out_dir, "published.png"))
    except Exception:
        pass
    if not post_url:
        return {"ok": False, "status": "post_failed",
                "error_message": "Share clicked but no published post URL appeared in 180s"}
    # Verify the post URL loads.
    if not _goto_allowed(post_url):
        return {"ok": False, "status": "post_failed",
                "error_message": "published URL is outside the allowed list (refusing to open it)"}
    try:
        await page.goto(post_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        txt = await _body_text(page, limit=1000)
        if "Sorry, this page isn't available" in txt:
            return {"ok": False, "status": "post_failed",
                    "error_message": "published URL does not load (page not available)"}
    except Exception as e:
        return {"ok": False, "status": "post_failed",
                "error_message": f"could not load the published URL: {type(e).__name__}"}
    return {"ok": True, "status": "posted", "post_url": post_url}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
async def amain(args):
    # NOTE: playwright is imported only after the session + dry-run gates,
    # so the no-session / dry-run paths work even without it installed.
    result = {"ok": False, "status": "error", "job_id": args.job_id,
              "mode": args.mode, "dry_run": args.dry_run,
              "video_url": args.video_url, "steps": []}
    mask = Masker({})
    out_dir = os.path.join(args.results_dir, "jobs", args.job_id)
    os.makedirs(out_dir, exist_ok=True)

    def log(step, ok, detail=""):
        entry = {"step": step, "ok": bool(ok), "detail": mask(str(detail))[:500]}
        result["steps"].append(entry)
        print(f"[{step}] {'ok' if ok else 'FAIL'} {mask(str(detail))[:300]}", flush=True)

    def finish(status, **extra):
        result["status"] = status
        result.update(extra)
        try:
            path = save_result(args.results_dir, args.job_id, result, mask)
            print(f"result written: {path} status={status}", flush=True)
            return 0
        except Exception as e:
            print(f"FATAL: could not write result.json: {type(e).__name__}", flush=True)
            return 1

    # ---- gate 1: session (before any browser or download) ----
    session, err = load_session("instagram")
    if err:
        log("session", False, err)
        return finish("no_session", error="no Instagram session configured",
                      error_message=err)
    mask = Masker(session)
    log("session", True, f"{len(session.get('cookies', []))} cookies loaded")

    # ---- gate 2: dry-run (a post request with dry_run on is refused here) ----
    if args.mode == "post" and args.dry_run:
        log("dry_run_gate", False, "dry_run is on — refusing to post")
        return finish("dry_run",
                      error="dry_run is on — refusing to post",
                      error_message="Dispatch with dry_run=false and mode=post to publish.")
    if args.mode not in ("probe", "post"):
        return finish("error", error="bad mode",
                      error_message=f"unknown mode {args.mode!r}")

    # ---- download the video ----
    video_path = os.path.join("/tmp", f"ig-{args.job_id}.mp4")
    try:
        nbytes, derr = download_video(args.video_url, video_path)
    except Exception as e:
        return finish("error", error="video download failed",
                      error_message=f"{type(e).__name__}")
    if derr:
        return finish("error", error="video download failed", error_message=derr)
    result["video_bytes"] = nbytes
    result["video_seconds"] = mp4_duration_seconds(video_path)
    log("download", True, f"{nbytes} bytes, {result['video_seconds']}s")

    # ---- browser: login check, then probe or post ----
    from playwright.async_api import async_playwright
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent=UA, locale="en-US")
                try:
                    cookies = sanitize_cookies(session.get("cookies", []))
                    if cookies:
                        await context.add_cookies(cookies)
                    ls_data = session.get("localStorage", {})
                    if isinstance(ls_data, dict) and ls_data:
                        ls_json = json.dumps(ls_data)
                        await context.add_init_script(
                            f"(() => {{ try {{ const d = {ls_json}; "
                            "for (const [k, v] of Object.entries(d)) { "
                            "window.localStorage.setItem(k, typeof v === 'string' ? v : JSON.stringify(v)); } "
                            "} catch (e) {} })();")
                    page = await context.new_page()
                    if not _goto_allowed(HOME):
                        return finish("error", error="allowlist",
                                      error_message="home URL failed the allowlist (internal error)")
                    await page.goto(HOME, wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(5000)
                    try:
                        await page.screenshot(path=os.path.join(out_dir, "home.png"))
                    except Exception:
                        pass
                    login = await detect_login(page)
                    result["logged_in"] = login["logged_in"]
                    result["username"] = login["username"]
                    result["checkpoint"] = login["checkpoint"]
                    log("login_check", login["logged_in"],
                        f"url={login['url']} checkpoint={login['checkpoint']}")
                    if login["checkpoint"]:
                        return finish("checkpoint",
                                      error="Instagram challenged the session",
                                      error_message="Instagram showed a challenge / "
                                      "identity confirmation for this login. The session "
                                      "needs a fresh capture (see README_instagram.md).")
                    if not login["logged_in"]:
                        return finish("login_required",
                                      error="Instagram session is not logged in",
                                      error_message="instagram.com showed a login wall "
                                      "with this session. The session needs a fresh "
                                      "capture (see README_instagram.md).")
                    if args.mode == "probe":
                        probe = await run_probe(page, video_path, out_dir, log)
                        result.update({k: v for k, v in probe.items()
                                       if k in ("dialog_text", "upload_dialog_text",
                                                "duration_shown", "limit_error",
                                                "reel_choice", "file_input_found")})
                        if probe.get("error"):
                            return finish("probe_failed", error="probe failed",
                                          error_message=probe["error"])
                        return finish("probe_ok",
                                      error=None, error_message=None,
                                      note="probe only: nothing was published")
                    posted = await run_post(page, video_path, args.caption or "",
                                            out_dir, log)
                    if posted.get("ok"):
                        return finish("posted", error=None, error_message=None,
                                      post_url=posted.get("post_url"))
                    return finish(posted.get("status", "post_failed"),
                                  error="post failed",
                                  error_message=posted.get("error_message", "unknown"))
                finally:
                    await context.close()
            finally:
                await browser.close()
    except Exception as e:
        log("browser", False, f"{type(e).__name__}")
        return finish("error", error="browser error",
                      error_message=f"{type(e).__name__}")
    finally:
        try:
            os.remove(video_path)
        except Exception:
            pass


def main(argv=None):
    ap = argparse.ArgumentParser(description="Post an Instagram Reel from a saved session.")
    ap.add_argument("--video-url", "--video_url", required=True, dest="video_url")
    ap.add_argument("--caption", default="")
    ap.add_argument("--job-id", "--job_id", required=True, dest="job_id")
    ap.add_argument("--mode", choices=["probe", "post"], default="probe")
    ap.add_argument("--dry-run", "--dry_run", dest="dry_run", default="true",
                    choices=["true", "false"],
                    help="true (default): never publish. false + mode=post: publish.")
    ap.add_argument("--results-dir", "--results_dir", dest="results_dir",
                    default="hq-ai-results")
    ap.add_argument("--notion-page-id", "--notion_page_id", dest="notion_page_id", default="")
    args = ap.parse_args(argv)
    args.dry_run = str(args.dry_run).lower() == "true"
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
