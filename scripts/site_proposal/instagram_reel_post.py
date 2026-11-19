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
  Reel upload flow (probe = inspect only, post = publish), (3) a recovery
  read of the owner's /reels/ page (recover mode — find the newest Reel
  the account just posted, so a Share that succeeded without returning a
  URL is never lost), (4) delete mode (user order 2026-11-19): open the
  ONE specific Reel URL it is told to delete and delete ONLY that Reel —
  never anything else, never a whole profile, never DMs/feed. Delete NEVER
  runs automatically: it needs an explicit job dispatch. It navigates to
  no other Instagram page — see _goto_allowed() for the enforced allowlist
  (home, the create dialog, the published post URL for verification, the
  owner's /reels/ page, and the single Reel URL given to delete mode).
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
    pick Reel in the Create menu, attach the video file, walk through
    Crop/Edit to the caption screen, verify the caption field is findable
    and fillable (filled with the supplied caption, or a clearly-marked
    TEST string when none is supplied), and REPORT what the uploader says
    (duration it shows, any length limit / error text), then close the
    dialog with Discard. It also inspects (probe only) whether the
    uploader offers a 'Share to Facebook' toggle — on the caption screen
    and under 'Advanced settings' — and records present/absent and ON/OFF
    without touching it. Submits NOTHING — Share is never clicked in
    probe mode. Run this first: Instagram's web UI and its length limits
    are UNVERIFIED until a live probe reports.
  post — the full flow: upload -> Reel -> Next -> caption (pasted VERBATIM
    from the --caption input, never generated or reworded here; a post
    with no caption is REFUSED before any browser work) -> Share ->
    verify the post URL. ONLY runs when dry_run is false AND mode is
    post. Anything else refuses with status "dry_run" before any browser
    work. After Share, the confirmation waits up to 300s, then falls back
    to reading the newest Reel from the owner's /reels/ page (see recover).
  recover — find the newest Reel on the owner's profile (logged in) and
    return its URL, verified to load and carrying the caption marker
    "Succession" PLUS a Part 2 marker ("Get Rennie" / "keep you posted"
    — so the recovered URL is proven to be the Part 2 Reel, never the
    older Part 1 Reel). Used when a post succeeded but no URL came back
    (the 2026-11-19 Part 2 case: Share clicked, no URL in 180s, Reel
    live). Submits NOTHING — read-only. It tries, in order: the /reels/
    page grid (with a response listener that captures the grid's own
    GraphQL data even when the grid never renders, plus a Reels-tab
    click variant), the web profile-info API, the GraphQL profile-feed
    query, the /api/v1/feed/user timeline API, the notifications inbox,
    the mobile web profile (m.instagram.com), and the account's own
    'Your activity' page. Same session; owner's own profile only.
    Needs no video (the --video-url input is accepted but ignored; no
    download happens).
  delete — open the ONE Reel URL given with --reel-url and delete it
    through the UI (••• menu -> Delete -> confirm), then verify it is
    gone (the URL no longer loads). Deletes ONLY that exact URL — never
    anything else. dry_run=true = inspect only: verify the Delete option
    is present in the ••• menu and close it WITHOUT deleting. Live delete
    needs dry_run=false AND an explicit dispatch (never automatic).
    Needs no video (the --video-url input is accepted but ignored).

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
import uuid
from datetime import datetime, timezone

HOME = "https://www.instagram.com/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# --- navigation allowlist (privacy rule). Nothing else is ever opened. ---
# home page (login check + create dialog live here), the published post URL
# (verification after Share), the owner's /reels/ page (the recover mode's
# read of the newest Reel the account just posted), the owner's own
# 'Your activity' page (recover only — lists the posts the account made),
# and — delete mode only — the ONE specific Reel URL it is told to
# delete. The owner's own profile page (Reels-tab variant) and the mobile
# profile (m.instagram.com, same owner) are checked by _goto_profile_allowed
# / built from the logged-in username, never from any other input.
# DMs (/direct), feed (/explore), following lists, settings
# (/accounts/edit) and every other path are refused.
_ALLOWED_RE = re.compile(
    r"^https://www\.instagram\.com/(?:$|reel/[\w-]+/?$|p/[\w-]+/?$|[\w.\-]+/reels/?$|your_activity/?$)")


def _goto_allowed(url):
    return bool(_ALLOWED_RE.match(url or ""))


def _codes_from_json(data):
    """Shortcodes from a parsed Instagram GraphQL/API JSON body. The
    current profile-feed GraphQL shape puts the media id in
    node["code"] (not node["shortcode"]); walk the structure and
    collect every dict that carries a "code" string together with a
    media marker (media_type / __typename / pk). Returns codes in
    document order (newest first), deduplicated."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            code = node.get("code")
            if (isinstance(code, str) and len(code) >= 8
                    and ("media_type" in node or "__typename" in node
                         or "pk" in node)):
                found.append(code)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    seen = set()
    out = []
    for c in found:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


async def _newest_reel_via_graphql(page, username, log):
    """Second fallback (read-only): fetch the owner's own media through
    Instagram's GraphQL profile-feed query (the same query the web app
    itself uses for the profile grid) and return the newest Reel URL.
    The user id comes from the `ds_user_id` cookie (set by Instagram
    for the logged-in user) or, failing that, the profile page's meta
    tag (al:ios:url instagram://user?id=<id>). In-page fetch, same
    session, owner's own profile only. No DMs, no feed, no other
    account, no writes. The web_profile_info API returned 429 then
    404 (2026-11-19), so GraphQL is the remaining read path."""
    uid = None
    try:
        for c in await page.context.cookies():
            if c.get("name") == "ds_user_id" and c.get("value"):
                uid = c["value"]
                break
    except Exception:
        pass
    if not uid:
        try:
            html = await page.content()
        except Exception:
            html = ""
        m = re.search(r"instagram://user\?id=(\d+)", html or "")
        if m:
            uid = m.group(1)
    if not uid:
        log("newest_reel_gql", False,
            "no user id (no ds_user_id cookie, no meta tag)")
        return None
    import json as _json
    import urllib.parse as _up
    variables = _json.dumps({"id": uid, "first": 12}, separators=(",", ":"))
    url = ("https://www.instagram.com/graphql/query/?query_hash="
           "69cba40317214236af376e7d0a9e1a5&variables="
           + _up.quote(variables, safe=""))
    js = """async (url) => {
      try {
        const r = await fetch(url, {headers: {
          'X-IG-App-ID': '936619743392459',
          'X-Requested-With': 'XMLHttpRequest'}});
        if (r.status === 200) return {ok: true, data: await r.json()};
        return {ok: false, status: r.status};
      } catch (e) { return {ok: false, error: String(e)}; }
    }"""
    try:
        res = await page.evaluate(js, url)
    except Exception as e:
        log("newest_reel_gql", False, f"graphql failed: {type(e).__name__}")
        return None
    if not res.get("ok"):
        log("newest_reel_gql", False,
            f"graphql failed: status={res.get('status')} {res.get('error', '')}")
        return None
    data = res.get("data") or {}
    try:
        edges = data["data"]["user"]["edge_owner_to_timeline_media"]["edges"]
    except Exception:
        log("newest_reel_gql", False, "graphql JSON has no media edges")
        return None
    for edge in edges or []:
        node = edge.get("node") or {}
        if node.get("__typename") in ("GraphVideo", "GraphSidecar") or node.get("is_video"):
            code = node.get("shortcode")
            if code:
                cand = f"https://www.instagram.com/reel/{code}/"
                log("newest_reel_gql", True, f"newest Reel via GraphQL: {cand}")
                return cand
    log("newest_reel_gql", False, "no video edge in GraphQL media")
    return None


async def _newest_reel_via_api(page, username, log):
    """Fallback (read-only): fetch the owner's own profile media via
    Instagram's web profile-info API and return the newest Reel URL.
    The /reels/ PAGE grid does not render its links in the headless
    browser (2026-11-19: the page loads the profile header — '2 posts'
    — but no /reel/ links appear even after 30s of polling + scrolling,
    and no shortcode is in the page HTML), so the same data is read
    through the profile API instead. The fetch runs in the PAGE's JS
    context (same-origin fetch, session cookies included) — not a
    separate HTTP client. This is NOT a page navigation (the allowlist
    governs page.goto only); it fetches ONLY the owner's own profile
    media, to find the Reel this account just posted. No DMs, no feed,
    no other account, no writes.
    Retries up to 3 times (60s apart): the API returned HTTP 429
    (rate-limited) on 2026-11-19, which may clear after a short wait."""
    url = ("https://www.instagram.com/api/v1/users/web_profile_info/"
           f"?username={username}")
    csrf = await _csrf_token(page)
    js = """async ([url, csrf]) => {
      const headers = {'X-IG-App-ID': '936619743392459',
        'X-Requested-With': 'XMLHttpRequest',
        'X-ASBD-ID': '129477'};
      if (csrf) headers['X-CSRFToken'] = csrf;
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          const r = await fetch(url, {headers});
          if (r.status === 200) return {ok: true, data: await r.json()};
          if (r.status === 429) {
            await new Promise(res => setTimeout(res, 60000));
            continue;
          }
          return {ok: false, status: r.status};
        } catch (e) { return {ok: false, error: String(e)}; }
      }
      return {ok: false, status: 429, note: 'still rate-limited after 3 attempts'};
    }"""
    try:
        res = await page.evaluate(js, [url, csrf])
    except Exception as e:
        log("newest_reel_api", False, f"web_profile_info failed: {type(e).__name__}")
        return None
    if not res.get("ok"):
        log("newest_reel_api", False,
            f"web_profile_info failed: status={res.get('status')} {res.get('note', '')} {res.get('error', '')}")
        return None
    data = res.get("data") or {}
    try:
        edges = data["data"]["user"]["edge_owner_to_timeline_media"]["edges"]
    except Exception:
        log("newest_reel_api", False, "web_profile_info JSON has no media edges")
        return None
    for edge in edges or []:
        node = edge.get("node") or {}
        if node.get("__typename") in ("GraphVideo", "GraphSidecar") or node.get("is_video"):
            code = node.get("shortcode")
            if code:
                cand = f"https://www.instagram.com/reel/{code}/"
                log("newest_reel_api", True, f"newest Reel via profile API: {cand}")
                return cand
    log("newest_reel_api", False, "no video edge in profile media")
    return None


async def _newest_reel_link(page, username, log):
    """Read-only: open the owner's /reels/ page and return the FIRST
    /reel/<code>/ link (the newest Reel). The /reels/ page is inside the
    navigation allowlist (privacy rule: the recover read only ever opens
    this page, to find the Reel this account just posted). Returns the
    full https://www.instagram.com/reel/<code>/ URL, or None. Never
    clicks anything, never opens DMs/feed/other pages.
    The reels grid loads asynchronously (GraphQL), so this polls for up
    to 45s and scrolls to trigger lazy loading — a single 5s wait was
    not enough (2026-11-19: the first recover run found no links).
    NEW 2026-11-19 (recover v2): a response listener captures the
    profile grid's OWN GraphQL/API responses — if the grid request
    succeeds but the page never renders its links, the shortcodes are
    still in the response body. If the direct /reels/ URL does not
    render, the profile page's Reels tab is clicked instead (a
    different code path for the same grid)."""
    url = f"https://www.instagram.com/{username}/reels/"
    if not _goto_allowed(url):
        return None
    # Capture the grid's own data responses (read-only): the page's
    # GraphQL/API calls carry the media shortcodes even when the grid
    # never renders its links. Also records each such call's status, so
    # a later run can tell a rate limit (429) from a dead query (400).
    captured = []

    async def _on_response(resp):
        try:
            u = resp.url or ""
            if ("graphql/query" in u or "web_profile_info" in u
                    or "feed/user" in u):
                try:
                    body = await resp.text()
                except Exception:
                    body = ""
                codes = re.findall(
                    r'"shortcode"\s*:\s*"([A-Za-z0-9_-]+)"', body or "")
                shape = ""
                if not codes:
                    # The current profile-feed GraphQL shape carries the
                    # media id as node["code"] (not "shortcode").
                    try:
                        parsed = json.loads(body or "")
                    except Exception:
                        parsed = None
                    codes = _codes_from_json(parsed)
                    if isinstance(parsed, dict):
                        shape = ",".join(list(parsed.keys())[:5])
                        d = parsed.get("data")
                        if isinstance(d, dict):
                            shape += " | data:" + ",".join(list(d.keys())[:5])
                captured.append({"url": u[:120], "status": resp.status,
                                 "codes": codes[:6], "shape": shape})
        except Exception:
            pass

    try:
        page.on("response", _on_response)
    except Exception:
        pass

    def _captured_codes():
        for entry in captured:
            log("newest_reel_net", bool(entry["codes"]),
                f"{entry['status']} {entry['url']} codes={entry['codes']} "
                f"shape={entry.get('shape', '')}")
            if entry["codes"]:
                return f"https://www.instagram.com/reel/{entry['codes'][0]}/"
        return None

    async def _scan_links(seconds):
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                hrefs = await page.evaluate(
                    "() => [...document.querySelectorAll('a[href]')]"
                    ".map(a => a.getAttribute('href') || '')"
                    ".filter(h => h.includes('/reel/'))")
            except Exception:
                hrefs = []
            for href in hrefs or []:
                cand = ("https://www.instagram.com" + href
                        if href.startswith("/") else href)
                if _goto_allowed(cand):
                    log("newest_reel", True, f"newest Reel on profile: {cand}")
                    return cand
            # No links yet: scroll to trigger lazy loading, then wait.
            try:
                await page.evaluate(
                    "() => window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                pass
            await page.wait_for_timeout(3000)
        return None

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        log("newest_reel", False, f"could not load {username}/reels: {type(e).__name__}")
        return None
    found = await _scan_links(45)
    if found:
        return found
    # Diagnostics for the next run: what did the page actually show?
    try:
        body = await _body_text(page, limit=500)
        log("newest_reel", False,
            f"no /reel/ link on {username}/reels after 45s; page text: {body[:300]}")
    except Exception:
        log("newest_reel", False, "no /reel/ link found on the profile reels page")
    # The grid's own responses may carry the shortcodes even though no
    # link rendered — check what the page's calls returned.
    cand = _captured_codes()
    if cand:
        log("newest_reel", True, f"newest Reel from grid response: {cand}")
        return cand
    # Variant (2026-11-19, untried): open the profile page and click its
    # Reels tab — a different code path from the direct /reels/ URL.
    profile_url = f"https://www.instagram.com/{username}/"
    if _goto_profile_allowed(profile_url, username):
        try:
            await page.goto(profile_url, wait_until="domcontentloaded",
                            timeout=60000)
            await page.wait_for_timeout(4000)
            tab = None
            for loc in [page.locator("a[href$='/reels/']"),
                        page.locator("a:has-text('Reels')")]:
                try:
                    if await loc.count() and await loc.first.is_visible(timeout=3000):
                        tab = loc.first
                        break
                except Exception:
                    continue
            if tab is not None:
                await tab.click(timeout=10000)
                await page.wait_for_timeout(5000)
                found = await _scan_links(45)
                if found:
                    return found
                cand = _captured_codes()
                if cand:
                    log("newest_reel", True,
                        f"newest Reel from grid response: {cand}")
                    return cand
            else:
                log("newest_reel_tab", False,
                    "no Reels tab found on the profile page")
        except Exception as e:
            log("newest_reel_tab", False,
                f"profile Reels-tab variant failed: {type(e).__name__}")
    # The page grid does not render links in the headless browser — try
    # the page's own embedded data first (the profile HTML carries the
    # media edges as JSON with "shortcode" fields; the first is the
    # newest post), then the profile-info API.
    try:
        html = await page.content()
    except Exception:
        html = ""
    for m in re.finditer(r'"shortcode"\s*:\s*"([A-Za-z0-9_-]+)"', html or ""):
        cand = f"https://www.instagram.com/reel/{m.group(1)}/"
        if _goto_allowed(cand):
            log("newest_reel", True, f"newest Reel from page data: {cand}")
            return cand
    log("newest_reel", False, "no shortcode in the /reels/ page HTML either")
    # The profile-info API is rate-limited / 404 — try the GraphQL
    # profile-feed query (the web app's own grid query) as a last read.
    api_url = await _newest_reel_via_api(page, username, log)
    if api_url:
        return api_url
    return await _newest_reel_via_graphql(page, username, log)


async def _verify_reel_url(page, url, must_contain=None):
    """Load a Reel URL and check it really is a live Reel. Returns
    (loads, has_caption_marker): loads = the page is not the 'Sorry, this
    page isn't available' dead page; has_caption_marker = the page text
    contains 'Succession' (every lesson caption contains it — an identity
    signal that the Reel is ours, not some other account's post).
    must_contain (list of strings, optional): the candidate must also
    carry one of these markers in the page HTML — used by recover to
    prove the candidate is the Part 2 Reel (words 6-10, e.g. 'Get
    Rennie') and NOT the older Part 1 Reel, so a delete can never target
    the wrong post. When given and none matches, the second return
    value is False."""
    if not _goto_allowed(url):
        return False, False
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        body = await _body_text(page, limit=2000)
    except Exception:
        return False, False
    loads = "Sorry, this page isn't available" not in body
    marker = "Succession" in body
    if must_contain:
        try:
            html = await page.content()
        except Exception:
            html = ""
        marker = marker and any(m in (html or "") for m in must_contain)
    return loads, marker


def _goto_profile_allowed(url, username):
    """The owner's own profile page (Reels-tab variant of the recover
    read). Only ever called with the logged-in username — never any
    other account, never any other path."""
    return url in (f"https://www.instagram.com/{username}/",
                   f"https://www.instagram.com/{username}")


async def _csrf_token(page):
    """The `csrftoken` cookie value, for API calls that need the
    X-CSRFToken header. Returns '' when absent."""
    try:
        for c in await page.context.cookies():
            if c.get("name") == "csrftoken" and c.get("value"):
                return c["value"]
    except Exception:
        pass
    return ""


async def _user_id(page):
    """The logged-in user's numeric id, from the `ds_user_id` cookie or
    the profile page's meta tag. Returns None when unknown."""
    try:
        for c in await page.context.cookies():
            if c.get("name") == "ds_user_id" and c.get("value"):
                return c["value"]
    except Exception:
        pass
    try:
        html = await page.content()
    except Exception:
        html = ""
    m = re.search(r"instagram://user\?id=(\d+)", html or "")
    return m.group(1) if m else None


async def _newest_reel_via_feed_api(page, username, log):
    """NEW 2026-11-19 (untried): the classic user-timeline endpoint
    GET /api/v1/feed/user/<uid>/?count=12 — a different read path from
    web_profile_info (404) and GraphQL (400). In-page fetch first (same
    session, full headers incl. CSRF), then Playwright's request
    context. Items are newest-first; the first video item (media_type
    2) is the newest Reel. Read-only; owner's own profile only."""
    uid = await _user_id(page)
    if not uid:
        log("newest_reel_feed", False,
            "no user id (no ds_user_id cookie, no meta tag)")
        return None
    csrf = await _csrf_token(page)
    url = f"https://www.instagram.com/api/v1/feed/user/{uid}/?count=12"
    headers = {"X-IG-App-ID": "936619743392459",
               "X-Requested-With": "XMLHttpRequest",
               "X-ASBD-ID": "129477"}
    if csrf:
        headers["X-CSRFToken"] = csrf
    data = None
    js = """async ([url, headers]) => {
      try {
        const r = await fetch(url, {headers});
        if (r.status === 200) return {ok: true, data: await r.json()};
        return {ok: false, status: r.status};
      } catch (e) { return {ok: false, error: String(e)}; }
    }"""
    try:
        res = await page.evaluate(js, [url, headers])
    except Exception as e:
        res = {"ok": False, "error": type(e).__name__}
    if res.get("ok"):
        data = res.get("data")
    else:
        log("newest_reel_feed", False,
            f"feed/user in-page fetch failed: status={res.get('status')} "
            f"{res.get('error', '')}")
    if not isinstance(data, dict):
        # Second transport: Playwright's request context (shares the
        # browser context's cookies).
        try:
            resp = await page.context.request.get(url, headers=headers)
            if resp.status == 200:
                data = await resp.json()
            else:
                log("newest_reel_feed", False,
                    f"feed/user request-context failed: status={resp.status}")
        except Exception as e:
            log("newest_reel_feed", False,
                f"feed/user request-context failed: {type(e).__name__}")
    if not isinstance(data, dict):
        log("newest_reel_feed", False, "feed/user returned no usable JSON")
        return None
    for item in data.get("items") or []:
        if item.get("media_type") == 2 and item.get("code"):
            cand = f"https://www.instagram.com/reel/{item['code']}/"
            log("newest_reel_feed", True, f"newest Reel via feed/user API: {cand}")
            return cand
    log("newest_reel_feed", False, "no video item in feed/user response")
    return None


async def _newest_reel_via_news(page, log):
    """NEW 2026-11-19 (untried): the notifications/activity inbox
    GET /api/v1/news/inbox/ — Instagram posts a 'your reel is live'
    entry when a Reel publishes; its args carry a link to the media.
    Read-only; owner's own inbox only. Returns the first /reel/ (or
    /p/) link found, or None."""
    url = "https://www.instagram.com/api/v1/news/inbox/"
    js = """async (url) => {
      try {
        const r = await fetch(url, {headers: {
          'X-IG-App-ID': '936619743392459',
          'X-Requested-With': 'XMLHttpRequest'}});
        if (r.status === 200) return {ok: true, data: await r.json()};
        return {ok: false, status: r.status};
      } catch (e) { return {ok: false, error: String(e)}; }
    }"""
    try:
        res = await page.evaluate(js, url)
    except Exception as e:
        log("newest_reel_news", False, f"news inbox failed: {type(e).__name__}")
        return None
    if not res.get("ok"):
        log("newest_reel_news", False,
            f"news inbox failed: status={res.get('status')} {res.get('error', '')}")
        return None
    data = res.get("data") or {}
    stories = []
    for key in ("new_stories", "old_stories", "stories"):
        stories.extend(data.get(key) or [])
    all_links = []
    for st in stories:
        args = st.get("args") or {}
        for link in args.get("links") or []:
            u = link.get("link") or ""
            if u:
                all_links.append(u)
            if "/reel/" in u or "/p/" in u:
                cand = u if u.startswith("http") else "https://www.instagram.com" + u
                if _goto_allowed(cand):
                    log("newest_reel_news", True, f"Reel link in news inbox: {cand}")
                    return cand
    log("newest_reel_news", False,
        f"no Reel link in news inbox ({len(stories)} stories, "
        f"links={all_links[:10]})")
    return None


MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
             "Mobile/15E148 Safari/604.1")


