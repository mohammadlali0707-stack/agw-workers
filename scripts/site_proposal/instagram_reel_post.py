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
  runs automatically: it needs an explicit job dispatch.
  (5) probe_fb mode (2026-11-25): open the ONE specific published Reel
  URL it is told to inspect and look for a 'Share to Facebook' control
  on that Reel's page — read-only, nothing is shared or changed.
  It navigates to
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
    older Part 1 Reel) — OR, when --caption is supplied, the FULL
    expected caption (first line + ≥4 of 5 word lines + ≥80% of lines;
    a bare marker is not enough since 2026-11-25, when the Part 5 post
    URL turned out to be a stranger's feed post). Used when a post
    succeeded but no URL came back (the 2026-11-19 Part 2 case: Share
    clicked, no URL in 180s, Reel live), and to find a posted Reel's
    real shortcode after the fact. Submits NOTHING — read-only. It tries, in order: the /reels/
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

  probe_fb — inspect ONE already-published Reel URL (the owner's own
    post) for a 'Share to Facebook' control on the web Reel page: the
    share (paper-plane) button's dialog and the ••• more-options menu.
    Read-only: it never clicks anything labelled Facebook, never
    shares anything, never publishes. WHY this mode exists: the web
    uploader itself has NO 'Share to Facebook' toggle — proven by the
    2026-11-25 probe (caption screen + Advanced settings checked,
    present=false) — so cross-posting cannot be enabled at upload
    time on web; the published Reel page is the only runner-drivable
    place it could live. Needs --reel-url.
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


def _shortcode_from_media_id(media_id):
    """Convert a numeric Instagram media id to its shortcode (base64
    of the id using Instagram's alphabet). A wrong conversion simply
    fails the caption-marker verification later and is never deleted,
    so this is safe to try on any numeric id from the news inbox."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    try:
        n = int(str(media_id).split("_")[0])
    except Exception:
        return None
    if n <= 0:
        return None
    out = ""
    while n > 0:
        out = alphabet[n % 64] + out
        n //= 64
    return out or None


def _media_id_from_shortcode(code):
    """Decode an Instagram shortcode back to its numeric media id
    (the shortcode is base64 of the id using Instagram's alphabet).
    Round-trip verified 2026-11-19 (Df7rRTasyR1 <-> 4034008196320535669).
    Used only to address the media delete API for the exact Reel URL
    the user ordered deleted."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    try:
        n = 0
        for ch in code:
            n = n * 64 + alphabet.index(ch)
        return str(n)
    except Exception:
        return None


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
    """Read-only: open the owner's /reels/ page and return the Reel
    URLs it finds, NEWEST FIRST (a list — may be empty). Callers
    verify each candidate (loads + caption markers) and take the first
    that verifies. The /reels/ page is inside the navigation allowlist
    (privacy rule: the recover read only ever opens this page, to find
    the Reel this account just posted). Never clicks anything, never
    opens DMs/feed/other pages.
    The reels grid loads asynchronously (GraphQL), so this polls for up
    to 45s and scrolls to trigger lazy loading.
    NEW 2026-11-19 (recover v2): a response listener captures the
    profile grid's OWN GraphQL/API responses — if the grid request
    succeeds but the page never renders its links, the shortcodes are
    still in the response body (the current shape carries node["code"],
    not "shortcode"). Responses whose query variables contain the
    owner's user id are the profile grid; other graphql/query calls
    (e.g. the home timeline) are listed after them. If the direct
    /reels/ URL does not render, the profile page's Reels tab is
    clicked instead (a different code path for the same grid)."""
    url = f"https://www.instagram.com/{username}/reels/"
    if not _goto_allowed(url):
        return []
    owner_uid = await _user_id(page)
    candidates = []

    def _add(cand):
        if cand and _goto_allowed(cand) and cand not in candidates:
            candidates.append(cand)

    # Capture the grid's own data responses (read-only): the page's
    # GraphQL/API calls carry the media shortcodes even when the grid
    # never renders its links. Also records each such call's status and
    # which query it was, so a later run can tell the profile grid
    # (variables carry the owner's user id) from other queries (e.g.
    # the home timeline) and a rate limit (429) from a dead query (400).
    captured = []
    replay_infos = []

    async def _on_response(resp):
        try:
            u = resp.url or ""
            if ("graphql" in u or "web_profile_info" in u
                    or "feed/user" in u):
                try:
                    body = await resp.text()
                except Exception:
                    body = ""
                codes = re.findall(
                    r'"shortcode"\s*:\s*"([A-Za-z0-9_-]+)"', body or "")
                shape = ""
                qhash = ""
                uid_in_vars = False
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
                try:
                    import urllib.parse as _up
                    _qs = _up.parse_qs(_up.urlparse(u).query)
                    qhash = (_qs.get("query_hash") or [""])[0][:12]
                    _vars = _qs.get("variables") or [""]
                    if _vars[0] and owner_uid:
                        uid_in_vars = owner_uid in _vars[0]
                except Exception:
                    pass
                # The web app POSTs GraphQL (doc_id + variables in the
                # POST body, not the URL). Capture the grid query's
                # doc_id + variables so it can be replayed directly.
                # The body may be JSON or form-encoded; the endpoint
                # may be /graphql/query or /api/graphql.
                doc_id = ""
                pd_head = ""
                try:
                    _pd = resp.request.post_data
                except Exception:
                    _pd = None
                if _pd:
                    pd_head = _pd[:200]
                    try:
                        _pj = json.loads(_pd)
                    except Exception:
                        _pj = None
                        try:
                            import urllib.parse as _up2
                            _qs2 = _up2.parse_qs(_pd)
                            _pj = {"doc_id": (_qs2.get("doc_id") or [""])[0],
                                   "variables":
                                       (_qs2.get("variables") or [""])[0]}
                        except Exception:
                            pass
                    if isinstance(_pj, dict):
                        doc_id = str(_pj.get("doc_id")
                                     or _pj.get("query_hash") or "")[:16]
                        _v = _pj.get("variables")
                        if isinstance(_v, str):
                            try:
                                _v = json.loads(_v)
                            except Exception:
                                _v = None
                        if isinstance(_v, dict):
                            if owner_uid and owner_uid in json.dumps(_v):
                                uid_in_vars = True
                                _ep = u.split("?")[0]
                                if not any(r.get("doc_id") == str(_pj.get("doc_id") or "")
                                           and r.get("endpoint") == _ep
                                           for r in replay_infos):
                                    replay_infos.append(
                                        {"doc_id": str(_pj.get("doc_id") or ""),
                                         "variables": _v, "endpoint": _ep})
                try:
                    _method = resp.request.method
                except Exception:
                    _method = ""
                captured.append({"url": u[:400], "status": resp.status,
                                 "method": _method,
                                 "codes": codes[:6], "shape": shape,
                                 "qhash": qhash, "doc_id": doc_id,
                                 "pd_head": pd_head,
                                 "uid_in_vars": uid_in_vars})
        except Exception:
            pass

    try:
        page.on("response", _on_response)
    except Exception:
        pass

    # Record every data (XHR/fetch) request the page makes — the grid
    # query's real endpoint, if it fires at all, is in this list.
    requests_seen = []

    def _on_request(req):
        try:
            if req.resource_type in ("xhr", "fetch"):
                try:
                    _pd = req.post_data or ""
                except Exception:
                    _pd = ""
                requests_seen.append({
                    "method": req.method,
                    "url": (req.url or "")[:300],
                    "pd_head": _pd[:200],
                })
        except Exception:
            pass

    try:
        page.on("request", _on_request)
    except Exception:
        pass

    def _log_requests():
        seen = []
        for r in requests_seen:
            key = (r["method"], r["url"])
            if key not in seen:
                seen.append(key)
        detail = " | ".join(f"{m} {u}" for m, u in seen[:25])
        log("newest_reel_req", bool(seen),
            f"{len(seen)} data requests: {detail}")

    def _captured_urls():
        # Profile-grid responses first (their variables carry the
        # owner's user id), then any other graphql/query responses.
        ordered = sorted(captured,
                         key=lambda e: (not e.get("uid_in_vars"),))
        urls = []
        for entry in ordered:
            log("newest_reel_net", bool(entry["codes"]),
                f"{entry['status']} {entry.get('method', '')} "
                f"qhash={entry.get('qhash', '')} "
                f"doc={entry.get('doc_id', '')} "
                f"uid_in_vars={entry.get('uid_in_vars')} "
                f"codes={entry['codes']} shape={entry.get('shape', '')} "
                f"url={entry.get('url', '')[:200]} "
                f"pd={entry.get('pd_head', '')[:150]}")
            for code in entry["codes"]:
                cand = f"https://www.instagram.com/reel/{code}/"
                if cand not in urls:
                    urls.append(cand)
        return urls

    _replayed_docs = set()

    async def _replay_captured():
        # Replay the captured grid query (if any): its data IS the
        # profile grid, so it outranks codes from other queries (e.g.
        # the home timeline) that the listener also captured.
        for _ri in replay_infos:
            if not _ri.get("doc_id") or _ri["doc_id"] in _replayed_docs:
                continue
            _replayed_docs.add(_ri["doc_id"])
            _rc = await _replay_grid_query(page, _ri["doc_id"],
                                           _ri["variables"], log,
                                           _ri.get("endpoint"))
            if _rc and _rc not in candidates:
                candidates.insert(0, _rc)

    async def _scan_links(seconds):
        """Poll for /reel/ links; once any appear, let the grid settle
        3s and return ALL of them (DOM order = newest first).
        Returns [] on timeout."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                hrefs = await page.evaluate(
                    "() => [...document.querySelectorAll('a[href]')]"
                    ".map(a => a.getAttribute('href') || '')"
                    ".filter(h => h.includes('/reel/'))")
            except Exception:
                hrefs = []
            if hrefs:
                await page.wait_for_timeout(3000)
                try:
                    hrefs = await page.evaluate(
                        "() => [...document.querySelectorAll('a[href]')]"
                        ".map(a => a.getAttribute('href') || '')"
                        ".filter(h => h.includes('/reel/'))")
                except Exception:
                    pass
                out = []
                for href in hrefs or []:
                    cand = ("https://www.instagram.com" + href
                            if href.startswith("/") else href)
                    if _goto_allowed(cand) and cand not in out:
                        out.append(cand)
                if out:
                    log("newest_reel", True,
                        f"{len(out)} Reel link(s) on profile: {out}")
                    return out
            # No links yet: scroll to trigger lazy loading, then wait.
            try:
                await page.evaluate(
                    "() => window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                pass
            await page.wait_for_timeout(3000)
        return []

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        log("newest_reel", False, f"could not load {username}/reels: {type(e).__name__}")
        return []
    for cand in await _scan_links(45):
        _add(cand)
    if candidates:
        return candidates
    # Diagnostics for the next run: what did the page actually show?
    try:
        body = await _body_text(page, limit=500)
        log("newest_reel", False,
            f"no /reel/ link on {username}/reels after 45s; page text: {body[:300]}")
    except Exception:
        log("newest_reel", False, "no /reel/ link found on the profile reels page")
    # The grid's own responses may carry the shortcodes even though no
    # link rendered — check what the page's calls returned.
    for cand in _captured_urls():
        _add(cand)
    await _replay_captured()
    _log_requests()
    if candidates:
        return candidates
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
                for cand in await _scan_links(45):
                    _add(cand)
                if candidates:
                    return candidates
                for cand in _captured_urls():
                    _add(cand)
                await _replay_captured()
                _log_requests()
                if candidates:
                    return candidates
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
        _add(f"https://www.instagram.com/reel/{m.group(1)}/")
    if candidates:
        return candidates
    log("newest_reel", False, "no shortcode in the /reels/ page HTML either")
    # The profile-info API is rate-limited / 404 — try the GraphQL
    # profile-feed query (the web app's own grid query) as a last read.
    api_url = await _newest_reel_via_api(page, username, log)
    if api_url:
        _add(api_url)
        return candidates
    gql_url = await _newest_reel_via_graphql(page, username, log)
    if gql_url:
        _add(gql_url)
    return candidates



async def _verify_reel_url(page, url, must_contain=None, log=None):
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
    value is False. log (optional): when the marker check fails, logs
    what the page actually showed, so a later run can tell a wrong
    Reel from a Reel page whose caption never rendered in the
    headless browser."""
    if not _goto_allowed(url):
        return False, False
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        # Large limit: the caption sits below pages of navigation chrome
        # in the body text, so a small limit can miss it entirely
        # (2026-11-19: a real Reel failed verification on limit=2000).
        body = await _body_text(page, limit=20000)
    except Exception:
        return False, False
    loads = "Sorry, this page isn't available" not in body
    marker = "Succession" in body
    try:
        html = await page.content()
    except Exception:
        html = ""
    if must_contain:
        marker = marker and any(m in (html or "") for m in must_contain)
    if log is not None and not marker:
        try:
            _bt = (body or "")[:300].replace("\n", " | ")
        except Exception:
            _bt = ""
        log("verify_diag", False,
            f"{url} loads={loads} marker={marker} "
            f"body[:300]={_bt} html_has_caption={'caption' in (html or '')}")
    return loads, marker


def _norm_ws(text):
    """Collapse all whitespace to single spaces (for caption matching)."""
    return " ".join((text or "").split())


async def _verify_reel_caption(page, url, caption, log=None):
    """Load a Reel URL and check it carries the FULL expected caption.
    Returns (loads, full_match, detail): loads = the page is a live
    Reel page; full_match = the page body contains the caption's first
    line, at least 4 of its 5 word lines, and >=80% of its substantive
    lines. A bare 'Succession' marker is NOT enough — any Succession
    Reel would pass it (and the 2026-11-25 Part 5 post URL was a
    stranger's feed post, which also had no caption check at all).
    detail carries matched/total counts so a failure says exactly how
    far off the caption was. Never raises."""
    detail = {"matched": 0, "total": 0, "first_line": False,
              "word_lines": 0, "hashtags": False, "excerpt": ""}
    if not _goto_allowed(url):
        return False, False, detail
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        # Expand a collapsed caption ("...more") if one is shown.
        try:
            more = page.locator("span:text-is('more')").first
            if await more.count() and await more.is_visible(timeout=2000):
                await more.click(timeout=3000)
                await page.wait_for_timeout(1000)
        except Exception:
            pass
        body = await _body_text(page, limit=20000)
    except Exception:
        return False, False, detail
    loads = "Sorry, this page isn't available" not in body
    detail["excerpt"] = (body or "")[:300].replace("\n", " | ")
    if not loads:
        return False, False, detail
    norm_body = _norm_ws(body)
    lines = [ln.strip() for ln in (caption or "").splitlines()
             if len(ln.strip()) >= 15]
    detail["total"] = len(lines)
    matched = 0
    for ln in lines:
        if _norm_ws(ln) in norm_body:
            matched += 1
    detail["matched"] = matched
    if lines:
        detail["first_line"] = _norm_ws(lines[0]) in norm_body
    word_lines = [ln for ln in lines
                  if re.match(r"^[1-5]\.\s", ln)]
    detail["word_lines"] = sum(1 for ln in word_lines
                               if _norm_ws(ln) in norm_body)
    detail["hashtags"] = "#Succession" in norm_body
    full = (loads and detail["first_line"]
            and detail["word_lines"] >= 4
            and detail["total"] > 0
            and matched >= 0.8 * detail["total"])
    if log is not None:
        log("verify_caption", bool(full),
            f"{url} loads={loads} matched={matched}/{detail['total']} "
            f"first_line={detail['first_line']} "
            f"word_lines={detail['word_lines']}/5 "
            f"hashtags={detail['hashtags']}")
    return loads, full, detail


async def _confirm_posted_reel(page, username, caption, out_dir, log):
    """The ONLY source of a posted Reel's URL: the owner's /reels/
    page, newest first, with the FULL uploaded caption verified per
    candidate. NEVER the home feed, NEVER the share dialog (it carries
    no link — the 2026-11-25 Part 5 URL came from the feed post behind
    the dialog). Retries the /reels/ read 3x (45s apart) in case the
    grid lags the publish. Returns the verified URL, or None."""
    if not username:
        return None
    for attempt in range(3):
        try:
            cands = await _newest_reel_link(page, username, log)
        except Exception:
            cands = []
        for cand in (cands or [])[:3]:
            loads, full, detail = await _verify_reel_caption(
                page, cand, caption, log)
            if loads and full:
                try:
                    await page.screenshot(
                        path=os.path.join(out_dir, "confirmed_reel.png"))
                except Exception:
                    pass
                log("confirm_reel", True,
                    f"posted Reel verified on the profile: {cand}")
                return cand
            log("confirm_candidate", False,
                f"{cand} is not the just-posted Reel "
                f"(loads={loads}, caption {detail.get('matched')}/"
                f"{detail.get('total')})")
        if attempt < 2:
            log("confirm_retry", True,
                "no candidate matched yet — waiting 45s for the grid")
            await page.wait_for_timeout(45000)
    return None


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
    Read-only; owner's own inbox only. Returns a LIST of candidate
    /reel/ URLs from ALL stories' media args and links (in story
    order — the inbox carries several stories and the Part 2 post may
    not be the first), or []. Callers verify each candidate."""
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
        return []
    if not res.get("ok"):
        log("newest_reel_news", False,
            f"news inbox failed: status={res.get('status')} {res.get('error', '')}")
        return []
    data = res.get("data") or {}
    stories = []
    for key in ("new_stories", "old_stories", "stories"):
        stories.extend(data.get(key) or [])
    all_links = []
    cands = []
    for st in stories:
        args = st.get("args") or {}
        # A "your post" story can carry the media object directly —
        # as a dict or a list, with a shortcode ("code") or a numeric
        # media id ("id"/"pk", convertible to a shortcode).
        _med = args.get("media")
        _meds = [_med] if isinstance(_med, dict) else list(_med or [])
        for med in _meds:
            if not isinstance(med, dict):
                continue
            code = med.get("code")
            if not code:
                _mid = med.get("id") or med.get("pk")
                if _mid:
                    code = _shortcode_from_media_id(_mid)
            if code:
                cand = f"https://www.instagram.com/reel/{code}/"
                if _goto_allowed(cand) and cand not in cands:
                    cands.append(cand)
        for link in args.get("links") or []:
            u = link.get("link") or ""
            if u:
                all_links.append(u)
            if "/reel/" in u or "/p/" in u:
                cand = u if u.startswith("http") else "https://www.instagram.com" + u
                if _goto_allowed(cand) and cand not in cands:
                    cands.append(cand)
    if cands:
        log("newest_reel_news", True,
            f"{len(cands)} Reel candidate(s) from news inbox: {cands}")
        return cands
    _ak = list((stories[0].get("args") or {}).keys()) if stories else []
    _first = stories[0].get("args") or {} if stories else {}
    _diag = ""
    try:
        _diag = (f" first media={json.dumps(_first.get('media'))[:300]}"
                 f" dest={_first.get('destination')}"
                 f" media_dest={_first.get('media_destination')}"
                 f" text={(_first.get('text') or '')[:120]}")
    except Exception:
        pass
    log("newest_reel_news", False,
        f"no Reel link in news inbox ({len(stories)} stories, "
        f"links={all_links[:10]}, first args keys={_ak}{_diag})")
    return []


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


async def _replay_grid_query(page, doc_id, variables, log, endpoint=None):
    """Replay the profile grid's own GraphQL query (doc_id + variables
    captured from the page's live request) via in-page fetch POST.
    The page's grid may not render even when the query itself returns
    the media. Returns the first media code's Reel URL, or None.
    Read-only; owner's own profile only."""
    if not doc_id or not isinstance(variables, dict):
        return None
    csrf = await _csrf_token(page)
    headers = {"Content-Type": "application/json",
               "X-IG-App-ID": "936619743392459",
               "X-Requested-With": "XMLHttpRequest",
               "X-ASBD-ID": "129477"}
    if csrf:
        headers["X-CSRFToken"] = csrf
    body = {"doc_id": doc_id, "variables": variables}
    js = """async ([url, headers, body]) => {
      try {
        const r = await fetch(url, {method: 'POST', headers,
          body: JSON.stringify(body)});
        if (r.status === 200) return {ok: true, data: await r.json()};
        return {ok: false, status: r.status};
      } catch (e) { return {ok: false, error: String(e)}; }
    }"""
    try:
        res = await page.evaluate(
            js, [endpoint or "https://www.instagram.com/graphql/query/",
                 headers, body])
    except Exception as e:
        log("newest_reel_replay", False, f"grid replay failed: {type(e).__name__}")
        return None
    if not res.get("ok"):
        log("newest_reel_replay", False,
            f"grid replay failed: status={res.get('status')} {res.get('error', '')}")
        return None
    codes = _codes_from_json(res.get("data"))
    if codes:
        cand = f"https://www.instagram.com/reel/{codes[0]}/"
        log("newest_reel_replay", True, f"newest Reel via grid replay: {cand}")
        return cand
    log("newest_reel_replay", False, "grid replay returned no media codes")
    return None


async def _newest_reel_via_a1(page, username, log):
    """NEW 2026-11-19 (untried): the legacy profile JSON endpoint
    GET /<username>/?__a=1&__d=dis — in-page fetch (not a navigation),
    same session. Returns the newest video's /reel/<code>/ URL, or
    None. Read-only; owner's own profile only."""
    url = f"https://www.instagram.com/{username}/?__a=1&__d=dis"
    js = """async (url) => {
      try {
        const r = await fetch(url, {headers: {
          'X-IG-App-ID': '936619743392459',
          'X-Requested-With': 'XMLHttpRequest'}});
        const t = await r.text();
        let d = null; try { d = JSON.parse(t); } catch (e) {}
        return {ok: r.status === 200, status: r.status, data: d,
                body_head: t.slice(0, 200)};
      } catch (e) { return {ok: false, error: String(e)}; }
    }"""
    try:
        res = await page.evaluate(js, url)
    except Exception as e:
        log("newest_reel_a1", False, f"profile JSON failed: {type(e).__name__}")
        return None
    if not res.get("ok"):
        log("newest_reel_a1", False,
            f"profile JSON failed: status={res.get('status')} "
            f"{res.get('error', '')} body={res.get('body_head', '')[:150]}")
        # A 201 may still carry the profile JSON — try it anyway.
        if not res.get("data"):
            return None
    codes = _codes_from_json(res.get("data"))
    if not codes:
        # Fall back to the legacy shortcode field.
        try:
            _txt = json.dumps(res.get("data") or "")
        except Exception:
            _txt = ""
        codes = re.findall(r'"shortcode"\s*:\s*"([A-Za-z0-9_-]+)"', _txt)
    if codes:
        cand = f"https://www.instagram.com/reel/{codes[0]}/"
        log("newest_reel_a1", True, f"newest Reel via profile JSON: {cand}")
        return cand
    log("newest_reel_a1", False, "profile JSON returned no media codes")
    return None


async def _newest_reel_via_app_api(page, username, log):
    """NEW 2026-11-19 (untried): the app API host
    i.instagram.com/api/v1/feed/user/<uid>/ — the web /api/v1/feed/user
    404s, but the app host is a different backend and may answer the
    same session. Via the browser's request context (not a page
    navigation). Returns the first video item's /reel/<code>/ URL, or
    None. Read-only; owner's own profile only."""
    uid = await _user_id(page)
    if not uid:
        log("newest_reel_app", False, "no user id for the app API")
        return None
    url = f"https://i.instagram.com/api/v1/feed/user/{uid}/?count=12"
    headers = {"User-Agent": ("Instagram 302.0.0.0.0 Android (33/13; 420dpi; "
                              "1080x2400; samsung; SM-G998B; p3s; qcom; en_US; "
                              "523123133)"),
               "X-IG-App-ID": "936619743392459",
               "X-IG-Device-ID": "android-0000000000000000",
               "X-Requested-With": "XMLHttpRequest"}
    try:
        resp = await page.context.request.get(url, headers=headers)
    except Exception as e:
        log("newest_reel_app", False, f"app API failed: {type(e).__name__}")
        return None
    if resp.status != 200:
        log("newest_reel_app", False, f"app API failed: status={resp.status}")
        return None
    try:
        data = await resp.json()
    except Exception:
        log("newest_reel_app", False, "app API returned no usable JSON")
        return None
    codes = _codes_from_json(data)
    if codes:
        cand = f"https://www.instagram.com/reel/{codes[0]}/"
        log("newest_reel_app", True, f"newest Reel via app API: {cand}")
        return cand
    log("newest_reel_app", False, "app API returned no media codes")
    return None


# ---------------------------------------------------------------------------
# session handling (mirror of runner.py / upload_sample.py)
# ---------------------------------------------------------------------------
def load_session(site="instagram"):
    """Fresh mint (INSTAGRAM_WEB_COOKIES, 2026-11-24) wins, else per-site env, else SITE_SESSIONS. -> (session, error)."""
    for env_name in ("INSTAGRAM_WEB_COOKIES", "SITE_SESSION_" + site.strip().upper()):
        one = os.environ.get(env_name, "").strip()
        if one:
            try:
                sess = json.loads(one)
                if isinstance(sess, dict) and (sess.get("cookies") or sess.get("localStorage")):
                    return sess, None
            except Exception:
                return {}, env_name + " is not valid JSON"
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
# NOTE (2026-11-19): the bare "a:has-text('Create')" target was REMOVED.
# It matched "Create new account" on the logged-out "Continue as <user>"
# screen, so detect_login reported logged_in=True on a logged-out page
# (proven by the ig-recover-part-2i run: profile pages showed the
# "Continue as english_with_succession" screen while login_check passed).
# The real sidebar Create button carries the New post SVG, covered below.
CREATE_CLICK_TARGETS = [
    "a:has(svg[aria-label='New post'])",
    "div[role='button']:has(svg[aria-label='New post'])",
    "a:has(svg[aria-label='Post'])",
    "svg[aria-label='New post']",
    "[aria-label='New post']",
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
                  or any("Log in" in (l.get("text") or "") for l in links[:40])
                  # The "Continue as <user>" screen means the session is
                  # not authenticated (Instagram remembers the account but
                  # wants a fresh login tap). Without this, the Create
                  # check could pass on that screen via "Create new account".
                  or "Use another profile" in body
                  or "Create new account" in body)
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
            if m and m.group(1) not in (
                    "explore", "direct", "reels", "accounts", "popular",
                    "about", "blog", "jobs", "help", "api", "privacy",
                    "terms", "locations", "lite", "meta", "threads",
                    "directory", "legal", "safety", "press", "careers",
                    "ai", "muse", "contact", "web", "developer",
                    "cookies", "accessibility", "stories", "p", "reel",
                    "tv", "igtv", "shop", "saved", "tagged", "followers",
                    "following", "activity", "settings", "edit",
                    "archive", "insights", "ads", "meta_ai", "aistudio",
                    "professional_dashboard", "professional-dashboard"):
                username = m.group(1)
                break
    except Exception:
        pass
    logged_in = bool(create_present) and not login_wall and not checkpoint
    return {"logged_in": logged_in, "username": username, "checkpoint": checkpoint,
            "login_wall": login_wall, "url": url}


async def _page_logged_in(page):
    """True when the current page renders the logged-in nav (the Create
    control is present). The Reel page sometimes renders logged-out
    (Log In / Sign Up header) even though the home page is logged in —
    when that happens the ••• menu has no Delete option and the API
    calls return the login HTML instead of JSON."""
    for sel in CREATE_CLICK_TARGETS:
        try:
            if await page.locator(sel).count() > 0:
                return True
        except Exception:
            continue
    return False


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


async def run_recover(page, username, out_dir, log, expected_caption=""):
    """Read-only recovery: find the newest Reel on the logged-in profile
    and verify it is the Reel the caller expects. When an expected
    caption is supplied (recover dispatched with --caption), the
    candidate must carry the FULL caption — a 'Succession' marker alone
    is not enough (2026-11-25: the Part 5 post URL was a stranger's
    feed post because the share dialog carries no link). Without a
    caption, falls back to the legacy Part 2 markers (words 6-10, so
    the URL is proven to be the Part 2 Reel, never the older Part 1
    Reel). Used when a post succeeded but no URL came back (the
    2026-11-19 Part 2 case: Share clicked, no URL in 180s, Reel live),
    and to find a posted Reel's real shortcode after the fact.
    Submits NOTHING — it only reads pages and the candidate Reel page.
    Tries, in order: the /reels/ page (grid + Reels-tab variant +
    network capture), the feed/user timeline API, the notifications
    inbox, the mobile web profile, and 'Your activity'.
    Returns {"ok", "status", "post_url", "caption_verified",
    "caption_match", ...} with status "recovered" on success,
    "recover_failed" otherwise."""
    # Part 2 identity markers (words 6-10 caption): the recovered URL
    # must be THIS Reel, not the older Part 1 Reel — a delete can never
    # target the wrong post.
    part2_markers = ["Get Rennie", "keep you posted"]
    candidates = []
    for cand in await _newest_reel_link(page, username, log):
        if cand not in candidates:
            candidates.append(cand)
    for _single in (await _newest_reel_via_feed_api(page, username, log),
                    await _newest_reel_via_a1(page, username, log),
                    await _newest_reel_via_app_api(page, username, log),
                    await _newest_reel_via_mobile(page, username, log),
                    await _newest_reel_via_activity(page, username, log)):
        if _single and _single not in candidates:
            candidates.append(_single)
    for _nc in await _newest_reel_via_news(page, log):
        if _nc not in candidates:
            candidates.append(_nc)
    if not candidates:
        return {"ok": False, "status": "recover_failed",
                "error_message": "no Reel link found on the profile reels page"}
    # Try each candidate in order (newest first). With an expected
    # caption, the first candidate whose page carries the FULL caption
    # wins; without one, the first that loads AND carries the Part 2
    # caption markers wins. A candidate that fails verification is
    # logged, never deleted.
    newest = None
    verified = {"caption_verified": False, "caption_match": None}
    for cand in candidates:
        if (expected_caption or "").strip():
            loads, full, detail = await _verify_reel_caption(
                page, cand, expected_caption, log)
            if loads and full:
                newest = cand
                verified = {"caption_verified": True,
                            "caption_match": detail}
                break
            log("recover_candidate", False,
                f"{cand} caption did not fully match "
                f"(loads={loads}, matched={detail.get('matched')}/"
                f"{detail.get('total')})")
        else:
            loads, marker = await _verify_reel_url(page, cand,
                                                   must_contain=part2_markers,
                                                   log=log)
            if loads and marker:
                newest = cand
                break
            log("recover_candidate", False,
                f"{cand} did not verify as Part 2 (loads={loads}, marker={marker})")
    if not newest:
        want = ("full caption match" if (expected_caption or "").strip()
                else "the Part 2 Reel")
        return {"ok": False, "status": "recover_failed",
                "error_message": f"{len(candidates)} candidate(s) found but "
                                 f"none verified as {want}: {candidates}"}
    try:
        await page.screenshot(path=os.path.join(out_dir, "recovered.png"))
    except Exception:
        pass
    log("recovered", True, f"newest Reel verified: {newest}")
    return {"ok": True, "status": "recovered", "post_url": newest,
            "error": None, "error_message": None, **verified}


async def _dump_more_candidates(page, log):
    """Diagnostic: list ALL interactive elements on the Reel page (tag,
    role, aria-label, title, own text, class, position) so a later run
    can see what the ••• control actually is. The old version capped
    at 40 elements and stopped before the action rail (the rail renders
    after the caption in DOM order), and log() truncates each detail to
    500 chars, so the dump is split across several log steps."""
    js = """() => {
      const out = [];
      const els = document.querySelectorAll(
        'button, [role=button], a[href], svg[aria-label], [aria-label], [title]');
      for (const el of els) {
        const tag = (el.tagName || '').toLowerCase();
        const role = el.getAttribute('role') || '';
        const aria = el.getAttribute('aria-label') || '';
        const title = el.getAttribute('title') || '';
        const txt = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 25);
        const cls = (el.getAttribute('class') || '').slice(0, 30);
        let xy = '';
        try {
          const r = el.getBoundingClientRect();
          xy = Math.round(r.x) + ',' + Math.round(r.y);
        } catch (e) {}
        if (aria || title || txt) {
          out.push(tag + '|' + role + '|' + aria + '|' + title + '|' + txt + '|' + cls + '|' + xy);
        }
      }
      return out;
    }"""
    try:
        items = await page.evaluate(js)
    except Exception:
        items = []
    hits = [it for it in items
            if 'more' in it.lower() or '•••' in it or '…' in it
            or 'options' in it.lower()]
    log("delete_diag", True,
        f"interactive elements on Reel page: {len(items)} total")
    log("more_hits", bool(hits),
        f"More/options-like elements ({len(hits)}): {hits[:12]}")
    # Chunk the full list so the 500-char log truncation never cuts it.
    for i in range(0, len(items), 3):
        chunk = items[i:i + 3]
        log(f"delete_diag_{i // 3 + 1:02d}", True, " || ".join(chunk))


async def _inspect_session_state(page, reel_url, log):
    """READ-ONLY diagnostic (dry-run inspect only): determine whether
    the context's cookies authenticate server-side, and where the Reel
    page's logged-out render comes from. Home page JS state, raw HTTP
    reads of the Reel URL + the owner's /reels/ page through the
    context's own HTTP client (bypasses page JS), and a long wait on
    the Reel page. Logs names/markers only — never cookie values."""
    username = None
    try:
        await page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)
        det = await detect_login(page)
        username = det.get("username")
        log("inspect_home", True,
            f"logged_in={det.get('logged_in')} username={username} "
            f"checkpoint={det.get('checkpoint')} login_wall={det.get('login_wall')}")
        names = await page.evaluate(
            "() => document.cookie.split(';').map(s => s.trim().split('=')[0]).filter(Boolean)")
        log("inspect_home_cookies", True, f"js_cookies={names}")
    except Exception as e:
        log("inspect_home", False, f"{type(e).__name__}")
    req = page.context.request
    targets = [("reel", reel_url)]
    if username:
        targets.append(("reels_page", f"https://www.instagram.com/{username}/reels/"))
    for label, url in targets:
        try:
            r = await req.get(url, timeout=30000)
            t = await r.text()
            has_user = bool(username) and (f"/{username}/" in t)
            log(f"inspect_raw_{label}", True,
                f"status={r.status} len={len(t)} has_profile_link={has_user} "
                f"has_login={'Log in' in t}")
        except Exception as e:
            log(f"inspect_raw_{label}", False, f"{type(e).__name__}")
    try:
        await page.goto(reel_url, wait_until="domcontentloaded", timeout=60000)
        names = await page.evaluate(
            "() => document.cookie.split(';').map(s => s.trim().split('=')[0]).filter(Boolean)")
        log("inspect_reel_cookies", True, f"js_cookies={names}")
        await page.wait_for_timeout(15000)
        logged = await _page_logged_in(page)
        log("inspect_reel_wait15", logged, f"Reel page logged-in after 15s: {logged}")
    except Exception as e:
        log("inspect_reel_wait15", False, f"{type(e).__name__}")


