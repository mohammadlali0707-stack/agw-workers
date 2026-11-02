"""Freelancer.com adapter (owner 2026-11-02).

Runs ONLY on GitHub Actions (never on the VPS). Flow:
  1. Sign in: if the stored Freelancer session is missing/expired, log in with Google using the shared
     site-login account "momona" (tools/google_login.py, global lock, cookies never printed).
  2. Open the project page, fill the bid form (amount, delivery days, proposal text).
  3. preview -> leave filled, never click "Place Bid" (status preview_ready).
     submit  -> click "Place Bid" (status submitted). The runner enforces the daily limit.
Unknown page shapes return form_error with a short dump of visible fields so the selectors can be tuned.
"""
import os
import re
import sys
from typing import Any, Dict, List

FL_HOME = "https://www.freelancer.com"
GOOGLE_ACCOUNT = "momona"

AMOUNT_SEL: List[str] = ["#bidAmountInput", "input[name='bidAmount']", "input[formcontrolname='amount']",
                         "input[aria-label*='Bid amount' i]", "input[placeholder*='amount' i]"]
PERIOD_SEL: List[str] = ["#periodInput", "input[name='period']", "input[formcontrolname='period']",
                         "input[aria-label*='delivered' i]", "input[aria-label*='period' i]"]
TEXT_SEL: List[str] = ["#descriptionTextArea", "textarea[name='description']", "textarea[formcontrolname='description']",
                       "textarea[placeholder*='proposal' i]", "textarea"]
CAPTCHA_SEL = ["iframe[src*='recaptcha']", "iframe[src*='hcaptcha']", "iframe[src*='challenges.cloudflare']", "#challenge-stage"]


async def _first_visible(page, sels):
    for s in sels:
        try:
            loc = page.locator(s)
            n = await loc.count()
            for i in range(min(n, 5)):
                if await loc.nth(i).is_visible():
                    return loc.nth(i)
        except Exception:
            pass
    return None


async def _body(page):
    try:
        return await page.evaluate("() => document.body.innerText")
    except Exception:
        return ""


async def _fields(page):
    try:
        return await page.evaluate("""() => [...document.querySelectorAll('input,textarea,button,select')]
          .filter(e => { const r = e.getBoundingClientRect(); return r.width > 1 && r.height > 1 })
          .map(e => ({tag: e.tagName.toLowerCase(), type: e.type || '', id: e.id || '', name: e.name || '',
                      ph: e.placeholder || '', aria: e.getAttribute('aria-label') || '',
                      text: (e.innerText || '').trim().slice(0, 40)})).slice(0, 60)""")
    except Exception:
        return []


async def _errors(page):
    try:
        return await page.evaluate("""() => [...document.querySelectorAll('[class*=error],[class*=Error],[role=alert],fl-banner-alert')]
          .filter(e => e.offsetParent !== null).map(e => (e.innerText || '').trim()).filter(t => t && t.length < 300).slice(0, 8)""")
    except Exception:
        return []


async def _is_logged_in(page) -> bool:
    try:
        await page.goto(FL_HOME + "/dashboard", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)
    except Exception:
        return False
    u = (page.url or "").lower()
    return "/login" not in u and "/signup" not in u and "/dashboard" in u


async def _google_login(page, context) -> str:
    """Log in to Freelancer with the momona Google account. Returns '' on success, else a short reason."""
    sys.path.insert(0, os.path.join(os.getcwd(), "tools"))
    import google_login  # shared helper with the global account lock
    with google_login.session("site-proposal-freelancer", account=GOOGLE_ACCOUNT) as gl:
        await context.add_cookies(gl.cookies())
        await page.goto(FL_HOME + "/login", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)
        popup = None
        btn = await _first_visible(page, ["button:has-text('Google')", "a:has-text('Google')", "[aria-label*='Google' i]"])
        try:
            if btn:
                async with context.expect_page(timeout=20000) as pinfo:
                    await btn.click()
                popup = await pinfo.value
            else:
                fr = page.frame_locator("iframe[src*='accounts.google.com/gsi']").first
                async with context.expect_page(timeout=20000) as pinfo:
                    await fr.locator("div[role=button]").first.click()
                popup = await pinfo.value
        except Exception as e:
            if "accounts.google.com" not in (page.url or ""):
                return f"Google button / popup not found ({type(e).__name__})"
            popup = page   # same-tab redirect
        await popup.wait_for_load_state("domcontentloaded")
        await popup.wait_for_timeout(3000)
        for _ in range(4):   # account chooser -> consent "Continue" (each may or may not appear)
            if popup.is_closed():
                break
            if await popup.locator("input[type='password']").count():
                return "Google asked for the password: the momona Google session expired (renew GOOGLE_SITE_COOKIES_MOMONA)"
            hit = await _first_visible(popup, [f"[data-email='{gl.account}']", f"div:has-text('{gl.account}') >> nth=-1",
                                               "button:has-text('Continue')", "button:has-text('Allow')"])
            if not hit:
                break
            try:
                await hit.click(timeout=8000)
            except Exception:
                pass
            try:
                await popup.wait_for_timeout(3500)
            except Exception:
                break
        await page.wait_for_timeout(6000)
        if not await _is_logged_in(page):
            b = (await _body(page))[:300]
            if "sign up" in b.lower() or "/signup" in (page.url or "").lower():
                return "Google sign-in reached Freelancer sign-up: the momona account has no Freelancer profile"
            return "Freelancer login with Google did not finish"
    return ""