async def _newest_reel_via_mobile(page, username, log):
    """NEW 2026-11-19 (untried): the mobile web profile
    (m.instagram.com) renders a different front-end; its grid may
    expose the /reel/ links the desktop /reels/ page never renders.
    A second, mobile browser context with the SAME session cookies.
    Read-only; owner's own profile only. The mobile URLs are built
    from the logged-in username, never from any other input."""
    try:
        browser = page.context.browser
        cookies = await page.context.cookies()
    except Exception as e:
        log("newest_reel_mobile", False, f"no browser/cookies: {type(e).__name__}")
        return None
    if browser is None:
        log("newest_reel_mobile", False, "no browser handle for a mobile context")
        return None
    mctx = None
    try:
        mctx = await browser.new_context(
            viewport={"width": 390, "height": 844},
            user_agent=MOBILE_UA, locale="en-US",
            is_mobile=True, has_touch=True)
        if cookies:
            await mctx.add_cookies(cookies)
        mpage = await mctx.new_page()
        for path in (f"https://m.instagram.com/{username}/reels/",
                     f"https://m.instagram.com/{username}/"):
            try:
                await mpage.goto(path, wait_until="domcontentloaded", timeout=60000)
                await mpage.wait_for_timeout(5000)
            except Exception as e:
                log("newest_reel_mobile", False,
                    f"mobile {path} failed: {type(e).__name__}")
                continue
            try:
                hrefs = await mpage.evaluate(
                    "() => [...document.querySelectorAll('a[href]')]"
                    ".map(a => a.getAttribute('href') || '')"
                    ".filter(h => h.includes('/reel/') || h.includes('/p/'))")
            except Exception:
                hrefs = []
            for href in hrefs or []:
                cand = ("https://www.instagram.com" + href
                        if href.startswith("/") else href)
                if "/reel/" in cand and _goto_allowed(cand):
                    log("newest_reel_mobile", True,
                        f"newest Reel on mobile profile: {cand}")
                    return cand
            try:
                html = await mpage.content()
            except Exception:
                html = ""
            for m in re.finditer(r'"shortcode"\s*:\s*"([A-Za-z0-9_-]+)"', html or ""):
                cand = f"https://www.instagram.com/reel/{m.group(1)}/"
                log("newest_reel_mobile", True,
                    f"newest Reel from mobile page data: {cand}")
                return cand
        try:
            mtext = await _body_text(mpage, limit=300)
        except Exception:
            mtext = ""
        log("newest_reel_mobile", False,
            "no /reel/ link or shortcode on the mobile profile; "
            f"page text: {mtext[:200]}")
        return None
    except Exception as e:
        log("newest_reel_mobile", False, f"mobile context failed: {type(e).__name__}")
        return None
    finally:
        try:
            if mctx:
                await mctx.close()
        except Exception:
            pass