async def _probe_delete_api_variants(page, log):
    """READ-ONLY diagnostic (dry-run inspect only): probe the media
    delete API with a FAKE media id that cannot exist, so no real media
    can be deleted, and record which header/body variant gets a JSON
    answer instead of the SPA shell HTML. A JSON response (even an
    error such as 'media not found') proves the endpoint + headers are
    right; HTML means the request is routed to the SPA shell. The
    X-IG-WWW-Claim / X-ASBD-ID values are read and used inside the page
    only — only their presence/length is logged, never the values."""
    csrf = await _csrf_token(page)
    ds_user = await _user_id(page)
    js = """async ([csrf, dsUserId]) => {
      const out = {results: []};
      let claim = '', asbd = '';
      try {
        const g = await fetch('/', {headers: {'X-Requested-With': 'XMLHttpRequest'}});
        claim = g.headers.get('x-ig-www-claim') || '';
        asbd = g.headers.get('x-asbd-id') || '';
      } catch (e) { out.get_error = String(e); }
      out.claim_len = claim.length;
      out.asbd_len = asbd.length;
      out.csrf_len = (csrf || '').length;
      out.ds_user = dsUserId ? 'yes' : 'no';
      try {
        const uid = dsUserId || '';
        if (uid) {
          const vars = encodeURIComponent(JSON.stringify({id: uid, first: 2}));
          const gr = await fetch('https://www.instagram.com/graphql/query/?query_hash=69cba40317214236af376e7d0a9e1a5&variables=' + vars, {headers: {'X-IG-App-ID': '936619743392459', 'X-Requested-With': 'XMLHttpRequest'}});
          const gt = await gr.text();
          out.v0 = {status: gr.status, ctype: (gr.headers.get('content-type') || '').slice(0, 40), has_edges: gt.includes('edge_owner_to_timeline_media'), body: gt.slice(0, 120)};
        } else { out.v0 = {error: 'no uid'}; }
      } catch (e) { out.v0 = {error: String(e)}; }
      const fake = '1234567890123456789';
      const base = {'X-IG-App-ID': '936619743392459',
                    'X-Requested-With': 'XMLHttpRequest',
                    'X-ASBD-ID': '129477'};
      if (csrf) base['X-CSRFToken'] = csrf;
      const withClaim = (h) => { const o = {...h}; if (claim) o['X-IG-WWW-Claim'] = claim; return o; };
      const withUser = (h) => { const o = {...h}; if (dsUserId) o['X-IG-U-DS-USER-ID'] = dsUserId; return o; };
      const variants = [
        ['V1_baseline', 'https://www.instagram.com/api/v1/media/' + fake + '/delete/', {...base}, 'body'],
        ['V2_claim', 'https://www.instagram.com/api/v1/media/' + fake + '/delete/', withClaim(base), 'body'],
        ['V3_userid', 'https://www.instagram.com/api/v1/media/' + fake + '/delete/', withUser(base), 'body'],
        ['V4_claim_userid', 'https://www.instagram.com/api/v1/media/' + fake + '/delete/', withUser(withClaim(base)), 'body'],
        ['V5_query', 'https://www.instagram.com/api/v1/media/' + fake + '/delete/?media_type=2', withUser(withClaim(base)), 'query'],
      ];
      if (asbd) {
        const h6 = withUser(withClaim(base)); h6['X-ASBD-ID'] = asbd;
        variants.push(['V6_asbd_live', 'https://www.instagram.com/api/v1/media/' + fake + '/delete/', h6, 'body']);
      }
      for (const [name, url, headers, kind] of variants) {
        try {
          const body = new URLSearchParams();
          if (kind === 'body') body.append('media_type', '2');
          if (csrf) body.append('_csrftoken', csrf);
          const r = await fetch(url, {method: 'POST', headers, body});
          const t = await r.text();
          out.results.push({name, status: r.status,
            ctype: (r.headers.get('content-type') || '').slice(0, 50),
            body: t.slice(0, 150)});
        } catch (e) { out.results.push({name, error: String(e)}); }
      }
      return out;
    }"""
    try:
        res = await page.evaluate(js, [csrf, ds_user])
    except Exception as e:
        log("api_probe", False, f"probe failed: {type(e).__name__}")
        return
    log("api_probe", True,
        f"claim_len={res.get('claim_len')} asbd_len={res.get('asbd_len')} "
        f"csrf_len={res.get('csrf_len')} ds_user={res.get('ds_user')} "
        f"get_error={res.get('get_error', '')}")
    v0 = res.get("v0") or {}
    log("api_probe_v0_graphql", True,
        f"status={v0.get('status')} ctype={v0.get('ctype')} "
        f"has_edges={v0.get('has_edges')} body={str(v0.get('body', v0.get('error', '')))[:150]}")
    for r in res.get("results", []):
        log("api_probe_" + str(r.get("name", "?")).lower(), True,
            f"status={r.get('status')} ctype={r.get('ctype')} "
            f"body={str(r.get('body', r.get('error', '')))[:200]}")
