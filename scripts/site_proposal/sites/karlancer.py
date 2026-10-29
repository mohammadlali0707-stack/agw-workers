"""Karlancer Playwright DOM Adapter.

Handles:
- Detecting login redirection / session expiration (status: login_required)
- Detecting captchas (status: captcha_detected)
- Navigating to project page
- Opening bid modal / form if collapsed
- Filling proposal text, bid amount, delivery days
- preview mode: leaves form filled without submitting (status: preview_ready)
- submit mode: clicks final submit button (status: submitted)
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CAPTCHA_SELECTORS: List[str] = [
    "iframe[src*='recaptcha']",
    "iframe[src*='turnstile']",
    "iframe[src*='challenges.cloudflare']",
    "iframe[src*='hcaptcha']",
    "iframe[src*='arcaptcha']",
    ".g-recaptcha",
    ".cf-turnstile",
    ".h-captcha",
    ".arcaptcha",
    "[data-arcaptcha]",
    "#recaptcha",
    "#turnstile",
    "#arcaptcha",
    "#hcaptcha",
    "#challenge-stage",
    "div[id*='captcha']",
    "div[class*='captcha']",
]

CAPTCHA_TEXT_SELECTORS: List[str] = [
    ":has-text('من ربات نیستم')",
    ":has-text('تایید هویت انسان')",
    ":has-text('کد امنیتی را وارد کنید')",
    ":has-text('Verify you are human')",
    ":has-text('I am human')",
    ":has-text('Just a moment...')",
    ":has-text('Attention Required! | Cloudflare')",
]

LOGIN_URL_PATTERNS: List[str] = [
    "/login",
    "/auth",
    "/signin",
    "account/login",
    "karlancer.com/login",
]

LOGIN_TEXT_PATTERNS: List[str] = [
    "برای ثبت پیشنهاد ابتدا وارد شوید",
    "برای ثبت پیشنهاد وارد شوید",
    "برای ارسال پیشنهاد وارد شوید",
    "جهت ارسال پیشنهاد باید وارد شوید",
    "ابتدا وارد حساب کاربری خود شوید",
    "ابتدا وارد شوید یا ثبت نام کنید",
]

TEXT_SELECTORS: List[str] = [
    "textarea[name='description']",
    "textarea[name='proposal']",
    "textarea[name='bid_description']",
    "textarea[name='comment']",
    "textarea[placeholder*='توضیحات']",
    "textarea[placeholder*='متن پیشنهاد']",
    "textarea[placeholder*='پیشنهاد']",
    "textarea#description",
    "textarea#proposal",
    "textarea",
]

AMOUNT_SELECTORS: List[str] = [
    "input[name='amount']",
    "input[name='price']",
    "input[name='bid_amount']",
    "input[name='proposed_price']",
    "input[placeholder*='مبلغ پیشنهادی']",
    "input[placeholder*='مبلغ']",
    "input[placeholder*='قیمت']",
    "input#amount",
    "input#price",
]

DAYS_SELECTORS: List[str] = [
    "input[name='days']",
    "input[name='delivery_days']",
    "input[name='duration']",
    "input[name='delivery_time']",
    "input[placeholder*='مدت زمان']",
    "input[placeholder*='تعداد روز']",
    "input[placeholder*='زمان تحویل']",
    "input[placeholder*='روز']",
    "input#days",
    "input#duration",
]

EXPAND_BUTTON_SELECTORS: List[str] = [
    "button:has-text('ثبت پیشنهاد روی پروژه')",
    "button:has-text('ثبت پیشنهاد')",
    "button:has-text('ارسال پیشنهاد')",
    "a:has-text('ثبت پیشنهاد روی پروژه')",
    "a:has-text('ثبت پیشنهاد')",
    "a:has-text('ارسال پیشنهاد')",
]

SUBMIT_BUTTON_SELECTORS: List[str] = [
    "button[type='submit']",
    "input[type='submit']",
    "button:has-text('ثبت پیشنهاد')",
    "button:has-text('ارسال پیشنهاد')",
    "button:has-text('ثبت نهایی')",
    "button:has-text('تایید و ثبت')",
    "button.submit-bid-btn",
]


async def _first_matching_locator(page: Any, selectors: List[str]) -> Optional[Any]:
    """Find the first matching visible (or present) locator from candidate selectors."""
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                first = loc.first
                try:
                    if await first.is_visible():
                        return first
                except Exception:
                    pass
        except Exception:
            continue

    # Fallback to any present locator if visibility check didn't match
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                return loc.first
        except Exception:
            continue
    return None


async def _detect_captcha(page: Any) -> bool:
    """Check if any captcha widget or verification challenge is present on the page."""
    for sel in CAPTCHA_SELECTORS:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                return True
        except Exception:
            pass

    for text_sel in CAPTCHA_TEXT_SELECTORS:
        try:
            loc = page.locator(text_sel)
            if await loc.count() > 0:
                return True
        except Exception:
            pass

    return False


async def _detect_login_required(page: Any) -> bool:
    """Check if the user is redirected to login or if login prompt is displayed."""
    # 1. URL check
    current_url = getattr(page, "url", "") or ""
    current_url_lower = current_url.lower()
    if any(pat in current_url_lower for pat in LOGIN_URL_PATTERNS):
        return True

    # 2. Password input present (login form)
    try:
        pw_loc = page.locator("input[type='password']")
        if await pw_loc.count() > 0:
            return True
    except Exception:
        pass

    # 3. Explicit login text prompt
    try:
        for pat in LOGIN_TEXT_PATTERNS:
            loc = page.locator(f":has-text('{pat}')")
            if await loc.count() > 0:
                return True
    except Exception:
        pass

    return False


_FA = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _num(s):
    import re
    m = re.search(r"\d[\d,]*", (s or "").translate(_FA))
    return int(m.group(0).replace(",", "")) if m else None


async def _body(page):
    try:
        return await page.evaluate("() => document.body.innerText")
    except Exception:
        return ""


async def _click_text(page, sel, txt):
    """Click first visible element matching sel whose trimmed text == txt."""
    return await page.evaluate(
        """([sel, txt]) => { const e = [...document.querySelectorAll(sel)].find(e => (e.innerText||'').trim() === txt && e.getBoundingClientRect().width > 0);
             if (e) { e.scrollIntoView({block:'center'}); e.click(); return true } return false }""", [sel, txt])


async def _errors(page):
    try:
        return await page.evaluate("""() => [...document.querySelectorAll('.text-danger,.error,.invalid-feedback,[class*=error],[class*=toast],[class*=alert],[class*=snack]')]
            .filter(e => e.offsetParent !== null).map(e => (e.innerText||'').trim()).filter(t => t && t.length < 300).slice(0, 8)""")
    except Exception:
        return []


class KarlancerAdapter:
    """Karlancer flow (rewritten 2026-10-29 from live form):
    project page -> div 'ثبت پیشنهاد روی پروژه' -> tips modal 'ادامه و ثبت پیشنهاد' ->
    form: input[placeholder='تعداد روز'], input[placeholder='قیمت'] (toman), textarea, button#submitBid.
    Each bid costs tokens (free plan: 10 tokens / period). Checks balance first."""

    @classmethod
    async def run_site_flow(cls, page: Any, context: Any, proposal_data: Dict[str, Any], mode: str = "preview") -> Dict[str, Any]:
        import re
        fv: Dict[str, Any] = {}
        url = proposal_data.get("project_url")
        if not url:
            return {"status": "form_error", "filled_values": {}, "error_message": "project_url is missing"}
        submit = str(mode).strip().lower() == "submit"
        try:
            # 0. token balance from dashboard
            await page.goto("https://www.karlancer.com/panel/dashboard", wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)
            b = await _body(page)
            if "خروج" not in b:
                return {"status": "login_required", "filled_values": {}, "error_message": "Karlancer session expired (no logout link on dashboard)"}
            m = re.search(r"توکن‌های شما\s*\n\s*([۰-۹0-9]+)", b)
            fv["tokens_left"] = _num(m.group(1)) if m else None
            # 1. project page
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)
            if await _detect_captcha(page):
                return {"status": "captcha_detected", "filled_values": fv, "error_message": "captcha on page"}
            b = await _body(page)
            fv["url"] = page.url
            if re.search(r"پیشنهاد شما ثبت شده|ویرایش پیشنهاد|شما (قبلا|قبلاً) (برای این پروژه )?پیشنهاد", b):
                return {"status": "already_bid", "filled_values": fv, "error_message": "already bid on this project"}
            if re.search(r"پروژه (بسته شده|منقضی|به اتمام رسیده)|مهلت ارسال پیشنهاد (تمام|به پایان)", b):
                return {"status": "closed", "filled_values": fv, "error_message": "project closed"}
            if not await _click_text(page, "div.pointer, button, a", "ثبت پیشنهاد روی پروژه"):
                return {"status": "form_error", "filled_values": fv, "error_message": "bid button not found", "body": b[:1500]}
            await page.wait_for_timeout(2500)
            await _click_text(page, "button", "ادامه و ثبت پیشنهاد")
            await page.wait_for_timeout(3000)
            b = await _body(page)
            m = re.search(r"ثبت پیشنهاد روی این پروژه:\s*([۰-۹0-9]+)\s*توکن", b)
            fv["token_cost"] = _num(m.group(1)) if m else None
            m = re.search(r"عنوان پروژه:\s*(.+)", b)
            fv["title"] = m.group(1).strip()[:120] if m else ""
            if fv.get("token_cost") and fv.get("tokens_left") is not None and fv["token_cost"] > fv["tokens_left"]:
                return {"status": "no_tokens", "filled_values": fv, "error_message": f"need {fv['token_cost']} tokens, have {fv['tokens_left']}"}
            days = page.locator("input[placeholder='تعداد روز']").first
            price = page.locator("input[placeholder='قیمت']").first
            text = page.locator("textarea[placeholder*='روند انجام پروژه']").first
            if not await text.count() or not await price.count():
                return {"status": "form_error", "filled_values": fv, "error_message": "bid form fields not found", "body": b[:1500]}
            if proposal_data.get("delivery_days") is not None:
                await days.click(); await days.fill(""); await days.press_sequentially(str(int(proposal_data["delivery_days"])), delay=40)
                fv["delivery_days"] = await days.input_value()
            if proposal_data.get("bid_amount") is not None:
                amt = int(round(int(proposal_data["bid_amount"]) / 1000.0) * 1000)
                await price.click(); await price.fill(""); await price.press_sequentially(str(amt), delay=30)
                fv["bid_amount"] = await price.input_value()
            await text.click(); await text.fill(str(proposal_data.get("proposal_text", "")))
            fv["proposal_len"] = len(await text.input_value())
            await page.wait_for_timeout(800)
            if not submit:
                fv["errors"] = await _errors(page)
                return {"status": "preview_ready", "filled_values": fv, "error_message": None}
            btn = page.locator("button#submitBid").first
            if not await btn.count():
                return {"status": "form_error", "filled_values": fv, "error_message": "submitBid button not found"}
            await btn.scroll_into_view_if_needed(); await btn.click()
            await page.wait_for_timeout(6000)
            fv["after_errors"] = await _errors(page)
            still = await page.locator("button#submitBid").count() and await page.locator("button#submitBid").first.is_visible()
            # 2. confirm via my bids page
            await page.goto("https://www.karlancer.com/panel/bids", wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)
            bb = await _body(page)
            t = (fv.get("title") or "")[:25]
            if t and t in bb:
                return {"status": "submitted", "filled_values": fv, "error_message": None}
            return {"status": "form_error", "filled_values": fv,
                    "error_message": f"not confirmed in /panel/bids (form still open={bool(still)}); errors={fv['after_errors']}", "body": bb[:1200]}
        except Exception as exc:
            logger.exception("Unexpected error in Karlancer flow")
            return {"status": "form_error", "filled_values": fv, "error_message": f"{type(exc).__name__}: {exc}"[:400]}

    async def __call__(self, page: Any, context: Any, proposal_data: Dict[str, Any], mode: str = "preview") -> Dict[str, Any]:
        return await self.run_site_flow(page, context, proposal_data, mode)


run_site_flow = KarlancerAdapter.run_site_flow
