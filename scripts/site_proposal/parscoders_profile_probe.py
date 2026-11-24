#!/usr/bin/env python3
"""Read-only ParsCoders profile probe.

Loads the sealed SITE_SESSION_PARSCODERS session (cookies + localStorage),
opens parscoders.com logged in, and reads the profile/account page to find
the display name and the account email address.

READ-ONLY: only GET navigations, no form fills, no clicks on submit buttons,
no proposals, no messages, no settings changes. The email address is written
ONLY into result.json inside the PRIVATE hq-ai-results repo; stdout prints
booleans/counts only, never the email or any cookie value (run logs are public).

Env:
  SITE_SESSION_PARSCODERS - sealed session JSON {"cookies": [...], "localStorage": {...}}
  GWEB_PROXY              - optional proxy server (Oracle exit on the runner)
  PROBE_RESULTS_DIR       - results root (default: hq-ai-results)
  PROBE_JOB_ID            - job id, used for the output subdir (default: manual)

Output: <results>/jobs/<job_id>/result.json + screenshots page-N.png
Exit 0 when the probe ran (even if no email found); exit 1 only on fatal errors
(session missing/unparseable, browser launch failure, homepage unreachable).
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

SITE = "https://parscoders.com"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Candidate profile/account pages, in the order to try them.
CANDIDATES = [
    "/user-info/update",
    "/user-info",
    "/profile",
    "/user",
    "/account",
    "/dashboard",
    "/panel",
    "/user/profile",
    "/settings",
    "/profile/edit",
]

# Header/nav link keywords that point at the profile/account page.
PROFILE_HREF_RE = re.compile(r"profile|account|user|settings|panel|dashboard|edit", re.I)


def sanitize_cookies(cookies, default_domain=None):
    """Clean cookie dicts for Playwright context.add_cookies (same rules as
    scripts/site_proposal/runner.py sanitize_cookies)."""
    sanitized = []
    for c in cookies:
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
        sanitized.append(clean)
    return sanitized


def probe_portfolio(page, out_dir):
    """Read-only listing of the user's portfolio samples at /resume/portfolio/.

    Returns {url, logged_in_hint, samples: [{title, show_url, edit_url,
    card_text}], add_links, buttons, page_text}. GET navigation only, no
    form fills, no clicks on submit buttons. Titles/links come from the
    actual logged-in list page — nothing guessed.
    """
    url = SITE + "/resume/portfolio/"
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    try:
        page.evaluate("() => { window.scrollTo(0, document.body.scrollHeight); }")
        page.wait_for_timeout(1200)
        page.evaluate("() => { window.scrollTo(0, 0); }")
        page.wait_for_timeout(500)
    except Exception:
        pass
    try:
        page.screenshot(path=str(out_dir / "portfolio.png"))
    except Exception:
        pass
    data = page.evaluate(
        """() => {
            const out = {samples: [], add_links: [], buttons: []};
            const seen = new Set();
            for (const a of document.querySelectorAll('a[href*="/show/"]')) {
                const href = a.getAttribute('href') || '';
                if (!href.includes('portfolio')) continue;
                if (seen.has(href)) continue;
                seen.add(href);
                let card = a.closest('div');
                for (let i = 0; i < 6 && card; i++) {
                    const t = (card.innerText || '').trim();
                    if (t.length > 30) break;
                    card = card.parentElement;
                }
                let edit_url = '';
                if (card) {
                    for (const x of card.querySelectorAll('a[href]')) {
                        const h = (x.getAttribute('href') || '');
                        const tx = (x.innerText || '');
                        if (/edit|ویرایش/i.test(h + ' ' + tx)) { edit_url = h; break; }
                    }
                }
                out.samples.push({
                    title: (a.innerText || '').trim().slice(0, 150),
                    show_url: href,
                    edit_url: edit_url,
                    card_text: card ? (card.innerText || '').trim().slice(0, 600) : '',
                });
            }
            for (const a of document.querySelectorAll('a[href]')) {
                const href = (a.getAttribute('href') || '');
                const tx = (a.innerText || '').trim().slice(0, 80);
                if (href.includes('/resume/portfolio') && !href.includes('/show/')) {
                    out.add_links.push({href, text: tx});
                }
            }
            out.buttons = [...document.querySelectorAll('button')]
                .filter(e => e.offsetParent !== null)
                .map(e => (e.innerText || '').trim().slice(0, 80)).filter(Boolean).slice(0, 40);
            out.page_text = (document.body.innerText || '').slice(0, 4000);
            return out;
        }""")
    body_text = ""
    try:
        body_text = page.locator("body").inner_text(timeout=10000)
    except Exception:
        pass
    data["url"] = url
    data["final_url"] = page.url
    data["on_login_page"] = "/login" in page.url
    data["has_display_name"] = "محمد لعلی" in (body_text or "")
    return data


def probe_sample_details(page, out_dir, samples):
    """Read-only detail probe for each portfolio sample.

    For every sample from the list page: visit its show page (confirm what
    the sample actually is — title + description text) and GET the guessed
    edit URL /resume/portfolio/<id>/edit, dumping its forms with current
    field values. GET only, no fills, no submits. If the edit URL is not
    the real edit page, the dumped forms/title make that visible.
    """
    details = []
    for s in samples or []:
        show = s.get("show_url") or ""
        m = re.search(r"/show/(\d+)/", show)
        sid = m.group(1) if m else ""
        entry = {"show_url": show, "sample_id": sid}
        try:
            page.goto(urljoin(SITE + "/", show.lstrip("/")),
                      wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)
            entry["show_title"] = page.title()
            try:
                entry["show_text"] = page.locator("body").inner_text(timeout=8000)[:3000]
            except Exception:
                entry["show_text"] = ""
            try:
                entry["show_links"] = page.evaluate(
                    """() => [...document.querySelectorAll('a[href]')].map(a => ({
                        href: a.getAttribute('href') || '',
                        text: (a.innerText || '').trim().slice(0, 60)
                    })).filter(l => /edit|ویرایش|portfolio/i.test(l.href + ' ' + l.text)).slice(0, 20)""")
            except Exception:
                entry["show_links"] = []
        except Exception as e:
            entry["show_error"] = f"{type(e).__name__}: {e}"
        if sid:
            edit_url = f"{SITE}/resume/portfolio/{sid}/edit"
            entry["edit_url_tried"] = edit_url
            try:
                page.goto(edit_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2000)
                entry["edit_final_url"] = page.url
                entry["edit_title"] = page.title()
                entry["edit_forms"] = page.evaluate(
                    """() => [...document.querySelectorAll('form')].map(f => ({
                        action: f.action || '', method: f.method || '',
                        fields: [...f.querySelectorAll('input, textarea, select')].map(el => ({
                            tag: el.tagName.toLowerCase(),
                            type: el.getAttribute('type') || '',
                            name: el.getAttribute('name') || '', id: el.id || '',
                            value: (el.value || '').slice(0, 300)
                        })),
                        buttons: [...f.querySelectorAll('button, input[type=submit]')].map(b => ({
                            text: ((b.innerText || b.value || '').trim().slice(0, 60)),
                            id: b.id || '', type: b.getAttribute('type') || ''
                        }))
                    }))""")
                try:
                    entry["edit_body_text"] = page.locator("body").inner_text(timeout=8000)[:2000]
                except Exception:
                    entry["edit_body_text"] = ""
            except Exception as e:
                entry["edit_error"] = f"{type(e).__name__}: {e}"
        details.append(entry)
    return details


def main():
    job_id = os.environ.get("PROBE_JOB_ID", "manual")
    results_root = Path(os.environ.get("PROBE_RESULTS_DIR", "hq-ai-results"))
    out_dir = results_root / "jobs" / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    raw = os.environ.get("SITE_SESSION_PARSCODERS", "").strip()
    if not raw:
        print("RESULT no_session (SITE_SESSION_PARSCODERS empty)")
        return 1
    try:
        session = json.loads(raw)
    except Exception as e:
        print(f"RESULT session_parse_error ({type(e).__name__})")
        return 1
    cookies = session.get("cookies", []) if isinstance(session, dict) else []
    local_storage = session.get("localStorage", {}) if isinstance(session, dict) else {}
    clean_cookies = sanitize_cookies(cookies, default_domain="parscoders.com")
    print(f"session cookies={len(cookies)} clean={len(clean_cookies)} "
          f"localStorage_keys={len(local_storage) if isinstance(local_storage, dict) else 0}")

    from playwright.sync_api import sync_playwright

    result = {
        "job_id": job_id,
        "site": SITE,
        "logged_in": False,
        "display_name": None,
        "header_text": None,
        "email": None,
        "email_found_on": None,
        "pages": [],
    }

    proxy = os.environ.get("GWEB_PROXY", "").strip()
    launch_kw = {"proxy": {"server": proxy}} if proxy else {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, **launch_kw)
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 800})
            try:
                if clean_cookies:
                    context.add_cookies(clean_cookies)
                if isinstance(local_storage, dict) and local_storage:
                    ls_json = json.dumps(local_storage)
                    context.add_init_script(
                        "(() => { try { const d = " + ls_json + ";"
                        " for (const [k, v] of Object.entries(d)) {"
                        " window.localStorage.setItem(k, typeof v === 'string' ? v : JSON.stringify(v)); }"
                        " } catch (e) {} })();"
                    )
                page = context.new_page()

                # 1. Homepage: are we logged in, and what name does the header show?
                page.goto(SITE + "/", wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                page.screenshot(path=str(out_dir / "home.png"))
                title = page.title()
                body_text = page.locator("body").inner_text(timeout=10000)
                header_text = ""
                try:
                    header_text = page.locator("header").first.inner_text(timeout=5000)
                except Exception:
                    header_text = ""
                login_visible = False
                try:
                    login_visible = page.locator("a[href*='/login']").first.is_visible(timeout=3000)
                except Exception:
                    login_visible = False
                on_login_page = "/login" in page.url
                has_name = "محمد لعلی" in body_text or "محمد لعلی" in header_text
                # Logged in = header shows the account name + dashboard link and
                # we were not bounced to /login. (A login link can exist hidden
                # in the DOM, so its mere presence is not a logged-out signal.)
                result["logged_in"] = (not on_login_page) and has_name
                result["header_text"] = header_text.strip()[:500] if header_text else None
                result["display_name"] = "محمد لعلی" if has_name else None
                result["pages"].append({
                    "url": SITE + "/", "final_url": page.url, "title": title,
                    "on_login_page": on_login_page, "login_link_visible": login_visible,
                    "has_display_name": has_name, "emails": [],
                })
                print(f"home title={title!r} url={page.url} on_login={on_login_page} "
                      f"login_link={login_visible} has_name={has_name}")

                # 2. Discover profile links from the header/nav/sidebar.
                discovered = []
                try:
                    for a in page.locator("header a, nav a, aside a").all():
                        href = a.get_attribute("href") or ""
                        if href and PROFILE_HREF_RE.search(href):
                            abs_url = urljoin(SITE + "/", href)
                            if abs_url.startswith(SITE) and abs_url not in discovered:
                                discovered.append(abs_url)
                except Exception:
                    pass

                # 3. Visit candidate profile pages + discovered links (GET only).
                targets = [SITE + path for path in CANDIDATES]
                for u in discovered:
                    if u not in targets:
                        targets.append(u)
                seen = set()
                for idx, target in enumerate(targets):
                    if target in seen:
                        continue
                    seen.add(target)
                    try:
                        page.goto(target, wait_until="domcontentloaded", timeout=60000)
                        page.wait_for_timeout(2500)
                    except Exception as e:
                        result["pages"].append({
                            "url": target, "final_url": None, "title": None,
                            "error": type(e).__name__, "emails": [],
                        })
                        print(f"page {target} goto failed ({type(e).__name__})")
                        continue
                    shot = out_dir / f"page-{idx + 1}.png"
                    try:
                        page.screenshot(path=str(shot))
                    except Exception:
                        shot = None
                    # Scroll through the page so lazy content renders, then
                    # collect BOTH visible text AND form field values: an email
                    # sitting in an <input value="..."> never appears in
                    # innerText, so text-only extraction misses it.
                    try:
                        page.evaluate(
                            "() => { window.scrollTo(0, document.body.scrollHeight); }")
                        page.wait_for_timeout(1200)
                        page.evaluate("() => { window.scrollTo(0, 0); }")
                        page.wait_for_timeout(500)
                    except Exception:
                        pass
                    text = ""
                    try:
                        text = page.locator("body").inner_text(timeout=10000)
                    except Exception:
                        pass
                    field_text = ""
                    try:
                        field_text = page.evaluate(
                            """() => {
                                const vals = [];
                                document.querySelectorAll('input, textarea, select').forEach(el => {
                                    if (el.type === 'password') return;
                                    const v = (el.value || '').trim();
                                    if (v) vals.push(v);
                                    const ph = (el.getAttribute('placeholder') || '').trim();
                                    if (ph) vals.push(ph);
                                });
                                return vals.join('\\n');
                            }""")
                    except Exception:
                        field_text = ""
                    searchable = (text or "") + "\n" + (field_text or "")
                    emails = sorted(set(EMAIL_RE.findall(searchable)))
                    # Masked emails (m***@gmail.com) still count as a sighting.
                    masked = sorted(set(re.findall(r"[A-Za-z0-9._%+-]\*{2,}[A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+", searchable)))
                    entry = {
                        "url": target, "final_url": page.url, "title": page.title(),
                        "on_login_page": "/login" in page.url,
                        "has_display_name": "محمد لعلی" in searchable,
                        "emails": emails, "masked_emails": masked,
                        "screenshot": shot.name if shot else None,
                    }
                    result["pages"].append(entry)
                    print(f"page {target} -> {page.url} emails={len(emails)} "
                          f"masked={len(masked)} name={'محمد لعلی' in searchable}")
                    if emails and result["email"] is None:
                        result["email"] = emails[0]
                        result["email_found_on"] = page.url
                    if result["display_name"] is None and "محمد لعلی" in searchable:
                        result["display_name"] = "محمد لعلی"
                # 4. Portfolio listing: /resume/portfolio/ — read-only.
                try:
                    result["portfolio"] = probe_portfolio(page, out_dir)
                    print(f"portfolio samples={len(result['portfolio'].get('samples', []))} "
                          f"url={result['portfolio'].get('final_url')}")
                except Exception as e:
                    result["portfolio"] = {"error": f"{type(e).__name__}: {e}"}
                    print(f"portfolio probe failed ({type(e).__name__}: {e})")
                # 5. Sample details: show pages + edit-form probe (read-only).
                try:
                    result["sample_details"] = probe_sample_details(
                        page, out_dir, result["portfolio"].get("samples", []))
                    print(f"sample_details={len(result['sample_details'])}")
                except Exception as e:
                    result["sample_details"] = {"error": f"{type(e).__name__}: {e}"}
                    print(f"sample details probe failed ({type(e).__name__}: {e})")
            finally:
                context.close()
        finally:
            browser.close()

    with open(out_dir / "result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"RESULT ok logged_in={result['logged_in']} "
          f"display_name={'yes' if result['display_name'] else 'no'} "
          f"email_found={result['email'] is not None} "
          f"pages={len(result['pages'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