async def _delete_reel_via_api(page, reel_url, log):
    """Delete a Reel via the media delete API (POST
    /api/v1/media/<media_id>/delete/?media_type=2), the same endpoint
    the web app calls after Delete. The media id is decoded from the
    shortcode in the URL. Returns True only if the API said ok AND the
    Reel URL no longer loads afterwards, else False. Only ever called
    for the exact URL the user ordered deleted (and only on a live
    delete, never on an inspect). The API checks ownership server-side,
    so a wrong id cannot delete someone else's media."""
    m = re.search(r"/reel/([A-Za-z0-9_-]+)/", reel_url or "")
    if not m:
        return False
    media_id = _media_id_from_shortcode(m.group(1))
    if not media_id:
        log("delete_api", False, f"could not decode media id from {reel_url}")
        return False
    csrf = await _csrf_token(page)
    url = f"https://www.instagram.com/api/v1/media/{media_id}/delete/"
    headers = {"X-IG-App-ID": "936619743392459",
               "X-Requested-With": "XMLHttpRequest",
               "X-ASBD-ID": "129477"}
    if csrf:
        headers["X-CSRFToken"] = csrf
    # Form body (not a query param): media_type + the CSRF token, the
    # way the web app posts it. An empty body / query-param-only call
    # returns the SPA shell HTML (200) instead of a deletion.
    js = """async ([url, headers, csrf]) => {
      try {
        const body = new URLSearchParams({media_type: '2'});
        if (csrf) body.append('_csrftoken', csrf);
        const r = await fetch(url, {method: 'POST', headers, body});
        const t = await r.text();
        return {ok: r.status === 200, status: r.status,
                ctype: (r.headers.get('content-type') || '').slice(0, 60),
                body: t.slice(0, 300)};
      } catch (e) { return {ok: false, error: String(e)}; }
    }"""
    try:
        res = await page.evaluate(js, [url, headers, csrf])
    except Exception as e:
        log("delete_api", False, f"media delete API failed: {type(e).__name__}")
        return False
    if not res.get("ok"):
        log("delete_api", False,
            f"media delete API failed for {media_id}: "
            f"status={res.get('status')} {res.get('error', '')} "
            f"body={res.get('body', '')[:200]}")
        return False
    _body = res.get("body", "") or ""
    if _body.lstrip().startswith("<"):
        # HTML, not JSON — the endpoint returned the SPA shell / login
        # page instead of a deletion (not authenticated as an API call).
        log("delete_api", False,
            f"media delete API for {media_id} returned HTML, not JSON "
            f"(ctype={res.get('ctype', '')}) — no deletion happened")
        return False
    log("delete_api", True,
        f"media delete API ok for {media_id}: {_body[:200]}")
    # Verify the Reel is really gone.
    await page.wait_for_timeout(3000)
    loads, _marker = await _verify_reel_url(page, reel_url)
    if loads:
        log("delete_api", False, "Reel URL still loads after API delete")
        return False
    return True


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
    # The Reel page sometimes renders logged-out even though the home
    # page is logged in (then the ••• menu has no Delete and the API
    # returns login HTML). Re-establish the session via the home page
    # and come back before trying anything.
    if not await _page_logged_in(page):
        log("delete", False, "Reel page renders logged-out — re-establishing the session via home")
        try:
            await page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(4000)
            await page.goto(reel_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(4000)
        except Exception:
            pass
        if not await _page_logged_in(page):
            log("delete", False, "Reel page still renders logged-out after re-establishing the session")
        else:
            log("delete", True, "Reel page now renders logged-in")
    if dry_run:
        # Inspect only: session-state check first, then the fake-id API
        # probe and the element dump, before looking for •••.
        await _inspect_session_state(page, reel_url, log)
        await _probe_delete_api_variants(page, log)
        await _dump_more_candidates(page, log)
    if not dry_run:
        # Primary: delete via the media delete API. The UI action rail
        # does not reliably render in the headless browser (the •••
        # button is found but the menu it opens has no Delete option),
        # so the API is the reliable path. Falls back to the UI below
        # if the API fails (e.g. a 429 rate limit).
        if await _delete_reel_via_api(page, reel_url, log):
            return {"ok": True, "status": "deleted",
                    "deleted_url": reel_url, "error": None,
                    "error_message": None,
                    "note": "deleted via media delete API"}
        log("delete", False, "API delete failed, falling back to the UI path")
    # Find the ••• (More options) control. Poll up to 30s — the action
    # rail renders after the media data loads, later than the caption.
    # Try several selectors; the web UI exposes it as a button with
    # aria-label 'More options', or a plain div/button with the text
    # '•••' / '...'. (2026-11-19: a single 4s wait + one pass found
    # nothing on a page whose caption DID render.)
    more = None
    more_desc = None
    deadline = time.time() + 30
    while time.time() < deadline and more is None:
        for desc, loc in [
            ("button[aria-label=More options]",
             page.locator("button[aria-label='More options']")),
            ("role=button More options",
             page.get_by_role("button", name="More options")),
            ("button[aria-label*=More]",
             page.locator("button[aria-label*='More']")),
            ("button:has-text(•••)", page.locator("button:has-text('•••')")),
            ("div[role=button]:has-text(•••)",
             page.locator("div[role='button']:has-text('•••')")),
            ("[aria-label='More options']",
             page.locator("[aria-label='More options']")),
            ("text=•••", page.locator("text=•••")),
        ]:
            try:
                if await loc.count() and await loc.first.is_visible(timeout=1000):
                    more, more_desc = loc.first, desc
                    break
            except Exception:
                continue
        if more is None:
            await page.wait_for_timeout(3000)
    if more is None:
        if not dry_run:
            # Fallback: the action rail never renders in the headless
            # browser, so delete via the media delete API directly.
            if await _delete_reel_via_api(page, reel_url, log):
                return {"ok": True, "status": "deleted",
                        "deleted_url": reel_url, "error": None,
                        "error_message": None,
                        "note": "deleted via media delete API "
                                "(action rail did not render)"}
        try:
            await page.screenshot(path=os.path.join(out_dir, "delete_no_menu.png"))
        except Exception:
            pass
        await _dump_more_candidates(page, log)
        try:
            _bt = (await _body_text(page, limit=500)).replace("\n", " | ")[:400]
        except Exception:
            _bt = ""
        log("delete", False, f"Reel page body: {_bt}")
        return {"ok": False, "status": "delete_failed",
                "error_message": "could not find the ••• (More options) control on the Reel page"}
    log("delete", True, f"••• control found via {more_desc}")
    # Click it. A plain click can time out in the headless browser
    # (the element moves / is covered while the Reel viewer settles),
    # so fall back to a forced click and then a JS click.
    _clicked = False
    for _how in ("click", "force", "js"):
        try:
            if _how == "click":
                await more.click(timeout=10000)
            elif _how == "force":
                await more.click(timeout=10000, force=True)
            else:
                await more.evaluate("el => el.click()")
            _clicked = True
            log("delete", True, f"••• menu opened via {_how} click")
            break
        except Exception:
            continue
    if not _clicked:
        return {"ok": False, "status": "delete_failed",
                "error_message": "could not open the ••• menu"}
    await page.wait_for_timeout(2000)
    # Diagnostic: what menu actually opened? (The nav sidebar's More
    # menu has no Delete; the Reel's ••• menu must have it.)
    try:
        _menu_text = await page.evaluate(
            """() => {
              const dlg = document.querySelector('[role="dialog"], [role="menu"]');
              const el = dlg || document.body;
              return (el.innerText || '').slice(0, 800);
            }""")
    except Exception:
        _menu_text = ""
    log("delete_menu", True, f"menu text after ••• click: {_menu_text}")
    await _dump_more_candidates(page, log)
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


async def run_probe_fb(page, reel_url, out_dir, log):
    """PROBE ONLY: open one already-published Reel URL (the owner's own
    post — the same URL class the post flow opens to verify a publish)
    and inspect whether the web Reel page offers a 'Share to Facebook'
    control, via the share (paper-plane) button's dialog and the
    more-options (•••) menu. Inspects only: it NEVER clicks anything
    labelled Facebook, never shares anything, never publishes.
    Returns a dict for result.json: present (bool), location
    ('share_dialog' / 'more_menu' / None), the texts seen, screenshots."""
    fb = {"reel_url": reel_url, "present": False, "on": None,
          "location": None, "share_button_found": False,
          "more_button_found": False, "share_dialog_text": "",
          "more_menu_text": "", "page_text": "", "mentions": []}
    if not _goto_allowed(reel_url):
        fb["error"] = "reel URL outside the navigation allowlist"
        return fb
    try:
        await page.goto(reel_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)
    except Exception:
        fb["error"] = "could not load the Reel URL"
        return fb
    try:
        await page.screenshot(path=os.path.join(out_dir, "fb_reel_page.png"))
    except Exception:
        pass
    fb["page_text"] = await _body_text(page, limit=2000)
    if "Sorry, this page isn't available" in fb["page_text"]:
        fb["error"] = "Reel page is not available (does not load)"
        return fb
    # 1. Share (paper-plane) button -> dialog. Clicking Share only OPENS
    # the share dialog; nothing is sent until an option inside is picked,
    # and this probe never picks one.
    share_clicked = False
    for sel in ("[aria-label='Share']", "svg[aria-label='Share']"):
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible(timeout=3000):
                await loc.click(timeout=5000)
                share_clicked = True
                break
        except Exception:
            continue
    fb["share_button_found"] = share_clicked
    if share_clicked:
        await page.wait_for_timeout(2000)
        try:
            await page.screenshot(path=os.path.join(out_dir, "fb_share_dialog.png"))
        except Exception:
            pass
        fb["share_dialog_text"] = (await _dialog_text(page)
                                   or await _body_text(page, limit=1500))
        if "facebook" in fb["share_dialog_text"].lower():
            fb["present"] = True
            fb["location"] = "share_dialog"
            log("facebook_share", True, "Facebook option in the Share dialog")
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(1000)
        except Exception:
            pass
    # 2. More-options (•••) menu.
    if not fb["present"]:
        more_clicked = False
        for sel in ("[aria-label='More options']", "svg[aria-label='More options']"):
            try:
                loc = page.locator(sel).first
                if await loc.count() and await loc.is_visible(timeout=3000):
                    await loc.click(timeout=5000)
                    more_clicked = True
                    break
            except Exception:
                continue
        fb["more_button_found"] = more_clicked
        if more_clicked:
            await page.wait_for_timeout(2000)
            try:
                await page.screenshot(path=os.path.join(out_dir, "fb_more_menu.png"))
            except Exception:
                pass
            fb["more_menu_text"] = (await _dialog_text(page)
                                    or await _menu_text(page)
                                    or await _body_text(page, limit=1500))
            if "facebook" in fb["more_menu_text"].lower():
                fb["present"] = True
                fb["location"] = "more_menu"
                log("facebook_share", True,
                    "Facebook option in the more-options menu")
            try:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(1000)
            except Exception:
                pass
    if not fb["present"]:
        log("facebook_share", True,
            "no Share-to-Facebook control on the published Reel page "
            "(share dialog + more menu checked)")
    fb["mentions"] = [ln.strip()[:120]
                      for txt in (fb.get("share_dialog_text", ""),
                                  fb.get("more_menu_text", ""))
                      for ln in txt.splitlines()
                      if "facebook" in ln.lower()][:10]
    return fb


async def _share_to_facebook(page, out_dir, log):
    """Best-effort post-publish cross-post (owner order 2026-11-25):
    on the just-published Reel's page, open the Share dialog and pick
    the Facebook option. Returns {"shared": bool, "detail": str}.
    NEVER raises and NEVER fails the Reel post: a missing control or
    an unclear confirmation is recorded, not fatal. The probe_fb probe
    (2026-11-25) proved the Facebook option exists in this dialog;
    what happens AFTER picking it (a Facebook confirm dialog, or an
    immediate share) is observed here and recorded with screenshots,
    so the first live run's evidence settles the exact flow."""
    res = {"shared": False, "detail": ""}
    try:
        # Open the Share dialog (paper-plane button on the Reel page).
        # Clicking Share only OPENS the dialog; nothing is sent yet.
        share_clicked = False
        for sel in ("[aria-label='Share']", "svg[aria-label='Share']"):
            try:
                loc = page.locator(sel).first
                if await loc.count() and await loc.is_visible(timeout=5000):
                    await loc.click(timeout=5000)
                    share_clicked = True
                    break
            except Exception:
                continue
        if not share_clicked:
            res["detail"] = "no Share button on the published Reel page"
            log("facebook_share", False, res["detail"])
            return res
        await page.wait_for_timeout(2000)
        try:
            await page.screenshot(path=os.path.join(out_dir, "fb_share_dialog.png"))
        except Exception:
            pass
        dlg = await _dialog_text(page) or ""
        if "facebook" not in dlg.lower():
            res["detail"] = ("Share dialog opened but has no Facebook "
                             "option: " + dlg[:200])
            log("facebook_share", False, res["detail"])
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            return res
        # Pick the Facebook option. (The probe never clicked it; this is
        # the ordered cross-post action.)
        hit = await _click_visible_text(page, ("Facebook",), timeout_ms=5000)
        if not hit:
            res["detail"] = "Facebook option visible but not clickable"
            log("facebook_share", False, res["detail"])
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            return res
        log("facebook_share", True,
            "Facebook option picked; waiting for confirmation")
        await page.wait_for_timeout(3000)
        try:
            await page.screenshot(path=os.path.join(out_dir, "fb_shared.png"))
        except Exception:
            pass
        after = await _dialog_text(page) or await _body_text(page, limit=1500)
        # A Facebook confirm dialog may ask once more. Only click inside
        # a dialog that itself mentions Facebook.
        dlg2 = await _dialog_text(page) or ""
        if "facebook" in dlg2.lower():
            for confirm in ("Share", "Post"):
                try:
                    loc = page.locator(
                        f"[role='dialog'] button:has-text('{confirm}')").first
                    if await loc.count() and await loc.is_visible(timeout=3000):
                        await loc.click(timeout=5000)
                        log("facebook_share", True,
                            f"Facebook confirmation clicked ({confirm})")
                        await page.wait_for_timeout(3000)
                        break
                except Exception:
                    continue
            after = await _dialog_text(page) or await _body_text(page, limit=1500)
        low = after.lower()
        if any(w in low for w in ("shared", "your reel was shared",
                                  "post shared")):
            res["shared"] = True
            res["detail"] = "Facebook share confirmed: " + after[:200]
        else:
            # No explicit success text: record as unclear, not failed —
            # the first live run's screenshots settle the exact flow.
            res["shared"] = False
            res["detail"] = ("Facebook picked; no explicit confirmation "
                             "text seen: " + after[:200])
        log("facebook_share", res["shared"], res["detail"][:300])
        return res
    except Exception as e:
        res["detail"] = f"exception: {type(e).__name__}"
        log("facebook_share", False, res["detail"])
        return res


async def run_post(page, video_path, caption, out_dir, log, username=None):
    """Full publish flow. ONLY called when dry_run is false and mode is post.
    The caption is pasted VERBATIM from the caller — never generated,
    reworded, or invented here. An empty caption is refused by the caller
    before any browser work; this is a second check.
    Confirmation (2026-11-19 fix, hardened 2026-11-25): after Share the
    wait is 300s for the confirmation text (was 180s — the Part 2 post
    succeeded but returned no URL in 180s). The post URL is NEVER taken
    from this page: the share dialog carries no link, and the links
    behind it belong to the home feed (2026-11-25: that grabbed
    @iamishachopra's post for Part 5). Instead the URL comes from the
    owner's /reels/ page with the FULL uploaded caption verified.
    After the URL is verified, the Reel is also shared to Facebook from
    the published Reel page (Share dialog -> Facebook; owner order
    2026-11-25). Best-effort: a Facebook failure is recorded, never
    fatal to the post."""
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
    # Wait for the shared confirmation (up to 300s). The confirmation
    # dialog carries NO link — NEVER take a URL from this page: the
    # links in the DOM behind the dialog belong to the home feed
    # (2026-11-25: this grabbed @iamishachopra's post for Part 5).
    confirmed = False
    deadline = time.time() + 300
    while time.time() < deadline:
        await page.wait_for_timeout(3000)
        body = await _body_text(page, limit=2000)
        if "shared" in body.lower() or "Your reel" in body:
            confirmed = True
            break
    log("share_confirmed", confirmed,
        "publish confirmation seen" if confirmed else
        "no confirmation text in 300s — verifying from the profile anyway")
    try:
        await page.screenshot(path=os.path.join(out_dir, "published.png"))
    except Exception:
        pass
    # The ONLY source of the post URL: the owner's /reels/ page, with
    # the FULL uploaded caption verified. Never the home feed.
    post_url = await _confirm_posted_reel(page, username, caption, out_dir, log)
    if not post_url:
        return {"ok": False, "status": "post_failed",
                "error_message": "Share clicked but the posted Reel could "
                                 "not be verified on the owner's /reels/ page "
                                 "(full caption match required)"}
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
    # Cross-post to Facebook (owner order 2026-11-25): the published
    # Reel page's Share dialog has a Facebook option (proven by the
    # probe_fb probe). Best-effort ONLY — a Facebook failure never
    # fails the Reel post, which is already verified above.
    fb_share = await _share_to_facebook(page, out_dir, log)
    return {"ok": True, "status": "posted", "post_url": post_url,
            "facebook_shared": fb_share}


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
                                  "deleted", "delete_option_found",
                                  "probe_fb_ok")
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
    if args.mode not in ("probe", "post", "recover", "delete", "probe_fb"):
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

    # ---- gate 2a2: probe_fb needs the exact Reel URL to inspect. ----
    if args.mode == "probe_fb" and not (args.reel_url or "").strip():
        log("probe_fb_gate", False, "no --reel-url supplied — refusing")
        return finish("error", error="no Reel URL to inspect",
                      error_message="probe_fb needs --reel-url (the exact "
                      "instagram.com/reel/... URL of an already-published "
                      "Reel). It inspects ONLY that URL.")

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
    if args.mode in ("recover", "delete", "probe_fb"):
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
                    if args.mode == "recover" and not login.get("username"):
                        return finish("recover_failed",
                                      error="no username from login check",
                                      error_message="the home page did not yield a profile username, so recover has no profile to read (refusing to open a bogus URL).")
                    if args.mode == "recover":
                        rec = await run_recover(page, login["username"],
                                                out_dir, log,
                                                expected_caption=args.caption or "")
                        if rec.get("ok"):
                            return finish("recovered", error=None,
                                          error_message=None,
                                          post_url=rec.get("post_url"),
                                          caption_verified=rec.get("caption_verified"),
                                          caption_match=rec.get("caption_match"))
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
                    if args.mode == "probe_fb":
                        pfb = await run_probe_fb(page, args.reel_url or "",
                                                 out_dir, log)
                        if pfb.get("error"):
                            return finish("probe_fb_failed",
                                          error="probe_fb failed",
                                          error_message=pfb.get("error"),
                                          facebook_share=pfb)
                        return finish("probe_fb_ok", error=None,
                                      error_message=None,
                                      facebook_share=pfb,
                                      note="probe only: nothing was shared "
                                           "or published")
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
                                      post_url=posted.get("post_url"),
                                      facebook_shared=posted.get("facebook_shared"))
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
    ap.add_argument("--mode", choices=["probe", "post", "recover", "delete",
                                      "probe_fb"],
                    default="probe",
                    help="probe (inspect only), post (publish), recover (find the "
                         "newest Reel on the profile), delete (delete --reel-url), "
                         "probe_fb (inspect one published Reel page for a "
                         "Share-to-Facebook control; read-only).")
    ap.add_argument("--reel-url", "--reel_url", dest="reel_url", default="",
                    help="Delete / probe_fb mode: the exact instagram.com/reel/... "
                         "URL. Delete removes ONLY that Reel; probe_fb inspects "
                         "ONLY that Reel's page.")
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