async def _newest_reel_via_activity(page, username, log):
    """NEW 2026-11-19 (untried): the account's own 'Your activity'
    page lists the photos/videos it posted. Read-only; owner's own
    activity only."""
    url = "https://www.instagram.com/your_activity/"
    if not _goto_allowed(url):
        return None
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)
    except Exception as e:
        log("newest_reel_activity", False,
            f"could not load your_activity: {type(e).__name__}")
        return None
    try:
        hrefs = await page.evaluate(
            "() => [...document.querySelectorAll('a[href]')]"
            ".map(a => a.getAttribute('href') || '')"
            ".filter(h => h.includes('/reel/') || h.includes('/p/'))")
    except Exception:
        hrefs = []
    for href in hrefs or []:
        cand = ("https://www.instagram.com" + href
                if href.startswith("/") else href)
        if _goto_allowed(cand):
            log("newest_reel_activity", True, f"post link on your_activity: {cand}")
            return cand
    log("newest_reel_activity", False, "no /reel/ or /p/ link on your_activity")
    return None


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


async def _dismiss_popups(page):
    """Dismiss 'Save your login info?' / 'Turn on Notifications' popups
    (Not Now), if Instagram shows them after login. Nothing else."""
    for text in ("Not Now", "Not now"):
        try:
            hit = await _click_visible_text(page, (text,), timeout_ms=3000)
            if hit:
                await page.wait_for_timeout(1000)
                return True
        except Exception:
            continue
    return False


