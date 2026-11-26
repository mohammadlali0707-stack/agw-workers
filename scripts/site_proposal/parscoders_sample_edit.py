#!/usr/bin/env python3
"""Inspect / edit ONE ParsCoders portfolio sample (test: sample 61734).

Modes (env EDIT_MODE):
  inspect - READ-ONLY: show page (rejection reason, current description,
            image URLs) + the update-new form dump (fields, current values,
            buttons) + the upload-widget dump (#file-input, drop area,
            cropProps). Nothing is changed.
  edit    - inspect first, then fill ONLY the description with
            NEW_DESCRIPTION, keep title/skills unchanged, attach NO file,
            click the submit/publish button once, record the result page
            and re-read the portfolio list row for this sample.
  image   - inspect first, then attach ONLY the file in IMAGE_FILE through
            the widget's #file-input, wait for cropProps to change to a NEW
            upload, keep title/description/skills UNTOUCHED, click the
            publish button once, record the result page, re-read the
            portfolio list row AND the show page images (new image URL must
            differ from the old one).

Safety:
  * Operates on ONE sample id (env SAMPLE_ID, default 61734). No other
    sample is touched, nothing is reactivated, nothing new is created.
  * Aborts unless the edit form's title matches the expected sample title.
  * Aborts if NEW_DESCRIPTION contains anything URL-like (http://,
    https://, t.me, www., .com, .ir) - the rejection bans addresses.
  * Image mode aborts unless IMAGE_FILE exists on disk and the cropProps
    value actually changes to a new upload after attaching.
  * Stdout prints RESULT lines only, never session values.

Env:
  SITE_SESSION_PARSCODERS - sealed session JSON {"cookies": [...], "localStorage": {...}}
  GWEB_PROXY              - optional proxy server (Oracle exit on the runner)
  EDIT_RESULTS_DIR        - results root (default: hq-ai-results)
  EDIT_JOB_ID             - job id, used for the output subdir (default: manual)
  EDIT_MODE               - inspect | edit (default: inspect)
  SAMPLE_ID               - portfolio sample id (default: 61734)
  EXPECTED_TITLE          - title the edit form must show (default: چت‌بات هوشمند سایت)
  NEW_DESCRIPTION         - new description text (edit mode only)
  IMAGE_FILE              - repo-relative image path to attach (image mode only)

Output: <results>/jobs/<job_id>/inspect.json (+ edit.json in edit mode)
        + screenshots show.png, edit-form.png, after.png
Exit 0 when the run completed (even if the edit was rejected by the site);
exit 1 only on fatal errors (no session, not logged in, wrong sample,
form missing, description failed the safety check).
"""

import json
import os
import re
import sys
from pathlib import Path

SITE = "https://parscoders.com"
URL_LIKE = re.compile(r"https?://|t\.me|www\.|\.com|\.ir|\.net|\.org", re.I)


def sanitize_cookies(cookies, default_domain=None):
    """Clean cookie dicts for Playwright context.add_cookies (same rules as
    scripts/site_proposal/parscoders_profile_probe.py)."""
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


def dump_forms(page):
    return page.evaluate(
        """() => [...document.querySelectorAll('form')].map(f => ({
            action: f.action || '', method: f.method || '',
            fields: [...f.querySelectorAll('input, textarea, select')].map(el => {
                const o = {tag: el.tagName.toLowerCase(),
                    type: el.getAttribute('type') || '',
                    name: el.getAttribute('name') || '', id: el.id || '',
                    required: !!el.required,
                    value: (el.value || '').slice(0, 2000)};
                if (el.tagName.toLowerCase() === 'select') {
                    o.options = [...el.options].map(op => ({
                        value: op.value, text: (op.text || '').slice(0, 80),
                        selected: !!op.selected}));
                }
                if (el.type === 'checkbox' || el.type === 'radio') o.checked = !!el.checked;
                if (el.type === 'file') o.accept = el.getAttribute('accept') || '';
                return o;
            }),
            buttons: [...f.querySelectorAll('button, input[type=submit]')].map(b => ({
                text: ((b.innerText || b.value || '').trim().slice(0, 80)),
                id: b.id || '', type: b.getAttribute('type') || '',
                name: b.getAttribute('name') || ''}))
        }))""")


