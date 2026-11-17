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
  inspect — like probe, plus a deep dump of the create page: button types,
           hidden file inputs, the image widget's HTML, the skills select's
           options/required state. Submits NOTHING. Use it to answer "how
           does the image get attached / which button submits / is skills
           required" before writing upload logic.
  upload — fill the portfolio form (title, description, sample link) and
           submit, then re-load the profile and verify the sample appears.
  attach-test — like upload, but STOPS before the publish click: attach a
           real image, verify the form reveals and the publish button
           enables, then exit. Submits NOTHING. Tests the one chain a probe
           cannot: attach -> form reveal -> publish enabled.
  service-probe — Karlancer ONLY: probe the SERVICE flow (خدمت). The profile
           has NO portfolio form (probe 2026-11-17: public profile page has
           no forms at all); a freelancer's work is published as a service
           via the panel /panel/services -> "ثبت خدمت جدید". Dumps the
           services page and the new-service form structure. Submits NOTHING.
  service-create — Karlancer ONLY: create the service (title, description,
           category, price, delivery, demo screenshot) and verify it appears
           in /panel/services. Implemented from the service-probe form dump.

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

SITE_HOME = {"parscoders": "https://parscoders.com", "karlancer": "https://karlancer.com"}
# Karlancer services (خدمت): the profile has NO portfolio form. A freelancer's
# work is published as a SERVICE via the panel: /panel/services ("خدمت‌های من")
# -> "ثبت خدمت جدید" (register a new service). Probe 2026-11-17 confirmed the
# public profile page has no forms at all and the panel links to /panel/services.
SERVICES_URL = "https://karlancer.com/panel/services"
SERVICE_NEW_WORDS = ("خدمت جدید", "ثبت خدمت", "ایجاد خدمت", "افزودن خدمت", "خدمت تازه")
PROFILE_WORDS = ("پروفایل", "نمونه کار", "نمونه‌کار", "portfolio", "پروفایل من", "سوابق")
# Words that mark a portfolio ADD/MANAGE link (as opposed to a sample display page).
# Probe 2026-11-16 found the old logic returning a /resume/portfolio/show/<id>/ display
# page (an existing sample), whose only form is a feedback form — no title field.
ADD_WORDS = ("add", "create", "new", "manage", "edit", "افزودن", "ایجاد", "جدید", "مدیریت", "ویرایش", "اضافه")
# Strict add pick (probe 004): the add link must be about a SAMPLE/portfolio,
# not just a profile — "ویرایش اطلاعات/پروفایل" (edit profile) matched the loose
# pick and led to /user-info/update, a personal-info form with no title field.
# "ویرایش"/"edit" are NOT add words: editing a profile is not adding a sample.
SAMPLE_WORDS = ("نمونه", "portfolio", "sample")
ADD_WORDS_STRICT = ("add", "create", "new", "افزودن", "ایجاد", "جدید", "اضافه")
MANAGE_WORDS = ("manage", "مدیریت")
# Account holder's name — the logged-in header shows it (proven by the proposal
# preview screenshot, run 43859946135: «محمد لالی» in the header). Used to find
# the username/panel link and as a positive logged-in signal.
USERNAME = "محمد"
# Words that mark the logged-in user panel/dashboard link.
PANEL_WORDS = ("panel", "dashboard", "پنل", "داشبورد", "کاربری", "my-account")
TITLE_WORDS = ("عنوان", "title", "نام نمونه")
DESC_WORDS = ("توضیح", "شرح", "description", "توضیحات")
LINK_WORDS = ("لینک", "آدرس", "url", "link", "پیوند")
# Skills select (probe 2026-11-16): required (≥1), select2, max 5 selections.
# DEFAULT_SKILLS is ONLY a fallback when the lead's skills match no live
# option — and they are the user's own true skills, never invented ones.
DEFAULT_SKILLS = ("هوش مصنوعی", "پایتون")
MAX_SKILLS = 5
PUBLISH_BUTTON_ID = "app_bundle_portfolio_publish"
DRAFT_BUTTON_ID = "app_bundle_portfolio_save_draft"


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
    """Clean cookie dicts for Playwright context.add_cookies (mirror of runner.py).

    NOTE: this deliberately does NOT drop cookies whose `expires` is in the
    past. The uploader's old expiry filter dropped them — but session cookies
    are stored with expires=-1, so the filter dropped the very session cookie
    the site needs, and the page rendered logged-out even with a valid session.
    The runner keeps them and works; mirror it exactly.
    """
    out = []
    for c in cookies or []:
        if not isinstance(c, dict):
            continue
        clean = {}
        for key in ("name", "value", "url", "domain", "path", "expires", "httpOnly", "secure", "sameSite"):
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
    # Login prompt counts ONLY in the header: the footer keeps "ورود"/"ثبت نام"
    # links even when logged in (probe 004: username in header, yet a footer
    # link still tripped the page-wide check and reported logged_in=false).
    login_prompt = any((l.get("in_header") and
                        (("ورود" in (l.get("text") or "")) or ("ثبت نام" in (l.get("text") or ""))
                         or ("ثبت‌نام" in (l.get("text") or "")))) for l in links) or ("/login" in url.lower())
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
    """A portfolio ADD link: a SAMPLE/portfolio word + a strict add word,
    never a /show/ display page. "ویرایش پروفایل" must NOT match (it is a
    profile edit, not a sample add)."""
    for l in links:
        href = (l.get("href") or "").lower()
        text = (l.get("text") or "")
        if "/show/" in href:
            continue
        hay = (href + " " + text).lower()
        sampleish = any(w.lower() in hay for w in SAMPLE_WORDS)
        addish = any(w.lower() in hay for w in ADD_WORDS_STRICT)
        if sampleish and addish:
            return _abs_url(home, l["href"])
    return None


def _pick_manage_link(links, home):
    """A portfolio MANAGE page (e.g. 'مدیریت نمونه کار'): the add button may
    live there instead of the panel. Second tier after the strict add pick."""
    for l in links:
        href = (l.get("href") or "").lower()
        if "/show/" in href:
            continue
        hay = (href + " " + (l.get("text") or "")).lower()
        sampleish = any(w.lower() in hay for w in SAMPLE_WORDS)
        manageish = any(w.lower() in hay for w in MANAGE_WORDS)
        if sampleish and manageish:
            return _abs_url(home, l["href"])
    return None


def _pick_portfolio_list_link(links, home):
    """The portfolio LIST page (e.g. 'نمونه کارهای من' -> /resume/portfolio/):
    the add button lives there, not on the panel. Its link text is just 'my
    samples' — no add/manage word — so it needs its own pick: a /portfolio
    href that is not a /show/ display page and not the public /skill-portfolio
    browse page."""
    for l in links:
        href = (l.get("href") or "").lower()
        if "/portfolio" in href and "/show/" not in href and "skill-portfolio" not in href:
            return _abs_url(home, l["href"])
    return None