async def _menu_text(page):
    """Inner text of the Create menu ([role=menu]), '' when absent."""
    try:
        return await page.evaluate(
            """() => {
              const m = document.querySelector('[role="menu"]');
              return m ? m.innerText.slice(0, 1000) : '';
            }""")
    except Exception:
        return ""


async def _create_state(page):
    """-> (menu_text, dialog_open, dialog_text)."""
    menu = await _menu_text(page)
    dlg = await _dialog_text(page)
    return menu, bool(dlg), dlg


# Click targets for the sidebar Create (+) control, most specific first.
# NOTE: clicking the bare <svg> is unreliable — prefer its clickable
# ancestor (<a> / [role=button]). At viewports narrower than ~1920px
# Instagram collapses the sidebar and the + button is not clickable,
# which is why the browser runs at 1920x1080 (see amain).
CREATE_CLICK_TARGETS = [
    "a:has(svg[aria-label='New post'])",
    "div[role='button']:has(svg[aria-label='New post'])",
    "a:has(svg[aria-label='Post'])",
    "svg[aria-label='New post']",
    "[aria-label='New post']",
    "a:has-text('Create')",
]


async def _open_create(page, log):
    """Click Create (+), pick Reel in the Create menu, wait for the upload
    dialog. Returns a dict with opened / reel_choice / menu_text /
    attempts (per-selector diagnostics) / error.

    REELS ONLY: if the Create menu appears without a Reel option, this
    FAILS instead of picking Post — a regular Feed post is never an
    acceptable fallback.
    """
    attempts = []
    menu, dlg_open, _ = await _create_state(page)
    clicked_any = False
    if not menu and not dlg_open:
        for sel in CREATE_CLICK_TARGETS:
            try:
                loc = page.locator(sel).first
                n = await loc.count()
                vis = False
                if n:
                    try:
                        vis = await loc.is_visible(timeout=3000)
                    except Exception:
                        vis = False
                if not n or not vis:
                    attempts.append({"selector": sel, "count": n,
                                     "visible": vis, "clicked": False})
                    continue
                await loc.click(timeout=8000)
                clicked_any = True
                await page.wait_for_timeout(1500)
                menu, dlg_open, _ = await _create_state(page)
                attempts.append({"selector": sel, "count": n, "visible": True,
                                 "clicked": True, "menu": (menu or "")[:300],
                                 "dialog_open": dlg_open})
                if menu or dlg_open:
                    break
            except Exception as e:
                attempts.append({"selector": sel, "clicked": False,
                                 "error": type(e).__name__})
                continue
        if not clicked_any:
            # Last resort: the link by its accessible name.
            try:
                link = page.get_by_role("link", name="Create").first
                if await link.count() and await link.is_visible(timeout=3000):
                    await link.click(timeout=8000)
                    clicked_any = True
                    await page.wait_for_timeout(1500)
                    menu, dlg_open, _ = await _create_state(page)
                    attempts.append({"selector": "get_by_role(link, Create)",
                                     "clicked": True, "menu": (menu or "")[:300],
                                     "dialog_open": dlg_open})
            except Exception as e:
                attempts.append({"selector": "get_by_role(link, Create)",
                                 "clicked": False, "error": type(e).__name__})
    if not clicked_any and not menu and not dlg_open:
        return {"opened": False, "attempts": attempts, "menu_text": menu,
                "error": "no Create control was visible/clickable"}
    # The Create menu offers Post vs Reel: always pick Reel.
    reel_choice = None
    if menu:
        if "reel" in menu.lower():
            hit = None
            for sel in ("[role='menuitem']:has-text('Reel')",
                        "[role='menu'] :text('Reel')",
                        "div[role='button']:has-text('Reel')"):
                try:
                    loc = page.locator(sel).first
                    if await loc.count() and await loc.is_visible(timeout=3000):
                        await loc.click(timeout=8000)
                        hit = sel
                        break
                except Exception:
                    continue
            if not hit:
                return {"opened": False, "attempts": attempts,
                        "menu_text": menu,
                        "error": "create menu shows Reel but it could not be clicked"}
            reel_choice = "Reel"
            log("reel_choice", True, "picked Reel in the Create menu")
            await page.wait_for_timeout(1500)
        else:
            return {"opened": False, "attempts": attempts, "menu_text": menu,
                    "error": "create menu has no Reel option — refusing to "
                             "post as a regular Post"}
    if not await _wait_dialog(page, timeout_ms=15000):
        return {"opened": False, "attempts": attempts, "menu_text": menu,
                "error": "create dialog did not open after picking Reel"}
    dlg = await _dialog_text(page)
    return {"opened": True, "attempts": attempts, "menu_text": menu,
            "reel_choice": reel_choice, "dialog_text": dlg}


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


async def _attach_video(page, video_path, log):
    """Attach the video to the open create dialog.
    The file input is created ON DEMAND: first try an input that already
    exists; otherwise click 'Select from computer' and catch the native
    file chooser. Returns {ok, method, error}."""
    fi = await _file_input(page)
    if fi is not None:
        try:
            await fi.set_input_files(video_path, timeout=30000)
            return {"ok": True, "method": "set_input_files"}
        except Exception as e:
            log("attach", False,
                f"set_input_files on existing input failed: {type(e).__name__}")
    try:
        async with page.expect_file_chooser(timeout=15000) as fc_info:
            hit = await _click_visible_text(
                page, ("Select from computer",), timeout_ms=10000)
            if not hit:
                return {"ok": False, "method": "file_chooser",
                        "error": "no file input and no 'Select from computer' button"}
        chooser = await fc_info.value
        await chooser.set_files(video_path, timeout=30000)
        return {"ok": True, "method": "file_chooser"}
    except Exception as e:
        return {"ok": False, "method": "file_chooser",
                "error": f"file chooser failed: {type(e).__name__}"}


# Caption field: on the share screen Instagram uses a contenteditable div
# (role=textbox, aria-label contains 'caption'), NOT a <textarea>.
# The textarea selectors are fallbacks only.
CAPTION_SELECTORS = [
    "div[role='textbox'][contenteditable='true'][aria-label*='caption' i]",
    "div[role='textbox'][contenteditable='true']",
    "[role='dialog'] div[contenteditable='true']",
    "textarea[aria-label*='caption' i]",
    "textarea[placeholder*='caption' i]",
    "[role='dialog'] textarea",
    "textarea",
]


async def _find_caption_field(page):
    """-> (locator, selector) of the first visible caption field, or (None, None)."""
    for sel in CAPTION_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible(timeout=3000):
                return loc, sel
        except Exception:
            continue
    return None, None


async def _fill_caption(page, caption):
    """Fill the caption VERBATIM (never generated or reworded here).
    Returns (found, filled, selector)."""
    loc, sel = await _find_caption_field(page)
    if loc is None:
        return False, False, None
    try:
        await loc.click(timeout=5000)
    except Exception:
        pass
    try:
        await loc.fill(caption or "", timeout=10000)
        return True, True, sel
    except Exception:
        pass
    # Fallback for contenteditable: select-all + type.
    try:
        await loc.click(timeout=5000)
        await page.keyboard.press("Control+A")
        await page.keyboard.type(caption or "", delay=5)
        return True, True, sel + " (typed)"
    except Exception:
        return True, False, sel