class FreelancerAdapter:
    @classmethod
    async def run_site_flow(cls, page: Any, context: Any, proposal_data: Dict[str, Any], mode: str = "preview") -> Dict[str, Any]:
        url = (proposal_data.get("project_url") or "").strip()
        fv: Dict[str, Any] = {}
        if "freelancer.com/projects/" not in url:
            return {"status": "form_error", "filled_values": fv, "error_message": "not a freelancer.com project URL"}
        try:
            if not await _is_logged_in(page):
                why = await _google_login(page, context)
                if why:
                    return {"status": "login_required", "filled_values": fv, "error_message": why}
            fv["logged_in"] = True
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(6000)
            for s in CAPTCHA_SEL:
                if await page.locator(s).count():
                    return {"status": "captcha_detected", "filled_values": fv, "error_message": "captcha on project page"}
            body = await _body(page)
            low = body.lower()
            if "this project is no longer accepting bids" in low or "project closed" in low:
                return {"status": "closed", "filled_values": fv, "error_message": "project closed"}
            if "you have already bid" in low or "edit bid" in low or "retract bid" in low:
                return {"status": "already_bid", "filled_values": fv, "error_message": "already bid on this project"}
            m = re.search(r"(\d+)\s+bids? left", low)
            if m:
                fv["bids_left"] = int(m.group(1))
            amount = await _first_visible(page, AMOUNT_SEL)
            period = await _first_visible(page, PERIOD_SEL)
            text = await _first_visible(page, TEXT_SEL)
            if not (amount and period and text):
                return {"status": "form_error", "filled_values": {**fv, "fields": await _fields(page)},
                        "error_message": "bid form fields not found", "body": body[:1500]}
            bid_amount = proposal_data.get("bid_amount")
            days = proposal_data.get("delivery_days") or 7
            msg = (proposal_data.get("proposal_text") or "").strip()
            if not msg:
                return {"status": "form_error", "filled_values": fv, "error_message": "proposal_text is empty"}
            if bid_amount:
                await amount.fill(str(bid_amount))
            await period.fill(str(int(days)))
            await text.fill(msg)
            fv.update({"bid_amount": await amount.input_value(), "delivery_days": await period.input_value(),
                       "proposal_text": msg[:200]})
            await page.wait_for_timeout(1500)
            if mode != "submit":
                return {"status": "preview_ready", "filled_values": fv, "error_message": None}
            btn = await _first_visible(page, ["button:has-text('Place Bid')", "button:has-text('Place bid')"])
            if not btn:
                return {"status": "form_error", "filled_values": fv, "error_message": "Place Bid button not found"}
            await btn.click()
            await page.wait_for_timeout(7000)
            errs = await _errors(page)
            low = (await _body(page)).lower()
            if "your bid has been placed" in low or "edit bid" in low or "retract bid" in low or "bid placed" in low:
                return {"status": "submitted", "filled_values": fv, "error_message": None}
            return {"status": "form_error", "filled_values": {**fv, "errors": errs},
                    "error_message": "; ".join(errs)[:300] or "no confirmation after Place Bid"}
        except Exception as exc:
            return {"status": "form_error", "filled_values": fv, "error_message": f"{type(exc).__name__}: {str(exc)[:300]}"}

    async def __call__(self, page, context, proposal_data, mode="preview"):
        return await self.run_site_flow(page, context, proposal_data, mode)


async def run_site_flow(page, context, proposal_data, mode):
    return await FreelancerAdapter().run_site_flow(page, context, proposal_data, mode)