async def collect_buttons(page):
    """Visible button/link texts that could open an add-sample form (a <button>
    or a JS link, not an <a href>). The add control may be a button, not a link."""
    try:
        return await page.evaluate(
            """() => [...document.querySelectorAll('button, a[href=\"#\"], [role=\"button\"]')]
                .filter(e => e.offsetParent !== null)
                .map(e => (e.innerText || '').trim().slice(0, 80))
                .filter(t => t).slice(0, 60)""")
    except Exception:
        return []


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
            # second tier: the portfolio LIST page holds the add control
            list_url = _pick_portfolio_list_link(panel_links, home)
            if list_url:
                info["portfolio_list_url"] = list_url
                await page.goto(list_url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(2000)
                list_links = await collect_links(page)
                info["list_links"] = list_links
                add = _pick_add_link(list_links, home)
                if add:
                    return add, info
                # The add control may be a button/modal on the list page, not
                # a link. Report the list page itself so the probe dumps its
                # forms and buttons — that is where the add mechanism lives.
                info["list_note"] = "portfolio list page reached; no add link there (add may be a button/modal)"
                return list_url, info
            # third tier: a portfolio manage page may hold the add button
            manage_url = _pick_manage_link(panel_links, home)
            if manage_url:
                info["manage_url"] = manage_url
                await page.goto(manage_url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(2000)
                manage_links = await collect_links(page)
                info["manage_links"] = manage_links
                add = _pick_add_link(manage_links, home)
                if add:
                    return add, info
                info["manage_note"] = "manage page reached but no add-sample link found there"
            else:
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

    # (3) any SAMPLE/portfolio link that is not a /show/ display page and not
    # the public /skill-portfolio browse page (a bare "پروفایل" link is a
    # profile edit, not a sample page)
    for l in home_links:
        href = (l.get("href") or "").lower()
        hay = (href + " " + (l.get("text") or "")).lower()
        if (any(w.lower() in hay for w in SAMPLE_WORDS) and "/show/" not in href
                and "skill-portfolio" not in href):
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


async def inspect_portfolio_page(page):
    """Deep inspection of the portfolio create page (inspect mode only).

    Answers three questions without submitting anything:
      1. how is an image attached (plain file input / hidden file input /
         a JS crop/upload widget)?
      2. what are the submit buttons' type attributes (draft vs submit)?
      3. is the skills select required, and how does it work (plain select
         or a JS widget like select2 with an AJAX search box)?
    """
    js = r"""
    () => {
      const out = {buttons: [], file_inputs: [], upload_hits: [], skills: {},
                   fields_required: {}, form_attrs: {}, widget_html: '',
                   select2_present: false, cropper_present: false};
      const forms = [...document.querySelectorAll('form')];
      const pf = forms.find(f => f.querySelector('[name="app_bundle_portfolio[title]"]')) || forms[0];
      if (pf) {
        out.form_attrs = {action: pf.action || '', method: pf.method || '',
                          enctype: pf.enctype || '', novalidate: pf.hasAttribute('novalidate')};
        pf.querySelectorAll('button, input[type=submit], input[type=button]').forEach(b => {
          out.buttons.push({tag: b.tagName.toLowerCase(), type: b.getAttribute('type'),
            name: b.getAttribute('name'), id: b.getAttribute('id'),
            text: (b.innerText || b.value || '').trim().slice(0, 80),
            cls: (b.getAttribute('class') || '').slice(0, 120), disabled: !!b.disabled});
        });
        pf.querySelectorAll('input, textarea, select').forEach(el => {
          const key = el.getAttribute('name') || el.getAttribute('id') || el.tagName;
          out.fields_required[key] = {required: el.hasAttribute('required'),
            aria_required: el.getAttribute('aria-required'),
            tag: el.tagName.toLowerCase(), type: el.getAttribute('type')};
        });
        out.widget_html = pf.outerHTML.slice(0, 20000);
      }
      document.querySelectorAll('input[type=file]').forEach(el => {
        const cs = getComputedStyle(el);
        out.file_inputs.push({id: el.getAttribute('id'), name: el.getAttribute('name'),
          accept: el.getAttribute('accept'), multiple: el.hasAttribute('multiple'),
          display: cs.display, visibility: cs.visibility,
          offsetParentNull: el.offsetParent === null,
          cls: (el.getAttribute('class') || '').slice(0, 120),
          parent_html: (el.parentElement ? el.parentElement.outerHTML : '').slice(0, 600)});
      });
      const needles = ['آپلود', 'تصویر', 'عکس', 'upload', 'dropzone', 'cropper', 'crop',
                       'انتخاب فایل', 'انتخاب تصویر'];
      document.querySelectorAll('div, span, label, button, a, input').forEach(el => {
        const hay = ((el.getAttribute('class') || '') + ' ' + (el.getAttribute('id') || '')
          + ' ' + (el.innerText || '').slice(0, 60) + ' '
          + [...el.attributes].map(a => a.name + '=' + a.value).join(' ')).toLowerCase();
        if (needles.some(n => hay.includes(n.toLowerCase())) && el.children.length < 6) {
          out.upload_hits.push({tag: el.tagName.toLowerCase(),
            cls: (el.getAttribute('class') || '').slice(0, 100),
            id: el.getAttribute('id'), text: (el.innerText || '').trim().slice(0, 80),
            html: el.outerHTML.slice(0, 500)});
        }
      });
      out.upload_hits = out.upload_hits.slice(0, 30);
      const sel = document.querySelector('#app_bundle_portfolio_skills');
      if (sel) {
        out.skills = {tag: 'select', name: sel.getAttribute('name'),
          multiple: sel.hasAttribute('multiple'), required: sel.hasAttribute('required'),
          options: [...sel.options].map(o => ({value: o.value,
            text: o.text.trim().slice(0, 60), selected: o.selected})),
          option_count: sel.options.length, visible: sel.offsetParent !== null,
          cls: (sel.getAttribute('class') || '').slice(0, 120)};
      }
      out.select2_present = !!document.querySelector(
        '.select2-container, [class*=select2], [class*=chosen], [class*=selectize]');
      out.cropper_present = !!document.querySelector(
        '[class*=cropper], [class*=crop], [data-crop]');
      // Image widget: the drop area + the cropProps field's surroundings + the
      // scripts that wire the upload (so the attach mechanism can be read
      // without actually uploading a file).
      const drop = document.querySelector('#upload-drop-area');
      out.upload_widget = drop ? {html: drop.outerHTML.slice(0, 5000),
        parent_html: (drop.parentElement ? drop.parentElement.outerHTML : '').slice(0, 5000)} : null;
      const cp = document.querySelector('#app_bundle_portfolio_cropProps');
      out.crop_props = cp ? {value: cp.value || '',
        parent_html: (cp.parentElement ? cp.parentElement.outerHTML : '').slice(0, 2000),
        form: !!(cp.form), form_name: cp.form ? (cp.form.getAttribute('name') || '') : ''} : null;
      const fi = document.querySelector('#file-input');
      out.file_input_detail = fi ? {in_form: !!(fi.form),
        form_name: fi.form ? (fi.form.getAttribute('name') || '') : '',
        onchange: fi.getAttribute('onchange') || '',
        data_attrs: [...fi.attributes].map(a => a.name + '=' + a.value).join(' ').slice(0, 500)} : null;
      out.script_srcs = [...document.querySelectorAll('script[src]')].map(s => s.getAttribute('src')).slice(0, 60);
      out.inline_upload_js = [];
      document.querySelectorAll('script:not([src])').forEach(s => {
        const t = s.textContent || '';
        ['cropProps', 'file-input', 'upload-drop-area'].forEach(kw => {
          const i = t.indexOf(kw);
          if (i >= 0 && out.inline_upload_js.length < 10) {
            out.inline_upload_js.push({kw, snippet: t.slice(Math.max(0, i - 200), i + 600)});
          }
        });
      });
      // Full text of the inline script that wires the uploader (init options,
      // what enables the buttons, what fills cropProps) — the part the
      // snippets above only show in fragments.
      out.uploader_script = '';
      document.querySelectorAll('script:not([src])').forEach(s => {
        const t = s.textContent || '';
        if (t.includes('cropProps') && t.includes('uploader') && !out.uploader_script) {
          out.uploader_script = t.slice(0, 20000);
        }
      });
      return out;
    }
    """
    try:
        return await page.evaluate(js)
    except Exception as e:  # noqa: BLE001 - inspection failure must not crash the run
        return {"error": f"{type(e).__name__}: {e}"}


async def select_skills(page, wanted):
    """Select skills in the required multi-select (select2, max 5).

    wanted: list of skill names (from the lead). Each is matched against the
    LIVE option list (case-insensitive substring, either direction) so the
    uploader never depends on hardcoded option ids. Falls back to
    DEFAULT_SKILLS (the user's own true skills) when nothing matched.
    Returns (selected_values, selected_texts, used_default).
    """
    sel = "#app_bundle_portfolio_skills"
    try:
        options = await page.evaluate(
            """() => [...document.querySelectorAll('#app_bundle_portfolio_skills option')]
                .map(o => ({value: o.value, text: (o.text || '').trim()}))""")
    except Exception:
        return [], [], False

    def match(name):
        n = (name or "").strip().lower()
        if not n:
            return None
        for o in options:
            t = o["text"].lower()
            if n in t or t in n:
                return o
        return None

    picked = []
    for name in (wanted or []):
        m = match(name)
        if m and m["value"] not in [p["value"] for p in picked]:
            picked.append(m)
        if len(picked) >= MAX_SKILLS:
            break
    used_default = False
    if not picked:
        used_default = True
        for name in DEFAULT_SKILLS:
            m = match(name)
            if m and m["value"] not in [p["value"] for p in picked]:
                picked.append(m)
            if len(picked) >= 2:
                break
    if not picked:
        return [], [], used_default
    try:
        await page.select_option(sel, [p["value"] for p in picked], timeout=10000)
    except Exception:
        return [], [], used_default
    try:
        selected = await page.evaluate(
            """() => [...document.querySelectorAll('#app_bundle_portfolio_skills option:checked')]
                .map(o => ({value: o.value, text: (o.text || '').trim()}))""")
    except Exception:
        selected = picked
    return [s["value"] for s in selected], [s["text"] for s in selected], used_default


async def attach_image(page, shot_file):
    """Attach the sample screenshot through the image widget.

    The widget is NOT a plain file input inside the form: #file-input is a
    visibility:hidden input OUTSIDE any form, wired to PortfolioUploader,
    which uploads the file by AJAX and records crop data in the hidden
    app_bundle_portfolio[cropProps] field. Playwright set_input_files works
    on the hidden input and fires the change event the uploader listens to.
    Completion is observed via DOM signals (cropProps filled, drop-area
    content/class change, publish button enabled). Returns a dict; a timeout
    does not stop the run — the publish click handler itself blocks while an
    upload is incomplete, and verification catches a blocked submit.
    """
    out = {"attached": False, "signal": None, "crop_props": ""}
    try:
        await page.set_input_files("#file-input", shot_file, timeout=15000)
        out["attached"] = True
    except Exception as e:  # noqa: BLE001 - record and continue without an image
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    try:
        before = await page.evaluate(
            "() => { const da = document.querySelector('#upload-drop-area');"
            " return da ? da.innerHTML : ''; }")
    except Exception:
        before = ""
    import time as _t
    start = _t.time()
    while _t.time() - start < 60:
        try:
            state = await page.evaluate(
                """() => {
                  const cp = document.querySelector('#app_bundle_portfolio_cropProps');
                  const da = document.querySelector('#upload-drop-area');
                  const pb = document.querySelector('#app_bundle_portfolio_publish');
                  return {crop: cp ? (cp.value || '') : '',
                          drop_html: da ? da.innerHTML : '',
                          drop_cls: da ? (da.getAttribute('class') || '') : '',
                          publish_disabled: pb ? !!pb.disabled : null};
                }""")
        except Exception:
            state = {}
        out["crop_props"] = state.get("crop", "")
        if state.get("crop"):
            out["signal"] = "cropProps filled"
            return out
        if state.get("drop_html") and state["drop_html"] != before:
            out["signal"] = "drop-area content changed"
            return out
        if state.get("drop_cls") and "before-drop-div" not in state["drop_cls"]:
            out["signal"] = "drop-area class changed"
            return out
        if state.get("publish_disabled") is False:
            out["signal"] = "publish button enabled"
            return out
        await page.wait_for_timeout(2000)
    out["signal"] = "timeout: no completion signal in 60s"
    return out


async def wait_form_visible(page, timeout_s=60):
    """Wait until the form inputs are visible.

    The inputs live in #formInputsParent, which starts with class d-none
    (hidden) — the page only reveals it after an image is attached through
    the widget. Returns True when the title field is visible.
    """
    import time as _t
    start = _t.time()
    while _t.time() - start < timeout_s:
        try:
            vis = await page.evaluate(
                """() => {
                  const t = document.querySelector('#app_bundle_portfolio_title');
                  if (!t) return false;
                  const r = t.getBoundingClientRect();
                  const cs = getComputedStyle(t);
                  return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden';
                }""")
        except Exception:
            vis = False
        if vis:
            return True
        await page.wait_for_timeout(2000)
    return False


async def publish_button_state(page):
    """(disabled, exists) for the publish and draft buttons."""
    try:
        return await page.evaluate(
            """() => {
              const p = document.querySelector('#app_bundle_portfolio_publish');
              const d = document.querySelector('#app_bundle_portfolio_save_draft');
              return {publish_disabled: p ? !!p.disabled : null,
                      draft_disabled: d ? !!d.disabled : null,
                      publish_exists: !!p, draft_exists: !!d};
            }""")
    except Exception:
        return {}


async def wait_publish_enabled(page, timeout_s=60):
    """Wait until the publish button is enabled. Returns True/False."""
    import time as _t
    start = _t.time()
    while _t.time() - start < timeout_s:
        st = await publish_button_state(page)
        if st.get("publish_disabled") is False:
            return True
        await page.wait_for_timeout(2000)
    return False


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


async def dump_select_options(page):
    """For every <select> on the page: name/id + its options (value + text).
    dump_forms() does not include options; a service form's category/price
    selects are only understandable with them."""
    try:
        return await page.evaluate(
            """() => [...document.querySelectorAll('select')].map(s => ({
                name: s.getAttribute('name') || '',
                id: s.id || '',
                options: [...s.options].map(o => ({value: o.value,
                    text: (o.text || '').trim().slice(0, 80)}))
            }))""")
    except Exception:
        return []


async def dump_all_inputs(page):
    """Every input/textarea/select/contenteditable on the page, whether or not
    it sits inside a <form>. Karlancer's panel is a React SPA: the service
    form may be div-based, so dump_forms() (form-only) can miss it."""
    try:
        return await page.evaluate(
            """() => [...document.querySelectorAll('input, textarea, select, [contenteditable="true"]')].map(e => ({
                tag: e.tagName.toLowerCase(),
                type: e.getAttribute('type') || '',
                name: e.getAttribute('name') || '',
                id: e.id || '',
                placeholder: e.getAttribute('placeholder') || '',
                visible: !!(e.offsetParent !== null),
                label: (() => { const l = e.closest('label'); if (l) return l.innerText.trim().slice(0, 80);
                    const id = e.id; if (id) { const lab = document.querySelector(`label[for='${id}']`);
                    if (lab) return lab.innerText.trim().slice(0, 80); } return ''; })()
            }))""")
    except Exception:
        return []


async def service_probe(page, home, out_dir):
    """Karlancer service flow probe. Submits NOTHING.

    Goes to /panel/services ("خدمت‌های من"), dumps its links/buttons/forms,
    then follows/clicks the "new service" control ("ثبت خدمت جدید" and
    siblings) and dumps the service-creation form. Returns a result dict.
    Every URL comes from a link on the actual logged-in page — nothing guessed
    except the /panel/services entry point, which the 2026-11-17 probe already
    saw linked from the panel."""
    res = {}
    await page.goto(SERVICES_URL, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2500)
    await page.screenshot(path=os.path.join(out_dir, "services.png"))
    links = await collect_links(page)
    res["services_url"] = page.url
    res["logged_in"] = await detect_logged_in(page, links)
    res["links"] = links[:200]
    res["buttons"] = await collect_buttons(page)
    res["forms"] = await dump_forms(page)
    try:
        res["page_title"] = await page.title()
    except Exception:
        pass
    # Find the new-service control: a link first, then a button.
    new_link = None
    for l in links:
        hay = ((l.get("href") or "") + " " + (l.get("text") or "")).lower()
        if any(w.lower() in hay for w in SERVICE_NEW_WORDS):
            new_link = l
            break
    res["new_service_link"] = new_link
    href = (new_link.get("href") or "").strip() if new_link else ""
    if href and href not in ("#", "javascript:void(0)", "javascript:;", "javascript:"):
        create_url = _abs_url(home, href)
        res["create_url"] = create_url
        await page.goto(create_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2500)
        await page.screenshot(path=os.path.join(out_dir, "service_create.png"))
        res["create_page_url"] = page.url
        try:
            res["create_page_title"] = await page.title()
            res["create_headings"] = await page.evaluate(
                "() => [...document.querySelectorAll('h1,h2,h3')].map(h => h.innerText.trim()).filter(t => t).slice(0, 20)")
        except Exception:
            pass
        res["create_links"] = (await collect_links(page))[:200]
        res["create_buttons"] = await collect_buttons(page)
        res["create_forms"] = await dump_forms(page)
        res["create_selects"] = await dump_select_options(page)
        res["create_all_inputs"] = await dump_all_inputs(page)
        try:
            res["create_body_text"] = (await page.inner_text("body"))[:4000]
        except Exception:
            pass
        # The create page first asks for the service TYPE (professional
        # services vs. online teaching). Click "خدمات حرفه‌ای" to reach the
        # actual form, then dump everything again.
        clicked_type = None
        for word in ("خدمات حرفه‌ای", "خدمات حرفه اي"):
            try:
                loc = page.locator(f"text={word}")
                if await loc.count() > 0:
                    await loc.first.click(timeout=5000)
                    clicked_type = word
                    break
            except Exception as e:  # noqa: BLE001 - record and continue
                res.setdefault("type_click_errors", []).append(f"{word}: {type(e).__name__}: {e}")
        res["type_clicked"] = clicked_type
        if clicked_type:
            await page.wait_for_timeout(3000)
            await page.screenshot(path=os.path.join(out_dir, "service_create_step2.png"))
            res["step2_url"] = page.url
            try:
                res["step2_headings"] = await page.evaluate(
                    "() => [...document.querySelectorAll('h1,h2,h3')].map(h => h.innerText.trim()).filter(t => t).slice(0, 20)")
                res["step2_body_text"] = (await page.inner_text("body"))[:5000]
            except Exception:
                pass
            res["step2_links"] = (await collect_links(page))[:200]
            res["step2_buttons"] = await collect_buttons(page)
            res["step2_forms"] = await dump_forms(page)
            res["step2_selects"] = await dump_select_options(page)
            res["step2_all_inputs"] = await dump_all_inputs(page)
    else:
        # Maybe a <button> (not a link) opens the create form/modal.
        clicked = None
        for word in SERVICE_NEW_WORDS:
            try:
                loc = page.locator(f"button:has-text('{word}')")
                if await loc.count() > 0:
                    await loc.first.click(timeout=5000)
                    clicked = word
                    break
            except Exception as e:  # noqa: BLE001 - record and continue
                res.setdefault("button_errors", []).append(f"{word}: {type(e).__name__}: {e}")
        res["new_service_button"] = clicked
        if clicked:
            await page.wait_for_timeout(2500)
            await page.screenshot(path=os.path.join(out_dir, "service_create.png"))
            res["create_page_url"] = page.url
            res["create_links"] = (await collect_links(page))[:200]
            res["create_buttons"] = await collect_buttons(page)
            res["create_forms"] = await dump_forms(page)
            res["create_selects"] = await dump_select_options(page)
    res["note"] = "service-probe only: structure recorded, nothing submitted"
    return res


async def fill_by_placeholder(page, substr, value):
    """Fill the first VISIBLE input/textarea whose placeholder contains substr."""
    try:
        loc = page.locator(f"input[placeholder*='{substr}'], textarea[placeholder*='{substr}']")
        for i in range(await loc.count()):
            el = loc.nth(i)
            if await el.is_visible():
                await el.fill(value, timeout=5000)
                return True
        return False
    except Exception:
        return False


async def fill_by_name(page, name, value):
    """Fill the first VISIBLE input/textarea with the given name attribute."""
    try:
        loc = page.locator(f'[name="{name}"]')
        for i in range(await loc.count()):
            el = loc.nth(i)
            if await el.is_visible():
                await el.fill(value, timeout=5000)
                return True
        return False
    except Exception:
        return False


async def click_button_by_text(page, text):
    """Click the first visible <button> containing the text."""
    try:
        loc = page.locator(f"button:has-text('{text}')")
        for i in range(await loc.count()):
            el = loc.nth(i)
            if await el.is_visible():
                await el.click(timeout=5000)
                return True
        return False
    except Exception:
        return False


async def click_dropdown_option(page, wants):
    """Click the first visible option-like element whose text contains one of
    the wanted substrings, in order. SCOPED to real dropdown options only
    (li / [role=option] / [role=menuitem]) — the page's "top skills" chips
    and other buttons contain the same words and must NOT be clicked.
    Prefers the SHORTEST matching text (the real option) and returns the
    matched text, or None."""
    try:
        return await page.evaluate(
            """(wants) => {
                const sels = ['li', '[role="option"]', '[role="menuitem"]'];
                for (const w of wants) {
                    let best = null, bestLen = 1e9;
                    for (const sel of sels) {
                        for (const e of document.querySelectorAll(sel)) {
                            if (e.offsetParent === null) continue;
                            const t = (e.innerText || '').trim();
                            if (t && t.includes(w) && t.length < 80 && t.length < bestLen) {
                                best = e; bestLen = t.length;
                            }
                        }
                    }
                    if (best) { best.click(); return best.innerText.trim(); }
                }
                return null;
            }""", list(wants))
    except Exception:
        return None


async def read_dropdown_value(page, button_text):
    """Read the text of the dropdown control that contains button_text.
    Returns the SMALLEST visible element containing the label (the control
    itself, whose text becomes label + chosen value after a pick), or ''."""
    try:
        return await page.evaluate(
            """(bt) => {
                let best = null, bestLen = 1e9;
                for (const e of document.querySelectorAll('div, button, span')) {
                    if (e.offsetParent === null) continue;
                    const t = (e.innerText || '').trim();
                    if (t.includes(bt) && t.length < bestLen && t.length < 120) {
                        best = e; bestLen = t.length;
                    }
                }
                return best ? best.innerText.trim() : '';
            }""", button_text)
    except Exception:
        return ""


async def wait_skills_ready(page, timeout_ms=12000):
    """The skill suggestions load asynchronously (the page shows a spinner
    and 'please wait for the suggested skills to load' toasts). Wait until
    the loading indicator is gone, up to timeout_ms. Returns True if the
    spinner is gone."""
    try:
        await page.wait_for_timeout(2000)
        # the loader is a small box with three dots; wait for it to vanish
        await page.wait_for_function(
            """() => {
                const txt = document.body.innerText || '';
                return !txt.includes('لطفا صبر کنید تا مهارت های پیشنهادی بارگذاری شوند');
            }""", timeout=timeout_ms)
        return True
    except Exception:
        return False


async def read_error_toast(page):
    """Grab the validation-error toast text (the red box listing what is
    missing), if one is visible. Returns the text or ''."""
    try:
        return await page.evaluate(
            """() => {
                const els = [...document.querySelectorAll('div, p, span, li')];
                const hits = [];
                for (const e of els) {
                    if (e.offsetParent === null) continue;
                    const t = (e.innerText || '').trim();
                    if (t.length > 5 && t.length < 400 &&
                        (t.includes('حداقل') || t.includes('لطفا یکی از') ||
                         t.includes('نباید کمتر') || t.includes('نباید بیشتر')) &&
                        !t.includes('چه مهارت')) {
                        hits.push(t);
                    }
                }
                return hits.length ? hits[hits.length - 1] : '';
            }""")
    except Exception:
        return ""


async def title_input_visible(page):
    try:
        loc = page.locator("input[placeholder*='خدمت خود را شرح دهید']")
        return await loc.count() > 0 and await loc.first.is_visible()
    except Exception:
        return False


async def price_input_visible(page):
    try:
        loc = page.locator("input[placeholder*='هزینه']")
        return await loc.count() > 0 and await loc.first.is_visible()
    except Exception:
        return False


async def pick_dropdown_option(page, button_text, wants):
    """Open a dropdown by clicking its button, then pick an option by text.
    VERIFIES the pick: the button must show the chosen value afterwards
    (a click on a look-alike element elsewhere on the page is a false
    positive and must be reported, not trusted). Returns the picked text
    only when verified, else None."""
    try:
        before = await read_dropdown_value(page, button_text)
        if not await click_button_by_text(page, button_text):
            try:
                loc = page.locator(f":text('{button_text}')")
                if await loc.count() > 0:
                    await loc.first.click(timeout=5000)
                else:
                    return None
            except Exception:
                return None
        await page.wait_for_timeout(1500)
        picked = await click_dropdown_option(page, wants)
        if not picked:
            return None
        await page.wait_for_timeout(1000)
        after = await read_dropdown_value(page, button_text)
        # the control's text must now contain the picked value (beyond the
        # label/placeholder it had before); if it only still shows the
        # placeholder, the pick did not stick
        if picked in after and after != before:
            return picked
        return None
    except Exception:
        return None


async def add_skill_tag(page, skill):
    """Add one skill tag: type it into the tag input, wait for the suggestion
    dropdown, click the matching suggestion, and VERIFY a tag chip appeared.
    The suggestions load asynchronously — typing too early (before they are
    ready) means Enter does nothing, so we wait for a suggestion first.
    Returns True only when a chip with the skill text is present."""
    try:
        loc = page.locator('input[name="tag"]')
        if await loc.count() == 0:
            return False
        el = loc.first
        if not await el.is_visible():
            return False
        await el.click(timeout=5000)
        await el.fill(skill)
        # wait for a suggestion matching this skill to appear near the input
        clicked = False
        for _ in range(10):
            await page.wait_for_timeout(800)
            try:
                ok = await page.evaluate(
                    """(skill) => {
                        const inp = document.querySelector('input[name="tag"]');
                        if (!inp) return false;
                        // the suggestion list is rendered near the input:
                        // look for a visible li / button / div containing
                        // the skill text, preferring the one closest to the
                        // input in document order after it
                        const cands = [...document.querySelectorAll('li, button, [role="option"]')]
                            .filter(e => e.offsetParent !== null &&
                                (e.innerText || '').trim().includes(skill) &&
                                (e.innerText || '').trim().length < 60);
                        if (!cands.length) return false;
                        // prefer a candidate that is NOT in the "top skills"
                        // footer (those are far below the form); the
                        // suggestion list sits right under the input
                        const inpRect = inp.getBoundingClientRect();
                        let best = null, bestDist = 1e9;
                        for (const c of cands) {
                            const r = c.getBoundingClientRect();
                            const d = Math.abs(r.top - inpRect.bottom);
                            if (d < bestDist) { best = c; bestDist = d; }
                        }
                        if (best && bestDist < 400) { best.click(); return true; }
                        return false;
                    }""", skill)
                if ok:
                    clicked = True
                    break
            except Exception:
                pass
        await page.wait_for_timeout(800)
        if not clicked:
            # no suggestion appeared; try Enter as a last resort
            try:
                await el.press("Enter")
                await page.wait_for_timeout(800)
            except Exception:
                pass
        # verify: a tag chip with this skill must now be present (and the
        # input cleared). Chips are rendered as small pills near the input.
        try:
            present = await page.evaluate(
                """(skill) => {
                    const els = [...document.querySelectorAll('div, span, button, li')]
                        .filter(e => e.offsetParent !== null &&
                            (e.innerText || '').trim() === skill);
                    return els.length > 0;
                }""", skill)
            if present:
                return True
            try:
                return (await el.input_value()) == ""
            except Exception:
                return False
        except Exception:
            return False
    except Exception:
        return False


async def service_create(page, home, out_dir, args):
    """Karlancer service creation (the fix for the portfolio-form blocker).

    Walks the create-service wizard: type -> subject (title/category/
    subcategory/skills) -> images (optional) -> cost & deliverables ->
    FAQ (optional) -> terms + submit. Submits ONLY when every required field
    is verified filled; any failed step stops BEFORE the submit click and
    records exactly what is missing. Returns a result dict."""
    res = {}
    svc_title = (args.service_title or "").strip() or (args.title or "")[:30]
    res["service_title"] = svc_title
    res["price"] = args.price
    res["delivery_days"] = args.delivery_days
    # --- reach the create page via the services page link ---
    await page.goto(SERVICES_URL, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(3000)
    links = await collect_links(page)
    new_link = None
    for l in links:
        hay = ((l.get("href") or "") + " " + (l.get("text") or "")).lower()
        if any(w.lower() in hay for w in SERVICE_NEW_WORDS):
            new_link = l
            break
    href = (new_link.get("href") or "").strip() if new_link else ""
    if href and href not in ("#", "javascript:void(0)", "javascript:;", "javascript:"):
        await page.goto(_abs_url(home, href), wait_until="domcontentloaded", timeout=60000)
    else:
        await page.goto(home.rstrip("/") + "/panel/create-service",
                        wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(3000)
    # --- choose the professional-services type ---
    clicked = False
    for word in ("خدمات حرفه‌ای", "خدمات حرفه اي"):
        try:
            loc = page.locator(f"text={word}")
            if await loc.count() > 0:
                await loc.first.click(timeout=5000)
                clicked = True
                break
        except Exception:
            pass
    res["type_clicked"] = clicked
    if not clicked:
        res["error"] = "could not click the professional-services type card"
        return res
    await page.wait_for_timeout(3000)
    # --- STEP 1: subject ---
    # the skill suggestions load async; wait for them BEFORE touching the
    # form, or the tag input ignores Enter and the dropdown picks race
    step1 = {}
    step1["skills_ready"] = await wait_skills_ready(page)
    step1["category"] = await pick_dropdown_option(
        page, "دسته‌بندی خدمت", ("برنامه نویسی", "پایتون", "برنامه‌نویسی"))
    step1["subcategory"] = await pick_dropdown_option(
        page, "زیردسته", ("پایتون", "ربات", "برنامه‌نویسی پایتون", "برنامه نویسی"))
    skills_added = []
    for s in [x.strip() for x in (args.skills or "").split(",") if x.strip()]:
        if await add_skill_tag(page, s):
            skills_added.append(s)
    step1["skills_added"] = skills_added
    # title LAST (the suggestions loading re-renders the form and can clear
    # an earlier fill); read it back and re-type if it did not stick
    step1["title"] = await fill_by_placeholder(page, "خدمت خود را شرح دهید", svc_title)
    try:
        title_val = await page.evaluate(
            """() => { const els = [...document.querySelectorAll('input')];
                const t = els.find(e => (e.placeholder || '').includes('خدمت خود را شرح دهید'));
                return t ? t.value : ''; }""")
        step1["title_value"] = title_val
        if title_val != svc_title:
            # fill() did not stick (React re-render); type it by keyboard
            try:
                loc = page.locator("input[placeholder*='خدمت خود را شرح دهید']")
                await loc.first.click(timeout=5000)
                await loc.first.fill("")
                await page.keyboard.type(svc_title, delay=30)
                await page.wait_for_timeout(800)
                title_val = await page.evaluate(
                    """() => { const els = [...document.querySelectorAll('input')];
                        const t = els.find(e => (e.placeholder || '').includes('خدمت خود را شرح دهید'));
                        return t ? t.value : ''; }""")
                step1["title_value"] = title_val
                step1["title"] = (title_val == svc_title)
            except Exception:
                step1["title"] = False
    except Exception:
        step1["title_value"] = ""
    res["step1"] = step1
    await page.screenshot(path=os.path.join(out_dir, "service_step1.png"))
    if not step1.get("title"):
        res["error"] = "step 1: could not fill the service title"
        return res
    if not step1.get("category"):
        res["error"] = "step 1: could not pick a category (verified pick failed)"
        return res
    if not step1.get("subcategory"):
        res["error"] = "step 1: could not pick a subcategory (verified pick failed)"
        return res
    if len(skills_added) < 3:
        res["error"] = (f"step 1: only {len(skills_added)} skill(s) added "
                        f"({skills_added}) — the form needs at least 3")
        return res
    if not await click_button_by_text(page, "مرحله بعد"):
        res["error"] = "step 1: could not click Next"
        return res
    await page.wait_for_timeout(3000)
    # ADVANCEMENT GUARD: a Next click is not an advance — validation may
    # have failed and left us on step 1. Only proceed if the title input
    # is gone; otherwise stop with the form's own error message.
    if await title_input_visible(page):
        res["error"] = "step 1: wizard did not advance — " + (await read_error_toast(page) or "validation failed (no error text captured)")
        res["validation_error"] = await read_error_toast(page)
        return res
    # --- STEP 2: images (optional) ---
    step2 = {}
    shot = args.screenshot or ""
    if shot and os.path.isfile(shot):
        try:
            loc = page.locator('input[name="upload_photo"]')
            if await loc.count() > 0:
                await loc.first.set_input_files(shot, timeout=20000)
                await page.wait_for_timeout(4000)
                step2["image_attached"] = True
                step2["image_file"] = os.path.basename(shot)
            else:
                step2["image_attached"] = False
                step2["image_error"] = "no upload_photo input on this step"
        except Exception as e:  # noqa: BLE001 - record and continue (optional)
            step2["image_attached"] = False
            step2["image_error"] = f"{type(e).__name__}: {e}"
    else:
        step2["image_attached"] = False
        step2["image_note"] = "no screenshot file supplied; images are optional"
    res["step2"] = step2
    await page.screenshot(path=os.path.join(out_dir, "service_step2.png"))
    if not await click_button_by_text(page, "مرحله بعد"):
        # the images step may use a different next control; try a generic next
        if not await click_button_by_text(page, "بعد"):
            res["error"] = "step 2: could not click Next"
            return res
    await page.wait_for_timeout(3000)
    # ADVANCEMENT GUARD: step 3's price input must now be visible
    if not await price_input_visible(page):
        res["error"] = "step 2: wizard did not advance to cost/deliverables — " + (await read_error_toast(page) or "no error text captured")
        return res
    # --- STEP 3: cost & deliverables ---
    step3 = {}
    step3["price"] = await fill_by_placeholder(page, "هزینه", args.price)
    step3["package_title"] = await fill_by_placeholder(
        page, "مثلا طراحی یک لوگو", "ربات رزرو خودکار نوبت تعویض پلاک")
    step3["delivery"] = await fill_by_placeholder(page, "روز", f"{args.delivery_days} روز")
    step3["orders"] = await fill_by_placeholder(page, "سفارش", "1")
    step3["description"] = await fill_by_name(page, "description", args.description)
    step3["delivery_description"] = await fill_by_name(
        page, "deliveryDescription",
        "ربات آماده‌ی اجرا + راهنمای نصب و استفاده + یک‌بار تنظیم مشخصات شما (نام، پلاک، کد ملی، موبایل، مرکزها و بازه‌ی زمانی).")
    step3["requirements"] = await fill_by_name(
        page, "requirements",
        "نام و نام خانوادگی، شماره پلاک، کد ملی، شماره موبایل، مرکزهای مورد نظر و بازه‌ی زمانی.")
    res["step3"] = step3
    await page.screenshot(path=os.path.join(out_dir, "service_step3.png"))
    if not step3.get("price"):
        res["error"] = "step 3: could not fill the price"
        return res
    if not step3.get("description"):
        res["error"] = "step 3: could not fill the description"
        return res
    if not await click_button_by_text(page, "مرحله بعد"):
        if not await click_button_by_text(page, "بعد"):
            res["error"] = "step 3: could not click Next"
            return res
    await page.wait_for_timeout(3000)
    # ADVANCEMENT GUARD: the price input must be gone (we left step 3)
    if await price_input_visible(page):
        res["error"] = "step 3: wizard did not advance to FAQ — " + (await read_error_toast(page) or "no error text captured")
        return res
    # --- STEP 4: FAQ (optional) — add one, but never block on it ---
    step4 = {}
    try:
        q = await fill_by_placeholder(page, "سوال خود را بنویسید",
                                      "ربات چطور کار می‌کند؟")
        a = await fill_by_placeholder(page, "جواب خود را بنویسید",
                                      "سامانه‌ی تعویض پلاک را پیوسته زیر نظر می‌گیرد؛ به محض باز شدن نوبت مناسب، آن را با مشخصات شما رزرو می‌کند و تا مرحله‌ی پرداخت می‌برد. پرداخت را خودتان کامل می‌کنید.")
        step4["faq_filled"] = bool(q and a)
        if q and a:
            # there may be an explicit "add FAQ" button; try common labels
            for lbl in ("افزودن", "اضافه کردن", "ثبت سوال"):
                if await click_button_by_text(page, lbl):
                    step4["faq_added"] = lbl
                    break
            await page.wait_for_timeout(1000)
    except Exception as e:  # noqa: BLE001 - FAQ is optional
        step4["faq_error"] = f"{type(e).__name__}: {e}"
    res["step4"] = step4
    await page.screenshot(path=os.path.join(out_dir, "service_step4.png"))
    # --- terms + submit (ONLY when the required fields are verified) ---
    try:
        acc = page.locator("#accept")
        if await acc.count() > 0 and not await acc.first.is_checked():
            try:
                await acc.first.check(timeout=5000)
                res["terms_accepted"] = True
            except Exception:
                # fall back to clicking its label
                try:
                    await page.locator("label[for='accept']").first.click(timeout=5000)
                    res["terms_accepted"] = True
                except Exception:
                    res["terms_accepted"] = False
        else:
            res["terms_accepted"] = True
    except Exception:
        res["terms_accepted"] = False
    # final guard: re-read the title + price values before submitting
    try:
        title_val = await page.evaluate(
            """() => { const els = [...document.querySelectorAll('input')];
                const t = els.find(e => (e.placeholder || '').includes('خدمت خود را شرح دهید'));
                return t ? t.value : ''; }""")
        res["title_value_before_submit"] = title_val
    except Exception:
        res["title_value_before_submit"] = ""
    if not res.get("title_value_before_submit"):
        res["error"] = "guard: title is empty before submit — refusing to submit"
        return res
    if not res.get("terms_accepted"):
        res["error"] = "guard: terms checkbox could not be accepted — refusing to submit"
        return res
    clicked_submit = None
    for lbl in ("ثبت خدمت", "انتشار خدمت", "ثبت نهایی", "ایجاد خدمت", "ثبت"):
        if await click_button_by_text(page, lbl):
            clicked_submit = lbl
            break
    res["submit_clicked"] = clicked_submit
    if not clicked_submit:
        res["error"] = "could not find/click the submit button"
        return res
    await page.wait_for_timeout(5000)
    await page.screenshot(path=os.path.join(out_dir, "service_after_submit.png"))
    res["submitted"] = True
    try:
        res["after_submit_url"] = page.url
        res["after_submit_text"] = (await page.inner_text("body"))[:1500]
    except Exception:
        pass
    # --- verify: the service must appear in /panel/services ---
    await page.goto(SERVICES_URL, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(3000)
    await page.screenshot(path=os.path.join(out_dir, "services_after.png"))
    try:
        body = await page.inner_text("body")
        res["verified"] = svc_title in body
        # find the service link (its public URL) if present
        svc_url = None
        for l in await collect_links(page):
            if svc_title in (l.get("text") or ""):
                svc_url = _abs_url(home, l["href"])
                break
        res["service_url"] = svc_url
        # which tab is it under? (published vs awaiting approval)
        res["services_body_snippet"] = body[:2000]
    except Exception as e:  # noqa: BLE001 - record
        res["verified"] = False
        res["verify_error"] = f"{type(e).__name__}: {e}"
    res["ok"] = bool(res.get("verified"))
    if not res.get("ok"):
        res["error"] = "submitted but the service title was not found in /panel/services"
    return res


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
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(viewport={"width": 1280, "height": 800})
            cookies = sanitize_cookies(session.get("cookies"), home.split("//")[1])
            if cookies:
                await ctx.add_cookies(cookies)
            # Inject localStorage via add_init_script BEFORE any page loads
            # (mirror of runner.py). The old approach — load the page, set
            # localStorage, reload — left the home page rendering logged-out
            # even with a valid session.
            ls_data = session.get("localStorage") or {}
            if isinstance(ls_data, dict) and ls_data:
                ls_json = json.dumps(ls_data)
                init_script = f"""(() => {{
                    try {{
                        const data = {ls_json};
                        for (const [k, v] of Object.entries(data)) {{
                            const val = typeof v === 'string' ? v : JSON.stringify(v);
                            window.localStorage.setItem(k, val);
                        }}
                    }} catch (e) {{}}
                }})();"""
                await ctx.add_init_script(init_script)
            page = await ctx.new_page()
            await page.goto(home, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2000)
            if args.mode in ("service-probe", "service-create"):
                # Karlancer service flow (no portfolio form on this site).
                if args.mode == "service-probe":
                    svc = await service_probe(page, home, out_dir)
                    result.update(svc)
                    result["ok"] = True
                    return save()
                svc = await service_create(page, home, out_dir, args)
                result.update(svc)
                result["ok"] = bool(svc.get("ok"))
                return save()
            profile_url = args.profile_url
            info = {}
            if not profile_url:
                profile_url, info = await find_profile_url(page, home)
            result["links_found"] = (info.get("home_links") or [])[:200]
            result["logged_in"] = info.get("logged_in", {})
            if info.get("panel_url"):
                result["panel_url"] = info["panel_url"]
            if info.get("panel_links"):
                result["panel_links"] = info["panel_links"][:200]
            if info.get("menu_links"):
                result["menu_links"] = info["menu_links"][:200]
            if info.get("manage_url"):
                result["manage_url"] = info["manage_url"]
            if info.get("manage_links"):
                result["manage_links"] = info["manage_links"][:200]
            if info.get("portfolio_list_url"):
                result["portfolio_list_url"] = info["portfolio_list_url"]
            if info.get("list_links"):
                result["list_links"] = info["list_links"][:200]
            for key in ("panel_note", "panel_error", "menu_error", "manage_note", "list_note"):
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
            result["buttons"] = await collect_buttons(page)
            forms = await dump_forms(page)
            result["forms"] = forms
            with open(os.path.join(out_dir, "forms.json"), "w", encoding="utf-8") as f:
                json.dump(forms, f, ensure_ascii=False, indent=2)
            if args.mode == "inspect":
                result["inspection"] = await inspect_portfolio_page(page)
                with open(os.path.join(out_dir, "inspect.json"), "w", encoding="utf-8") as f:
                    json.dump(result["inspection"], f, ensure_ascii=False, indent=2)
                result["ok"] = True
                result["note"] = "inspection only: page structure recorded, nothing submitted"
                return save()
            if args.mode == "probe":
                # Dry-fill (probe only, no submit, no file attach): fill the
                # required fields and report whether the publish button becomes
                # enabled — that answers "what enables the buttons" without
                # submitting anything. The page is discarded when the browser
                # closes, so no sample is created.
                # NOTE (probe 007): the inputs live in #formInputsParent with
                # class d-none — the page only reveals them after an image is
                # attached. A dry-fill without an image therefore cannot fill
                # anything. For the probe we reveal the container via JS (an
                # inspection aid only; the real flow reveals it by attaching
                # the image) so the fill/skills/publish logic can be verified.
                dry = {"publish_before": await publish_button_state(page)}
                try:
                    dry["form_hidden_initially"] = await page.evaluate(
                        "() => { const p = document.querySelector('#formInputsParent');"
                        " return p ? p.classList.contains('d-none') : null; }")
                    await page.evaluate(
                        "() => { const p = document.querySelector('#formInputsParent');"
                        " if (p) p.classList.remove('d-none'); }")
                except Exception:
                    dry["form_hidden_initially"] = None
                target = None
                for form in forms:
                    if pick_field(form["fields"], TITLE_WORDS):
                        target = form
                        break
                if target:
                    fields = target["fields"]
                    tf = pick_field(fields, TITLE_WORDS)
                    df = pick_field(fields, DESC_WORDS)
                    dry["title_filled"] = await fill_field(page, tf, args.title) if tf else False
                    dry["description_filled"] = await fill_field(page, df, args.description) if df else False
                    vals, texts, used_default = await select_skills(
                        page, [s.strip() for s in (args.skills or "").split(",") if s.strip()])
                    dry["skills_selected"] = vals
                    dry["skills_texts"] = texts
                    dry["skills_used_default"] = used_default
                    await page.wait_for_timeout(3000)
                    dry["publish_after"] = await publish_button_state(page)
                    dry["publish_enabled_after_fill"] = await wait_publish_enabled(page, timeout_s=15)
                else:
                    dry["error"] = "no portfolio form with a title field found"
                result["dry_fill"] = dry
                result["ok"] = True
                result["note"] = "probe only: form structure recorded, nothing submitted"
                return save()
            # ---- upload mode: capture the sample page itself for an image field ----
            # attach-test skips this: it always uses the --screenshot file (the
            # rendered demo PNG), and fails fast without one.
            sample_png = os.path.join(out_dir, "sample.png")
            if args.mode == "upload":
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
            # (1) Attach the image FIRST through the widget (#file-input +
            # PortfolioUploader, AJAX). The upload runs while we fill the
            # fields. ParsCoders' form has no link field — the sample IS the
            # uploaded image (thumbnail) + title/description/skills.
            if args.screenshot and os.path.isfile(args.screenshot):
                shot_file = args.screenshot
            elif os.path.isfile(sample_png):
                shot_file = sample_png
            else:
                shot_file = None
            if shot_file:
                filled["image"] = await attach_image(page, shot_file)
            else:
                # The image is mandatory: the form stays hidden (d-none) and
                # the publish button stays disabled without it. Fail fast
                # instead of waiting out the visibility timeout.
                result["filled"] = {"image": {"attached": False, "error": "no screenshot file available"}}
                result["error"] = "no screenshot file available — the image is required to reveal the form"
                return save()
            # (1b) The form inputs are hidden (d-none) until the image is
            # attached. Do not fill until they are visible — filling hidden
            # fields fails silently (probe 007 proved this).
            form_visible = await wait_form_visible(page, timeout_s=60)
            filled["form_visible_after_attach"] = form_visible
            if not form_visible:
                result["filled"] = filled
                result["error"] = "form inputs never became visible after image attach"
                return save()
            # (2) Fill title + description.
            tf = pick_field(fields, TITLE_WORDS)
            df = pick_field(fields, DESC_WORDS)
            if tf:
                filled["title"] = await fill_field(page, tf, args.title)
            if df:
                filled["description"] = await fill_field(page, df, args.description)
            # (3) Skills — required (≥1). Fail rather than submit without one.
            vals, texts, used_default = await select_skills(
                page, [s.strip() for s in (args.skills or "").split(",") if s.strip()])
            filled["skills"] = {"values": vals, "texts": texts, "used_default": used_default}
            if not vals:
                result["filled"] = filled
                result["error"] = "could not select any skill (required field); refusing to submit"
                return save()
            result["filled"] = filled
            if not any([filled.get("title"), filled.get("description"), vals]):
                result["error"] = "could not fill any portfolio field"
                return save()
            # (4) Wait for the publish button to enable (it starts disabled).
            # Never force a disabled button — that would bypass the site's
            # own validation.
            if not await wait_publish_enabled(page, timeout_s=60):
                result["publish_state"] = await publish_button_state(page)
                result["error"] = "publish button stayed disabled after filling fields + image"
                return save()
            # ---- attach-test: STOP HERE. Publish (and draft) are NEVER clicked,
            # nothing is submitted, no sample is created. The chain
            # attach -> form reveal -> publish enabled is the whole point. ----
            if args.mode == "attach-test":
                result["attach_test"] = {
                    "attached_ok": bool(filled.get("image", {}).get("attached")),
                    "attach_signal": filled.get("image", {}).get("signal"),
                    "image_file": os.path.basename(shot_file) if shot_file else None,
                    "form_visible": form_visible,
                    "title_filled": filled.get("title"),
                    "description_filled": filled.get("description"),
                    "skills_selected": vals,
                    "skills_texts": texts,
                    "publish_enabled": True,
                    "publish_state": await publish_button_state(page),
                }
                result["ok"] = True
                result["note"] = ("attach-test only: image attached, form revealed, "
                                  "publish enabled — publish NOT clicked, nothing submitted")
                return save()
            # (5) Submit EXPLICITLY via the publish button (id, then its exact
            # text). NEVER a generic button[type=submit] — the first submit
            # button on this form is the DRAFT button (save_draft), and
            # ':has-text(ذخیره)' also matches the draft.
            clicked = None
            for sel in (f"#{PUBLISH_BUTTON_ID}", "button:has-text('ارسال نمونه‌کار')"):
                try:
                    await page.click(sel, timeout=10000)
                    clicked = sel
                    break
                except Exception:
                    continue
            result["submit_clicked"] = clicked
            if not clicked:
                result["error"] = "could not click the publish button"
                return save()
            await page.wait_for_timeout(4000)
            # Did the site's own handler block the submit (upload incomplete)?
            try:
                blocked = await page.evaluate(
                    "() => { const s = document.querySelector('.swal2-container, .swal-modal');"
                    " return !!s && s.offsetParent !== null; }")
            except Exception:
                blocked = False
            result["submit_blocked_by_upload_warning"] = bool(blocked)
            if blocked:
                result["error"] = "publish click was blocked by the site (upload still in progress)"
                return save()
            result["submitted"] = True
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
    ap.add_argument("--skills", default="",
                    help="Comma-separated skill names from the lead (matched against the live option list).")
    ap.add_argument("--mode", required=True, choices=["probe", "inspect", "upload", "attach-test", "service-probe", "service-create"])
    ap.add_argument("--service-title", default="",
                    help="Short service title for Karlancer (max 30 chars); defaults to --title truncated to 30 chars.")
    ap.add_argument("--price", default="9000000",
                    help="Service price in Toman (Karlancer service-create only).")
    ap.add_argument("--delivery-days", default="7",
                    help="Delivery time in days (Karlancer service-create only).")
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--results-dir", required=True)
    ap.add_argument("--profile-url", default="")
    ap.add_argument("--screenshot", default="")
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