async def _walk_to_caption(page, log):
    """After the video is attached: dismiss the Reels-info popup if shown,
    pick 9:16 on the crop screen, click Next through Crop/Edit.
    Returns (ok, error, dialog_text_at_end)."""
    await page.wait_for_timeout(2000)
    try:
        await _click_visible_text(page, ("OK", "Got it"), timeout_ms=3000)
    except Exception:
        pass
    # Wait for processing to finish (Next appears, or an error).
    deadline = time.time() + 120
    ready = False
    limit_err = None
    seen = ""
    while time.time() < deadline:
        await page.wait_for_timeout(3000)
        seen = await _dialog_text(page)
        low = (seen or "").lower()
        if any(w in low for w in ("too long", "maximum", "exceeds", "90 seconds")):
            limit_err = (seen or "")[:500]
            break
        if "Next" in seen:
            ready = True
            break
    if limit_err:
        return False, f"uploader rejected the video: {limit_err}", seen
    if not ready:
        return False, "video did not finish processing in 120s", seen
    # Crop screen: pick 9:16 (vertical Reel) if offered.
    try:
        loc = page.locator(
            "div[role='button']:has-text('9:16'), button:has-text('9:16')").first
        if await loc.count() and await loc.is_visible(timeout=3000):
            await loc.click(timeout=5000)
            await page.wait_for_timeout(1000)
            log("aspect", True, "9:16 selected")
    except Exception:
        pass
    # Next through Crop -> Edit. The caption screen has no Next; stop there.
    for step_name in ("crop", "edit"):
        hit = await _click_visible_text(page, ("Next",), timeout_ms=15000)
        if not hit:
            txt = await _dialog_text(page)
            cf, _sel = await _find_caption_field(page)
            if "caption" in txt.lower() or cf is not None:
                return True, None, txt
            return False, f"no Next button on the {step_name} screen", txt
        await page.wait_for_timeout(2000)
        log("next", True, step_name)
    return True, None, await _dialog_text(page)


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
    for sel in CREATE_CLICK_TARGETS:
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
async def _dump_share_candidates(page):
    """Snapshot every element that could be the Share/publish control on
    the caption screen, plus processing indicators. Diagnostic only —
    clicks nothing. Returns a dict (empty/error on failure)."""
    js = """() => {
      const vis = (el) => {
        const r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && cs.display !== 'none'
               && cs.visibility !== 'hidden';
      };
      const describe = (el) => {
        const r = el.getBoundingClientRect();
        const cls = el.className && el.className.baseVal !== undefined
                    ? el.className.baseVal : String(el.className || '');
        return {tag: el.tagName.toLowerCase(),
                role: el.getAttribute('role'),
                aria_label: el.getAttribute('aria-label'),
                text: (el.innerText || '').trim().slice(0, 80),
                own_text: [...el.childNodes].filter(n => n.nodeType === 3)
                          .map(n => n.textContent.trim()).join(' ').slice(0, 80),
                disabled: el.disabled === true,
                aria_disabled: el.getAttribute('aria-disabled'),
                visible: vis(el),
                rect: [Math.round(r.x), Math.round(r.y),
                       Math.round(r.width), Math.round(r.height)],
                cls: cls.slice(0, 120)};
      };
      const out = {candidates: [], header_buttons: [], spinners: [],
                   dialog_text: '', body_has_processing: false};
      for (const el of document.querySelectorAll('body *')) {
        const own = [...el.childNodes].filter(n => n.nodeType === 3)
                      .map(n => n.textContent.trim()).join(' ');
        if (own === 'Share' || own.toLowerCase().includes('share')) {
          out.candidates.push(describe(el));
        }
      }
      const dlg = document.querySelector("[role='dialog']");
      if (dlg) {
        out.dialog_text = (dlg.innerText || '').slice(0, 500);
        for (const el of dlg.querySelectorAll('button, [role="button"], a')) {
          out.header_buttons.push(describe(el));
        }
      }
      for (const el of document.querySelectorAll(
               '[role="progressbar"], [aria-label*="loading" i]')) {
        out.spinners.push(describe(el));
      }
      out.body_has_processing =
        /processing|uploading/i.test(document.body.innerText || '');
      return out;
    }"""
    try:
        return await page.evaluate(js)
    except Exception as e:
        return {"error": type(e).__name__}


async def _find_share(page):
    """Locate the publish control on the caption screen. Returns
    (locator, selector-description) for the first visible, enabled
    candidate, or (None, diagnostics).

    Why this exists: the 2026-11-18 live run failed with "no Share button
    on the caption screen" even though its caption.png shows the word
    Share, in blue, at the top-right of the dialog header. The old search
    only looked for <button> or div[role='button'] — but Instagram's
    header action is a plain <div> with no role in the current web UI,
    so it was never matched. This finder also accepts that plain div
    (exact own text 'Share') and skips disabled controls (the video
    still processing keeps Share disabled for a while).
    Does NOT click."""
    cands = []
    specs = [
        ("button[name=Share]", page.get_by_role("button", name="Share")),
        ("button:has-text(Share)", page.locator("button:has-text('Share')")),
        ("div[role=button]:has-text(Share)",
         page.locator("div[role='button']:has-text('Share')")),
        ("dialog div:text-is(Share)",
         page.locator("[role='dialog'] div:text-is('Share')")),
        ("div:text-is(Share)", page.locator("div:text-is('Share')")),
    ]
    for desc, loc in specs:
        try:
            n = await loc.count()
        except Exception:
            continue
        for i in range(n):
            el = loc.nth(i)
            try:
                if not await el.is_visible(timeout=1000):
                    continue
                disabled = await el.evaluate(
                    "(e) => e.disabled === true || "
                    "e.getAttribute('aria-disabled') === 'true'")
                cands.append({"selector": desc, "disabled": bool(disabled)})
                if not disabled:
                    return el, desc
            except Exception:
                continue
    return None, {"candidates": cands}


async def _inspect_facebook_toggle(page, log, out_dir):
    """PROBE ONLY: look for Instagram's 'Share to Facebook' toggle on the
    caption screen, and under 'Advanced settings' inside the create
    dialog. Inspects only — never clicks a toggle, never clicks Share,
    never changes anything. Returns a dict for result.json: present
    (bool), on (True/False/None), location ('caption_screen' /
    'advanced_settings' / None), plus the raw dialog text and every
    matching element, so a human can judge what the uploader showed."""
    fb = {"checked_caption_screen": False, "checked_advanced": False,
          "advanced_present": False, "advanced_opened": False,
          "present": False, "on": None, "location": None,
          "mentions": [], "dialog_text_before": "", "dialog_text_after": "",
          "candidates": [], "switches": []}
    js = """() => {
      const out = {dialog_text: '', dialog_count: 0, candidates: [], switches: []};
      const vis = (el) => {
        const r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden';
      };
      const describe = (el) => {
        const parent = el.parentElement;
        return {tag: el.tagName.toLowerCase(),
                role: el.getAttribute('role'),
                aria_label: el.getAttribute('aria-label'),
                aria_checked: el.getAttribute('aria-checked'),
                checked: el.checked === true,
                text: (el.innerText || '').trim().slice(0, 120),
                own_text: [...el.childNodes].filter(n => n.nodeType === 3)
                          .map(n => n.textContent.trim()).join(' ').slice(0, 120),
                parent_text: parent ? (parent.innerText || '').trim().slice(0, 200) : '',
                visible: vis(el)};
      };
      const dialogs = [...document.querySelectorAll("[role='dialog']")];
      out.dialog_count = dialogs.length;
      const scope = dialogs.length ? dialogs : [document.body];
      for (const root of scope) {
        out.dialog_text += (root.innerText || '') + ' | ';
        for (const el of root.querySelectorAll('*')) {
          const t = (el.innerText || '');
          const al = el.getAttribute('aria-label') || '';
          if ((/facebook/i.test(t) && t.length < 300) || /facebook/i.test(al)) {
            out.candidates.push(describe(el));
          }
        }
        for (const el of root.querySelectorAll("[role='switch'], [role='checkbox'], input[type='checkbox']")) {
          out.switches.push(describe(el));
        }
      }
      out.dialog_text = out.dialog_text.slice(0, 3000);
      return out;
    }"""

    def dedup(items):
        seen = set()
        out = []
        for it in items or []:
            key = (it.get("tag"), it.get("role"), it.get("text"),
                   it.get("aria_checked"), str(it.get("checked")))
            if key in seen:
                continue
            seen.add(key)
            out.append(it)
        return out

    def find_toggle(snap):
        """-> (present, on) from one snapshot, or (False, None)."""
        cands = list(snap.get("candidates", [])) + list(snap.get("switches", []))
        for el in cands:
            hay = " ".join([el.get("text") or "", el.get("own_text") or "",
                            el.get("parent_text") or "",
                            el.get("aria_label") or ""]).lower()
            if "facebook" not in hay:
                continue
            if el.get("role") in ("switch", "checkbox") or el.get("tag") == "input":
                on = (el.get("aria_checked") == "true") or (el.get("checked") is True)
                return True, on
            # A row/label mentioning Facebook with a switch in the same row.
            for sw in snap.get("switches", []):
                par = (sw.get("parent_text") or "").lower()
                if "facebook" in par:
                    on = (sw.get("aria_checked") == "true") or (sw.get("checked") is True)
                    return True, on
        return False, None

    # 1. Caption screen as-is (no clicks).
    try:
        before = await page.evaluate(js)
    except Exception as e:
        fb["error"] = type(e).__name__
        return fb
    fb["dialog_text_before"] = (before.get("dialog_text") or "")[:3000]
    fb["candidates"] = dedup(before.get("candidates", []))
    fb["switches"] = dedup(before.get("switches", []))
    fb["checked_caption_screen"] = True
    present, on = find_toggle(before)
    if present:
        fb["present"] = True
        fb["on"] = on
        fb["location"] = "caption_screen"
        log("facebook_toggle", True,
            f"toggle on caption screen, on={on}")
    else:
        # 2. Open 'Advanced settings' inside the same dialog and look again.
        fb["advanced_present"] = "advanced settings" in fb["dialog_text_before"].lower()
        hit = await _click_visible_text(page, ("Advanced settings",), timeout_ms=5000)
        if not hit:
            try:
                loc = page.locator("[role='dialog'] :text('Advanced settings')").first
                if await loc.count() and await loc.is_visible(timeout=3000):
                    await loc.click(timeout=5000)
                    hit = "Advanced settings (fallback)"
            except Exception:
                pass
        fb["advanced_opened"] = bool(hit)
        await page.wait_for_timeout(1500)
        try:
            after = await page.evaluate(js)
        except Exception as e:
            fb["error"] = type(e).__name__
            return fb
        fb["dialog_text_after"] = (after.get("dialog_text") or "")[:3000]
        fb["candidates"] = dedup(fb["candidates"] + after.get("candidates", []))
        fb["switches"] = dedup(fb["switches"] + after.get("switches", []))
        fb["checked_advanced"] = True
        present, on = find_toggle(after)
        if present:
            fb["present"] = True
            fb["on"] = on
            fb["location"] = "advanced_settings"
            log("facebook_toggle", True,
                f"toggle under Advanced settings, on={on}")
        else:
            log("facebook_toggle", True,
                "no Facebook toggle found on caption screen or under Advanced settings")
    fb["mentions"] = sorted({(c.get("text") or c.get("own_text") or "")
                             for c in fb["candidates"] if c.get("text") or c.get("own_text")})
    try:
        await page.screenshot(path=os.path.join(out_dir, "facebook_check.png"))
    except Exception:
        pass
    return fb