def main():
    job_id = os.environ.get("EDIT_JOB_ID", "manual")
    mode = os.environ.get("EDIT_MODE", "inspect").strip().lower()
    sample_id = os.environ.get("SAMPLE_ID", "61734").strip()
    expected_title = os.environ.get("EXPECTED_TITLE", "").strip() or "چت‌بات هوشمند سایت"
    new_desc = os.environ.get("NEW_DESCRIPTION", "")
    results_root = Path(os.environ.get("EDIT_RESULTS_DIR", "hq-ai-results"))
    out_dir = results_root / "jobs" / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    if mode not in ("inspect", "edit", "image"):
        print(f"RESULT bad_mode ({mode})")
        return 1
    if mode == "edit":
        if not new_desc.strip():
            print("RESULT edit_no_description (NEW_DESCRIPTION empty)")
            return 1
        if URL_LIKE.search(new_desc):
            print("RESULT description_has_url_like (refusing to submit)")
            return 1
    image_file = os.environ.get("IMAGE_FILE", "").strip()
    if mode == "image":
        if not image_file:
            print("RESULT image_no_file (IMAGE_FILE empty)")
            return 1
        if not os.path.isfile(image_file):
            print(f"RESULT image_file_missing ({image_file})")
            return 1
        if not expected_title.strip():
            print("RESULT image_no_expected_title")
            return 1

    raw = os.environ.get("SITE_SESSION_PARSCODERS", "").strip()
    if not raw:
        print("RESULT no_session (SITE_SESSION_PARSCODERS empty)")
        return 1
    try:
        session = json.loads(raw)
    except Exception:
        print("RESULT session_parse_error")
        return 1
    cookies = session.get("cookies", []) if isinstance(session, dict) else []
    local_storage = session.get("localStorage", {}) if isinstance(session, dict) else {}
    clean_cookies = sanitize_cookies(cookies, default_domain="parscoders.com")
    print(f"session cookies={len(cookies)} clean={len(clean_cookies)} "
          f"localStorage_keys={len(local_storage) if isinstance(local_storage, dict) else 0}")

    from playwright.sync_api import sync_playwright

    proxy = os.environ.get("GWEB_PROXY", "").strip()
    launch_kw = {"proxy": {"server": proxy}} if proxy else {}
    result = {"job_id": job_id, "mode": mode, "sample_id": sample_id}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, **launch_kw)
        context = None
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 900})
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
            dialogs = []
            page.on("dialog", lambda d: (dialogs.append(d.message[:200]), d.accept()))

            # 1. Logged-in check on the homepage.
            page.goto(SITE + "/", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            home_text = page.locator("body").inner_text(timeout=10000)
            logged_in = ("/login" not in page.url) and ("محمد لعلی" in home_text)
            result["logged_in"] = logged_in
            print(f"home url={page.url} logged_in={logged_in}")
            if not logged_in:
                print("RESULT not_logged_in")
                return 1

            # 2. Portfolio list: find this sample's real show + edit URLs.
            page.goto(SITE + "/resume/portfolio/", wait_until="domcontentloaded",
                      timeout=60000)
            page.wait_for_timeout(3000)
            links = page.evaluate(
                """() => [...document.querySelectorAll('a[href]')].map(a => ({
                    href: a.getAttribute('href') || '',
                    text: (a.innerText || '').trim().slice(0, 60)}))""")
            show_url = ""
            edit_url = ""
            for l in links:
                if f"/show/{sample_id}/" in l["href"]:
                    show_url = l["href"]
                if l["href"] == f"/resume/portfolio/{sample_id}/update-new":
                    edit_url = l["href"]
            result["show_url"] = show_url
            result["edit_url"] = edit_url
            result["edit_url_source"] = "list-link" if edit_url else ""
            result["portfolio_links"] = [
                l for l in links if "/resume/portfolio/" in l["href"]][:40]
            print(f"list show_url={show_url} edit_url={edit_url}")
            if not show_url:
                print("RESULT sample_links_not_found")
                return 1
            if not edit_url:
                # Under-review samples show NO edit link on the list page
                # (only rejected ones do). The direct update-new URL may
                # still open the form — try it, and let the title check
                # below abort if the site refuses the edit.
                edit_url = f"/resume/portfolio/{sample_id}/update-new"
                result["edit_url"] = edit_url
                result["edit_url_source"] = "constructed-fallback"
                print(f"list: no edit link; falling back to constructed {edit_url}")

            # 3. Show page: rejection reason, current description, images.
            page.goto(SITE + show_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)
            try:
                page.screenshot(path=str(out_dir / "show.png"))
            except Exception:
                pass
            show_text = page.locator("body").inner_text(timeout=10000)
            reason = ""
            m = re.search(r"علت رد نمونه کار شما:(.*?)(ویرایش نمونه‌کار|حذف کامل)",
                          show_text, re.S)
            if m:
                reason = " ".join(m.group(1).split())
            images = page.evaluate(
                """() => [...document.querySelectorAll('img')].map(i => ({
                    src: i.getAttribute('src') || '', alt: (i.getAttribute('alt') || '').slice(0, 80),
                    w: i.naturalWidth || 0, h: i.naturalHeight || 0})).filter(x => x.src)""")
            result["show"] = {
                "title": page.title(),
                "rejection_reason": reason[:1500],
                "description_on_page": "",
                "images": images,
            }
            # The description sits under the sample title on the show page.
            dm = re.search(re.escape(expected_title) + r"\s*\n\s*\n(.+?)\n",
                           show_text)
            if dm:
                result["show"]["description_on_page"] = dm.group(1).strip()[:2000]
            print(f"show reason_len={len(reason)} images={len(images)}")

            # 4. Update-new form dump (read-only).
            page.goto(SITE + edit_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2500)
            try:
                page.screenshot(path=str(out_dir / "edit-form.png"))
            except Exception:
                pass
            result["edit_final_url"] = page.url
            result["edit_title"] = page.title()
            forms = dump_forms(page)
            result["forms"] = forms
            body_text = page.locator("body").inner_text(timeout=10000)
            result["edit_body_text"] = body_text[:2000]
            n_fields = sum(len(f.get("fields", [])) for f in forms)
            print(f"edit url={page.url} title={page.title()!r} forms={len(forms)} fields={n_fields}")

            # Upload-widget dump (read-only): #file-input is OUTSIDE any
            # form on the create page; confirm it exists here too, plus the
            # drop area state and the current cropProps (old image ref).
            try:
                widget = page.evaluate(
                    """() => {
                      const fi = document.querySelector('#file-input');
                      const da = document.querySelector('#upload-drop-area');
                      const cp = document.querySelector('#app_bundle_portfolio_cropProps');
                      const imgs = [...document.querySelectorAll('#upload-drop-area img')].map(i => ({
                        src: i.getAttribute('src') || '', cls: (i.getAttribute('class') || '').slice(0, 60)}));
                      const sel = [...document.querySelectorAll('#upload-drop-area [class*=select], #upload-drop-area button, #upload-drop-area a')].map(e => ({
                        tag: e.tagName.toLowerCase(), text: (e.innerText || '').trim().slice(0, 60),
                        cls: (e.getAttribute('class') || '').slice(0, 80)}));
                      return {
                        file_input: fi ? {in_form: !!(fi.form), accept: fi.getAttribute('accept') || '',
                                          visible: !!(fi.offsetParent !== null)} : null,
                        drop_area_html: da ? da.outerHTML.slice(0, 4000) : '',
                        drop_area_text: da ? (da.innerText || '').slice(0, 500) : '',
                        crop_props: cp ? (cp.value || '') : '',
                        widget_imgs: imgs, widget_controls: sel};
                    }""")
            except Exception as e:
                widget = {"error": f"{type(e).__name__}: {e}"}
            result["upload_widget"] = widget
            old_crop = widget.get("crop_props", "") if isinstance(widget, dict) else ""
            old_show_images = [i.get("src", "") for i in
                               result.get("show", {}).get("images", [])
                               if "/portfolio/" in i.get("src", "")]
            print(f"widget file_input={widget.get('file_input')} "
                  f"crop={str(old_crop)[:120]!r} widget_imgs={len(widget.get('widget_imgs', []))}")

            # Title sanity check: the form must be for the expected sample.
            form_text = json.dumps(forms, ensure_ascii=False)
            title_ok = expected_title in form_text or expected_title in body_text
            result["title_matches_expected"] = title_ok
            if not title_ok:
                print("RESULT title_mismatch (not the expected sample)")
                return 1

            with open(out_dir / "inspect.json", "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print("RESULT inspect_ok")
            if mode == "inspect":
                return 0

            # 5. IMAGE mode: attach the new file, verify cropProps changes
            # to a NEW upload, keep title/description/skills UNTOUCHED.
            if mode == "image":
                result["image"] = {"asset": image_file,
                                   "crop_before": old_crop,
                                   "old_show_images": old_show_images}
                try:
                    fi_count = page.locator("#file-input").count()
                except Exception:
                    fi_count = 0
                result["image"]["file_input_count"] = fi_count
                print(f"image file_input_count={fi_count}")
                if fi_count == 0:
                    print("RESULT file_input_not_found")
                    with open(out_dir / "edit.json", "w", encoding="utf-8") as f:
                        json.dump(result, f, ensure_ascii=False, indent=2)
                    return 1
                try:
                    page.set_input_files("#file-input", image_file, timeout=30000)
                    result["image"]["attached"] = True
                except Exception as e:
                    result["image"]["attached"] = False
                    result["image"]["attach_error"] = f"{type(e).__name__}: {e}"
                    print(f"RESULT attach_failed ({type(e).__name__})")
                    with open(out_dir / "edit.json", "w", encoding="utf-8") as f:
                        json.dump(result, f, ensure_ascii=False, indent=2)
                    return 1
                new_crop = ""
                import time as _t
                _start = _t.time()
                while _t.time() - _start < 90:
                    try:
                        new_crop = page.evaluate(
                            "() => { const cp = document.querySelector('#app_bundle_portfolio_cropProps');"
                            " return cp ? (cp.value || '') : ''; }")
                    except Exception:
                        new_crop = ""
                    if new_crop and new_crop != old_crop:
                        break
                    page.wait_for_timeout(2000)
                result["image"]["crop_after"] = new_crop
                result["image"]["crop_changed"] = bool(new_crop and new_crop != old_crop)
                print(f"image crop_changed={result['image']['crop_changed']} "
                      f"crop_after={str(new_crop)[:120]!r}")
                try:
                    widget_after = page.evaluate(
                        """() => {
                          const da = document.querySelector('#upload-drop-area');
                          return {
                            drop_area_html: da ? da.outerHTML.slice(0, 4000) : '',
                            widget_imgs: [...document.querySelectorAll('#upload-drop-area img')].map(i => i.getAttribute('src') || '')};
                        }""")
                except Exception as e:
                    widget_after = {"error": f"{type(e).__name__}: {e}"}
                result["image"]["widget_after"] = widget_after
                try:
                    page.screenshot(path=str(out_dir / "after-attach.png"))
                except Exception:
                    pass
                if not result["image"]["crop_changed"]:
                    print("RESULT image_upload_not_registered")
                    with open(out_dir / "edit.json", "w", encoding="utf-8") as f:
                        json.dump(result, f, ensure_ascii=False, indent=2)
                    return 1
                # Submit once (same publish button preference as edit mode).
                clicked = ""
                for sel, how in (("#app_bundle_portfolio_publish", "id-publish"),
                                 ("button:has-text('ارسال نمونه‌کار')", "text-submit"),
                                 ("button:has-text('ارسال')", "text-send"),
                                 ("button:has-text('ویرایش')", "text-edit"),
                                 ("button:has-text('ذخیره')", "text-save"),
                                 ("input[type=submit]", "input-submit")):
                    try:
                        loc = page.locator(sel).first
                        if loc.count() > 0 and loc.is_visible():
                            loc.click(timeout=10000)
                            clicked = how + " " + sel
                            break
                    except Exception as e:
                        result["image"].setdefault("click_errors", []).append(
                            f"{how}: {type(e).__name__}")
                result["image"]["submit_clicked"] = clicked
                result["image"]["dialogs"] = dialogs
                print(f"image submit clicked={clicked!r}")
                if not clicked:
                    print("RESULT submit_button_not_found")
                    with open(out_dir / "edit.json", "w", encoding="utf-8") as f:
                        json.dump(result, f, ensure_ascii=False, indent=2)
                    return 1
                page.wait_for_timeout(5000)
                try:
                    page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    pass
                try:
                    page.screenshot(path=str(out_dir / "after.png"))
                except Exception:
                    pass
                after_text = ""
                try:
                    after_text = page.locator("body").inner_text(timeout=10000)
                except Exception:
                    pass
                result["image"]["after_url"] = page.url
                result["image"]["after_text"] = after_text[:3000]
                result["image"]["markers"] = {
                    "under_review": "در حال بررسی" in after_text,
                    "success_word": "با موفقیت" in after_text,
                    "error_word": ("خطا" in after_text or "نامناسب" in after_text
                                   or "مجاز نیست" in after_text),
                }
                print(f"image after url={page.url} markers={result['image']['markers']}")
                # Re-read the portfolio list row + the show page images.
                try:
                    page.goto(SITE + "/resume/portfolio/",
                              wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(3000)
                    list_text = page.locator("body").inner_text(timeout=10000)
                    row = ""
                    for line in list_text.splitlines():
                        if expected_title in line:
                            row = line.strip()
                    result["image"]["list_row"] = row[:300]
                    result["image"]["list_shows_under_review"] = (
                        "در حال بررسی" in list_text)
                except Exception as e:
                    result["image"]["list_error"] = f"{type(e).__name__}: {e}"
                try:
                    page.goto(SITE + show_url, wait_until="domcontentloaded",
                              timeout=60000)
                    page.wait_for_timeout(2000)
                    new_images = page.evaluate(
                        """() => [...document.querySelectorAll('img')].map(i => i.getAttribute('src') || '').filter(s => s.includes('/portfolio/'))""")
                    result["image"]["show_images_after"] = sorted(set(new_images))
                    result["image"]["image_changed"] = any(
                        u not in old_show_images
                        for u in result["image"]["show_images_after"])
                    print(f"image show_images_after={result['image']['show_images_after']} "
                          f"changed={result['image']['image_changed']}")
                except Exception as e:
                    result["image"]["show_error"] = f"{type(e).__name__}: {e}"
                with open(out_dir / "edit.json", "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
                print("RESULT image_submitted")
                return 0

            # 5. EDIT mode: fill ONLY the description.
            desc_sel = None
            for sel in ("#app_bundle_portfolio_description",
                        "textarea[name='app_bundle_portfolio[description]']",
                        "[name='app_bundle_portfolio[description]']"):
                try:
                    if page.locator(sel).count() > 0:
                        desc_sel = sel
                        break
                except Exception:
                    pass
            if not desc_sel:
                print("RESULT description_field_not_found")
                return 1
            before = page.locator(desc_sel).input_value()
            result["edit"] = {"description_field": desc_sel,
                              "description_before": before[:2000]}
            page.locator(desc_sel).fill(new_desc)
            page.wait_for_timeout(1000)
            after = page.locator(desc_sel).input_value()
            result["edit"]["description_after"] = after[:2000]
            result["edit"]["description_set"] = (after.strip() == new_desc.strip())
            print(f"description set={result['edit']['description_set']} "
                  f"len_before={len(before)} len_after={len(after)}")
            if not result["edit"]["description_set"]:
                print("RESULT description_fill_failed")
                return 1

            # 6. Submit once. Prefer the publish button id, else a button
            # whose text is a submit/resend wording.
            clicked = ""
            for sel, how in (("#app_bundle_portfolio_publish", "id-publish"),
                             ("button:has-text('ارسال نمونه‌کار')", "text-submit"),
                             ("button:has-text('ارسال')", "text-send"),
                             ("button:has-text('ویرایش')", "text-edit"),
                             ("button:has-text('ذخیره')", "text-save"),
                             ("input[type=submit]", "input-submit")):
                try:
                    loc = page.locator(sel).first
                    if loc.count() > 0 and loc.is_visible():
                        loc.click(timeout=10000)
                        clicked = how + " " + sel
                        break
                except Exception as e:
                    result["edit"].setdefault("click_errors", []).append(
                        f"{how}: {type(e).__name__}")
            result["edit"]["submit_clicked"] = clicked
            result["edit"]["dialogs"] = dialogs
            print(f"submit clicked={clicked!r}")
            if not clicked:
                print("RESULT submit_button_not_found")
                return 1
            page.wait_for_timeout(5000)
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            try:
                page.screenshot(path=str(out_dir / "after.png"))
            except Exception:
                pass
            after_text = ""
            try:
                after_text = page.locator("body").inner_text(timeout=10000)
            except Exception:
                pass
            result["edit"]["after_url"] = page.url
            result["edit"]["after_text"] = after_text[:3000]
            markers = {
                "under_review": "در حال بررسی" in after_text,
                "success_word": "با موفقیت" in after_text,
                "error_word": ("خطا" in after_text or "نامناسب" in after_text
                               or "مجاز نیست" in after_text),
            }
            result["edit"]["markers"] = markers
            print(f"after url={page.url} markers={markers}")

            # 7. Re-read the portfolio list row for this sample.
            try:
                page.goto(SITE + "/resume/portfolio/",
                          wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                list_text = page.locator("body").inner_text(timeout=10000)
                row = ""
                for line in list_text.splitlines():
                    if expected_title in line:
                        row = line.strip()
                result["edit"]["list_row"] = row[:300]
                result["edit"]["list_shows_under_review"] = (
                    "در حال بررسی" in list_text)
                print(f"list row={row[:120]!r}")
            except Exception as e:
                result["edit"]["list_error"] = f"{type(e).__name__}: {e}"

            with open(out_dir / "edit.json", "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print("RESULT edit_submitted")
            return 0
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