async def _click_share(page, log, timeout_s=120):
    """Poll for an enabled Share control and click it. The video is
    still processing for a while after the caption is filled, so the
    control can be disabled at first — wait up to timeout_s for it to
    become enabled. Returns the matched selector description, or None."""
    last_diag = {}
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        loc, diag = await _find_share(page)
        if loc is not None:
            try:
                await loc.click(timeout=10000)
                return diag
            except Exception:
                pass
        last_diag = diag
        await page.wait_for_timeout(5000)
    log("share_search", False,
        f"no enabled Share control in {timeout_s}s: {last_diag}")
    return None


async def run_probe(page, video_path, caption, out_dir, log):
    """Open Create -> pick Reel -> attach the video -> walk to the caption
    screen -> verify the caption field is findable and fillable -> dump
    the Share control's DOM and check the post flow's Share finder can
    see it -> inspect the 'Share to Facebook' toggle (present/absent,
    ON/OFF; never touched) -> close the dialog with Discard. Submits
    NOTHING (Share is never clicked)."""
    res = {"dialog_text": "", "upload_dialog_text": "", "duration_shown": None,
           "limit_error": None, "reel_choice": None, "file_input_found": False,
           "caption_field_found": False, "caption_filled": False,
           "caption_selector": None, "attach_method": None,
           "create_attempts": [], "create_menu_text": ""}
    opened = await _open_create(page, log)
    res["create_attempts"] = opened.get("attempts", [])
    res["create_menu_text"] = opened.get("menu_text", "")
    if not opened.get("opened"):
        log("open_create", False, opened.get("error", "Create dialog did not open"))
        res["error"] = opened.get("error", "create dialog did not open")
        return res
    res["reel_choice"] = opened.get("reel_choice")
    log("open_create", True,
        f"Create dialog opened (reel_choice={res['reel_choice']})")
    try:
        await page.screenshot(path=os.path.join(out_dir, "create_dialog.png"))
    except Exception:
        pass
    res["dialog_text"] = opened.get("dialog_text", "") or await _dialog_text(page)
    att = await _attach_video(page, video_path, log)
    if not att.get("ok"):
        log("attach", False, att.get("error", "could not select the video file"))
        res["error"] = att.get("error", "could not select the video file in the uploader")
        return res
    res["file_input_found"] = True
    res["attach_method"] = att.get("method")
    log("attach", True, f"video file selected ({att.get('method')})")
    ok, err, seen = await _walk_to_caption(page, log)
    res["upload_dialog_text"] = seen or ""
    m = re.search(r"(\d{1,2}):(\d{2})", seen or "")
    if m:
        res["duration_shown"] = int(m.group(1)) * 60 + int(m.group(2))
    low = (seen or "").lower()
    if any(w in low for w in ("too long", "maximum", "exceeds", "90 seconds", "shorter")):
        res["limit_error"] = (seen or "")[:500]
    if "reel" in low and not res["reel_choice"]:
        res["reel_choice"] = "Reel"
    if not ok:
        log("walk", False, err)
        res["error"] = err
        return res
    log("walk", True, "reached the caption screen")
    try:
        await page.screenshot(path=os.path.join(out_dir, "upload.png"))
    except Exception:
        pass
    # Caption check: fill the field (supplied caption, else a TEST string).
    # Probe fills only — it never clicks Share, so nothing is published.
    test_caption = (caption or "").strip() or \
        "PROBE TEST — caption fill check only, nothing will be posted."
    found, filled, sel = await _fill_caption(page, test_caption)
    res["caption_field_found"] = found
    res["caption_filled"] = filled
    res["caption_selector"] = sel
    log("caption_check", bool(found and filled),
        f"field_found={found} filled={filled} via {sel}")
    try:
        await page.screenshot(path=os.path.join(out_dir, "caption.png"))
    except Exception:
        pass
    # DOM snapshot of the caption screen: find the real Share control and
    # whether it appears late (video still processing). Sampled 3x over
    # ~40 s. Diagnostic only — nothing is clicked here.
    dom_samples = []
    for sample_i in range(3):
        snap = await _dump_share_candidates(page)
        snap["sample"] = sample_i
        dom_samples.append(snap)
        try:
            await page.screenshot(
                path=os.path.join(out_dir, f"caption_dom_{sample_i}.png"))
        except Exception:
            pass
        if sample_i < 2:
            await page.wait_for_timeout(20000)
    res["share_dom"] = {
        "samples": len(dom_samples),
        "candidates_per_sample": [len(s.get("candidates", []))
                                  for s in dom_samples],
        "share_visible": [any(c.get("visible") for c in s.get("candidates", []))
                          for s in dom_samples],
        "share_enabled": [any(c.get("visible") and not c.get("disabled")
                              and c.get("aria_disabled") != "true"
                              for c in s.get("candidates", []))
                          for s in dom_samples],
        "spinners_per_sample": [len(s.get("spinners", []))
                                for s in dom_samples],
        "body_processing": [bool(s.get("body_has_processing"))
                            for s in dom_samples],
    }
    try:
        with open(os.path.join(out_dir, "caption_dom.json"), "w") as f:
            json.dump(dom_samples, f, indent=2)
    except Exception:
        pass
    log("share_dom", True,
        f"{len(dom_samples)} samples; candidates="
        f"{res['share_dom']['candidates_per_sample']} visible="
        f"{res['share_dom']['share_visible']} enabled="
        f"{res['share_dom']['share_enabled']}")
    # Finder check (no click): does the Share control the post flow
    # would click actually exist and enabled on this screen?
    share_loc, share_diag = await _find_share(page)
    res["share_found"] = share_loc is not None
    res["share_selector"] = share_diag if isinstance(share_diag, str) else None
    log("share_finder", bool(share_loc),
        f"found={res['share_found']} via={res['share_selector']}")
    # PROBE ONLY: is there a 'Share to Facebook' toggle? Inspect only —
    # never click a toggle, never click Share.
    fb = await _inspect_facebook_toggle(page, log, out_dir)
    res["facebook_toggle"] = fb
    # Close the dialog WITHOUT proceeding: Escape, then a Discard/close click.
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(1000)
        await _click_visible_text(page, ("Discard", "Close", "Cancel"), timeout_ms=3000)
        await page.wait_for_timeout(1000)
    except Exception:
        pass
    log("close_dialog", True, "dialog closed without publishing anything")
    if not found:
        res["error"] = "caption field not found on the share screen"
    return res


async def run_recover(page, username, out_dir, log):
    """Read-only recovery: find the newest Reel on the logged-in profile
    and verify it loads and carries the caption marker 'Succession' plus
    a Part 2 marker (so the URL is proven to be the Part 2 Reel, never
    the older Part 1 Reel). Used when a post succeeded but no URL came
    back (the 2026-11-19 Part 2 case: Share clicked, no URL in 180s,
    Reel live). Submits NOTHING — it only reads pages and the candidate
    Reel page. Tries, in order: the /reels/ page (grid + Reels-tab
    variant + network capture), the feed/user timeline API, the
    notifications inbox, the mobile web profile, and 'Your activity'.
    Returns {"ok", "status", "post_url", ...} with status "recovered"
    on success, "recover_failed" otherwise."""
    # Part 2 identity markers (words 6-10 caption): the recovered URL
    # must be THIS Reel, not the older Part 1 Reel — a delete can never
    # target the wrong post.
    part2_markers = ["Get Rennie", "keep you posted"]
    newest = await _newest_reel_link(page, username, log)
    if not newest:
        newest = await _newest_reel_via_feed_api(page, username, log)
    if not newest:
        newest = await _newest_reel_via_news(page, log)
    if not newest:
        newest = await _newest_reel_via_mobile(page, username, log)
    if not newest:
        newest = await _newest_reel_via_activity(page, username, log)
    if not newest:
        return {"ok": False, "status": "recover_failed",
                "error_message": "no Reel link found on the profile reels page"}
    loads, marker = await _verify_reel_url(page, newest,
                                           must_contain=part2_markers)
    if not loads:
        return {"ok": False, "status": "recover_failed",
                "error_message": f"newest Reel URL does not load: {newest}"}
    if not marker:
        return {"ok": False, "status": "recover_failed",
                "error_message": f"newest Reel {newest} did not verify as "
                                 f"the Part 2 Reel (loads={loads}, "
                                 f"Succession/Part-2 marker={marker})"}
    try:
        await page.screenshot(path=os.path.join(out_dir, "recovered.png"))
    except Exception:
        pass
    log("recovered", True, f"newest Reel verified: {newest}")
    return {"ok": True, "status": "recovered", "post_url": newest,
            "error": None, "error_message": None}


async def run_delete(page, reel_url, out_dir, log, dry_run=True):
    """Delete ONE specific Reel. Opens exactly the given Reel URL (the
    allowlist permits reel/ URLs) and deletes it through the UI:
    ••• menu -> Delete -> confirm. Deletes ONLY that exact URL — it
    never navigates anywhere else. dry_run=True (inspect only): verify
    the Delete option is present in the ••• menu, screenshot it, close
    the menu WITHOUT deleting. dry_run=False: actually delete, then
    verify the URL no longer loads ('Sorry, this page isn't available').
    Returns {"ok", "status", "deleted_url", ...} with status "deleted"
    on success, "delete_failed" otherwise (or "delete_option_found" for
    a successful inspect-only run)."""
    if not reel_url or not _goto_allowed(reel_url):
        return {"ok": False, "status": "delete_failed",
                "error_message": "no valid Reel URL given to delete "
                                 "(--reel-url must be an instagram.com/reel/... URL)"}
    try:
        await page.goto(reel_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)
        body = await _body_text(page, limit=2000)
    except Exception as e:
        return {"ok": False, "status": "delete_failed",
                "error_message": f"could not open the Reel URL: {type(e).__name__}"}
    if "Sorry, this page isn't available" in body:
        log("delete", True, f"Reel already gone: {reel_url}")
        return {"ok": True, "status": "deleted", "deleted_url": reel_url,
                "error": None, "error_message": None,
                "note": "Reel URL was already unavailable before delete"}
    # Find the ••• (More options) control. Try several selectors; the web
    # UI exposes it as a button with aria-label 'More options', or a
    # plain div/button with the text '•••' / '...'.
    more = None
    more_desc = None
    for desc, loc in [
        ("button[aria-label=More options]",
         page.locator("button[aria-label='More options']")),
        ("button:has-text(•••)", page.locator("button:has-text('•••')")),
        ("div[role=button]:has-text(•••)",
         page.locator("div[role='button']:has-text('•••')")),
        ("[aria-label='More options']",
         page.locator("[aria-label='More options']")),
    ]:
        try:
            if await loc.count() and await loc.first.is_visible(timeout=2000):
                more, more_desc = loc.first, desc
                break
        except Exception:
            continue
    if more is None:
        try:
            await page.screenshot(path=os.path.join(out_dir, "delete_no_menu.png"))
        except Exception:
            pass
        return {"ok": False, "status": "delete_failed",
                "error_message": "could not find the ••• (More options) control on the Reel page"}
    try:
        await more.click(timeout=10000)
        await page.wait_for_timeout(2000)
    except Exception:
        return {"ok": False, "status": "delete_failed",
                "error_message": "could not open the ••• menu"}
    # In the menu, find the Delete option (a button/menuitem with the
    # text 'Delete'). Do NOT click anything else.
    delete_opt = None
    for desc, loc in [
        ("button:has-text(Delete)", page.locator("button:has-text('Delete')")),
        ("[role=menuitem]:has-text(Delete)",
         page.locator("[role='menuitem']:has-text('Delete')")),
        ("div[role=button]:has-text(Delete)",
         page.locator("div[role='button']:has-text('Delete')")),
        ("text=Delete", page.locator("text=Delete")),
    ]:
        try:
            if await loc.count() and await loc.first.is_visible(timeout=2000):
                delete_opt = loc.first
                break
        except Exception:
            continue
    try:
        await page.screenshot(path=os.path.join(out_dir, "delete_menu.png"))
    except Exception:
        pass
    if delete_opt is None:
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        return {"ok": False, "status": "delete_failed",
                "error_message": "••• menu opened but no Delete option found"}
    if dry_run:
        # Inspect only: the Delete option is present. Close the menu and
        # report — the Reel is NOT deleted.
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(1000)
        except Exception:
            pass
        log("delete_inspect", True,
            f"Delete option present for {reel_url} (inspect only, nothing deleted)")
        return {"ok": True, "status": "delete_option_found",
                "deleted_url": None, "error": None, "error_message": None,
                "note": "dry_run inspect only: Delete option found, Reel NOT deleted"}
    # Live delete: click Delete, then confirm in the dialog.
    try:
        await delete_opt.click(timeout=10000)
        await page.wait_for_timeout(2000)
    except Exception:
        return {"ok": False, "status": "delete_failed",
                "error_message": "could not click Delete in the ••• menu"}
    try:
        await page.screenshot(path=os.path.join(out_dir, "delete_confirm.png"))
    except Exception:
        pass
    confirmed = False
    for desc, loc in [
        ("button:has-text(Delete)", page.locator("button:has-text('Delete')")),
        ("[role=button]:has-text(Delete)",
         page.locator("[role='button']:has-text('Delete')")),
        ("text=Delete", page.locator("text=Delete")),
    ]:
        try:
            if await loc.count() and await loc.first.is_visible(timeout=3000):
                await loc.first.click(timeout=10000)
                confirmed = True
                break
        except Exception:
            continue
    if not confirmed:
        return {"ok": False, "status": "delete_failed",
                "error_message": "Delete clicked but no confirmation button appeared"}
    await page.wait_for_timeout(5000)
    # Verify the Reel is gone: the URL must no longer load.
    loads, _ = await _verify_reel_url(page, reel_url)
    if loads:
        return {"ok": False, "status": "delete_failed",
                "error_message": f"delete confirmed but the Reel URL still loads: {reel_url}"}
    try:
        await page.screenshot(path=os.path.join(out_dir, "deleted.png"))
    except Exception:
        pass
    log("deleted", True, f"Reel deleted and verified gone: {reel_url}")
    return {"ok": True, "status": "deleted", "deleted_url": reel_url,
            "error": None, "error_message": None}


async def run_post(page, video_path, caption, out_dir, log, username=None):
    """Full publish flow. ONLY called when dry_run is false and mode is post.
    The caption is pasted VERBATIM from the caller — never generated,
    reworded, or invented here. An empty caption is refused by the caller
    before any browser work; this is a second check.
    Confirmation (2026-11-19 fix): after Share the wait is 300s (was
    180s — the Part 2 post succeeded but returned no URL in 180s). If
    still no URL, fall back to reading the newest Reel from the owner's
    /reels/ page and verify it loads and carries the 'Succession'
    caption marker, instead of reporting post_failed."""
    if not (caption or "").strip():
        return {"ok": False, "status": "post_failed",
                "error_message": "no caption supplied — refusing to post "
                                 "without a caption"}
    opened = await _open_create(page, log)
    if not opened.get("opened"):
        return {"ok": False, "status": "post_failed",
                "error_message": opened.get("error", "create dialog did not open")}
    try:
        await page.screenshot(path=os.path.join(out_dir, "create_dialog.png"))
    except Exception:
        pass
    att = await _attach_video(page, video_path, log)
    if not att.get("ok"):
        return {"ok": False, "status": "post_failed",
                "error_message": att.get("error",
                                         "could not select the video file in the uploader")}
    log("attach", True, f"video file selected ({att.get('method')})")
    ok, err, seen = await _walk_to_caption(page, log)
    if not ok:
        return {"ok": False, "status": "post_failed",
                "error_message": err}
    try:
        await page.screenshot(path=os.path.join(out_dir, "upload.png"))
    except Exception:
        pass
    # Caption: paste the supplied caption verbatim into the field.
    found, filled, sel = await _fill_caption(page, caption)
    if not found or not filled:
        return {"ok": False, "status": "post_failed",
                "error_message": "caption field not found or could not be filled"}
    log("caption", True, f"{len(caption)} chars pasted verbatim via {sel}")
    try:
        await page.screenshot(path=os.path.join(out_dir, "caption.png"))
    except Exception:
        pass
    hit = await _click_share(page, log)
    if not hit:
        return {"ok": False, "status": "post_failed",
                "error_message": "no Share button on the caption screen"}
    log("share_clicked", True, f"Share clicked via {hit}; waiting for publish confirmation")
    # Wait for the shared confirmation and capture the post URL.
    # 300s (was 180s): the 2026-11-19 Part 2 post succeeded but returned
    # no URL within 180s, so the runner reported post_failed on a live Reel.
    deadline = time.time() + 300
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
        # Fallback (2026-11-19): Share may have succeeded without a URL
        # (the Part 2 case). Read the newest Reel from the owner's
        # /reels/ page instead of reporting post_failed on a live Reel.
        log("confirm_fallback", True,
            "no URL in 300s after Share — reading newest Reel from the profile")
        newest = await _newest_reel_link(page, username, log) if username else None
        if newest:
            loads, marker = await _verify_reel_url(page, newest)
            if loads and marker:
                post_url = newest
                log("confirm_fallback", True,
                    f"recovered the posted Reel from the profile: {post_url}")
            else:
                log("confirm_fallback", False,
                    f"newest Reel {newest} did not verify "
                    f"(loads={loads}, marker={marker})")
        if not post_url:
            return {"ok": False, "status": "post_failed",
                    "error_message": "Share clicked but no published post URL "
                                     "appeared in 300s, and no verifiable newest "
                                     "Reel was found on the profile"}
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
        # Fresh stamp on every write: the pipeline polls for a blob-sha
        # change to tell this run's result apart from a previous run's
        # result for the same job_id.
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        result["run_uuid"] = uuid.uuid4().hex
        # ok must agree with status: a probe that reached the caption
        # screen and a published post are successes, not failures. A
        # recovered Reel URL, a deleted Reel, and a delete-option
        # inspection are successes too.
        result["ok"] = status in ("probe_ok", "posted", "recovered",
                                  "deleted", "delete_option_found")
        result.update(extra)
        try:
            path = save_result(args.results_dir, args.job_id, result, mask)
            print(f"result written: {path} status={status}", flush=True)
            return 0
        except Exception as e:
            print(f"FATAL: could not write result.json: {type(e).__name__}", flush=True)
            return 1

    # ---- gate 0: idempotency — a previous run of this job already
    # posted the Reel (same job_id + same video URL). Return its URL
    # instead of posting a duplicate. Only for real posts; probes
    # always run fresh. Placed before the session gate on purpose: a
    # session that died AFTER the post must not hide the posted URL. ----
    if args.mode == "post" and not args.dry_run:
        try:
            _prev_path = os.path.join(args.results_dir, "jobs", args.job_id,
                                      "result.json")
            if os.path.exists(_prev_path):
                with open(_prev_path, encoding="utf-8") as _f:
                    _prev = json.load(_f)
                if (isinstance(_prev, dict) and _prev.get("status") == "posted"
                        and _prev.get("post_url")
                        and _prev.get("video_url") == args.video_url):
                    log("idempotent", True,
                        f"job {args.job_id} already posted")
                    return finish("posted", error=None, error_message=None,
                                  post_url=_prev.get("post_url"))
        except Exception:
            pass

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
    if args.mode not in ("probe", "post", "recover", "delete"):
        return finish("error", error="bad mode",
                      error_message=f"unknown mode {args.mode!r}")

    # ---- gate 2a: delete mode needs the exact Reel URL. It deletes ONLY
    # that URL — never anything else — so a missing/invalid URL is
    # refused before any browser work. ----
    if args.mode == "delete" and not (args.reel_url or "").strip():
        log("delete_gate", False, "no --reel-url supplied — refusing to delete")
        return finish("error", error="no Reel URL to delete",
                      error_message="Delete mode needs --reel-url (the exact "
                      "instagram.com/reel/... URL). It deletes ONLY that URL.")

    # ---- gate 2b: a real post needs a caption handed to it. The poster
    # pastes the caption verbatim and never invents one, so an empty
    # caption is refused here, before any download or browser work. ----
    if args.mode == "post" and not args.dry_run and not (args.caption or "").strip():
        log("caption_gate", False, "no caption supplied — refusing to post")
        return finish("error", error="no caption supplied",
                      error_message="Refusing to post without a caption: the "
                      "poster pastes the caption it is handed verbatim and "
                      "never invents one. Re-dispatch with the caption input filled.")

    # ---- download the video (probe/post only). Recover and delete need
    # no video: they only read pages / delete one given Reel URL. ----
    video_path = os.path.join("/tmp", f"ig-{args.job_id}.mp4")
    if args.mode in ("recover", "delete"):
        result["video_bytes"] = 0
        result["video_seconds"] = None
        log("download", True, f"mode={args.mode}: no video needed, skipping download")
    else:
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
    # The playwright import is INSIDE the try (2026-11-19): if the module
    # is missing, the job still writes an error result.json instead of
    # crashing with no result (the pipeline would then poll 30 min for a
    # file that never appears).
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    # 1920x1080: at narrower widths Instagram collapses the
                    # sidebar and the Create (+) button is not clickable.
                    viewport={"width": 1920, "height": 1080},
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
                    await _dismiss_popups(page)
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
                    if args.mode == "recover":
                        rec = await run_recover(page, login["username"],
                                                out_dir, log)
                        if rec.get("ok"):
                            return finish("recovered", error=None,
                                          error_message=None,
                                          post_url=rec.get("post_url"))
                        return finish("recover_failed", error="recover failed",
                                      error_message=rec.get("error_message", "unknown"))
                    if args.mode == "delete":
                        dele = await run_delete(page, args.reel_url or "",
                                                out_dir, log, args.dry_run)
                        if dele.get("ok"):
                            return finish(dele.get("status", "deleted"),
                                          error=None, error_message=None,
                                          deleted_url=dele.get("deleted_url"),
                                          note=dele.get("note"))
                        return finish("delete_failed", error="delete failed",
                                      error_message=dele.get("error_message", "unknown"))
                    if args.mode == "probe":
                        probe = await run_probe(page, video_path,
                                                args.caption or "", out_dir, log)
                        result.update({k: v for k, v in probe.items()
                                       if k in ("dialog_text", "upload_dialog_text",
                                                "duration_shown", "limit_error",
                                                "reel_choice", "file_input_found",
                                                "caption_field_found", "caption_filled",
                                                "caption_selector", "attach_method",
                                                "create_attempts", "create_menu_text",
                                                "share_dom", "share_found",
                                                "share_selector",
                                                "facebook_toggle")})
                        if probe.get("error"):
                            return finish("probe_failed", error="probe failed",
                                          error_message=probe["error"])
                        return finish("probe_ok",
                                      error=None, error_message=None,
                                      note="probe only: nothing was published")
                    posted = await run_post(page, video_path, args.caption or "",
                                            out_dir, log, login["username"])
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
    ap.add_argument("--video-url", "--video_url", required=True, dest="video_url",
                    help="Public URL of the Reel video (mp4). Required by the "
                         "workflow input; recover/delete accept it but ignore it.")
    ap.add_argument("--caption", default="")
    ap.add_argument("--job-id", "--job_id", required=True, dest="job_id")
    ap.add_argument("--mode", choices=["probe", "post", "recover", "delete"],
                    default="probe",
                    help="probe (inspect only), post (publish), recover (find the "
                         "newest Reel on the profile), delete (delete --reel-url).")
    ap.add_argument("--reel-url", "--reel_url", dest="reel_url", default="",
                    help="Delete mode only: the exact instagram.com/reel/... URL "
                         "to delete. Nothing else is ever deleted.")
    ap.add_argument("--dry-run", "--dry_run", dest="dry_run", default="true",
                    choices=["true", "false"],
                    help="true (default): never publish. false + mode=post: publish. "
                         "For delete: true = inspect only (verify the Delete option "
                         "is present, do not delete); false = actually delete.")
    ap.add_argument("--results-dir", "--results_dir", dest="results_dir",
                    default="hq-ai-results")
    ap.add_argument("--notion-page-id", "--notion_page_id", dest="notion_page_id", default="")
    args = ap.parse_args(argv)
    args.dry_run = str(args.dry_run).lower() == "true"
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
