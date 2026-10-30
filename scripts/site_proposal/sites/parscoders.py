"""Parscoders Playwright DOM Adapter.

Handles:
- Detecting login redirection / session expiration (status: login_required)
- Detecting captchas (status: captcha_detected)
- Navigating to project page
- Filling proposal text, bid amount, delivery days
- preview mode: leaves form filled without submitting (status: preview_ready)
- submit mode: clicks final submit button (status: submitted)

Site: https://parscoders.com
Bid flow: project page -> "ثبت پیشنهاد" button -> form modal/page ->
          fill textarea (توضیحات), input (مبلغ/قیمت تومان), input (مدت/روز) ->
          submit button
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
]

LOGIN_URL_PATTERNS: List[str] = [
    "/login",
    "/auth",
    "/signin",
    "parscoders.com/login",
    "parscoders.com/auth",
]

LOGIN_TEXT_PATTERNS: List[str] = [
    "برای ثبت پیشنهاد وارد شوید",
    "برای ارسال پیشنهاد وارد شوید",
    "جهت ارسال پیشنهاد باید وارد شوید",
    "ابتدا وارد حساب کاربری خود شوید",
    "ابتدا وارد شوید",
    "وارد شوید یا ثبت نام کنید",
    "ورود به حساب کاربری",
    "ایمیل یا رمز عبور",
]

TEXT_SELECTORS: List[str] = [
    "textarea[name='description']",
    "textarea[name='proposal']",
    "textarea[name='proposal_text']",
    "textarea[name='bid_description']",
    "textarea[name='comment']",
    "textarea[placeholder*='توضیحات']",
    "textarea[placeholder*='متن پیشنهاد']",
    "textarea[placeholder*='پیشنهاد']",
    "textarea[placeholder*='پیام']",
    "textarea[placeholder*='شرح']",
    "textarea#description",
    "textarea#proposal",
    "textarea",
]

AMOUNT_SELECTORS: List[str] = [
    "input[name='amount']",
    "input[name='price']",
    "input[name='bid_amount']",
    "input[name='cost']",
    "input[name='budget']",
    "input[placeholder*='مبلغ پیشنهادی']",
    "input[placeholder*='مبلغ']",
    "input[placeholder*='قیمت']",
    "input[placeholder*='هزینه']",
    "input[placeholder*='تومان']",
    "input#amount",
    "input#price",
    "input#bid_amount",
]

DAYS_SELECTORS: List[str] = [
    "input[name='delivery_days']",
    "input[name='days']",
    "input[name='duration']",
    "input[name='delivery_time']",
    "input[placeholder*='مدت زمان']",
    "input[placeholder*='تعداد روز']",
    "input[placeholder*='زمان تحویل']",
    "input[placeholder*='روز']",
    "input[placeholder*='مدت']",
    "input#delivery_days",
    "input#days",
    "input#duration",
]

EXPAND_BUTTON_SELECTORS: List[str] = [
    "button:has-text('ثبت پیشنهاد')",
    "button:has-text('ارسال پیشنهاد')",
    "button:has-text('پیشنهاد دهید')",
    "button:has-text('ارسال')",
    "a:has-text('ثبت پیشنهاد')",
    "a:has-text('ارسال پیشنهاد')",
    "a.btn:has-text('پیشنهاد')",
    "[class*='proposal']:has-text('ثبت')",
    "[class*='bid']:has-text('ثبت')",
]

SUBMIT_BUTTON_SELECTORS: List[str] = [
    "button[type='submit']",
    "input[type='submit']",
    "button:has-text('ثبت پیشنهاد')",
    "button:has-text('ارسال پیشنهاد')",
    "button:has-text('ارسال نهایی')",
    "button:has-text('ثبت نهایی')",
    "button:has-text('تایید و ثبت')",
    "button:has-text('تایید')",
    "button.submit-btn",
    "button.btn-submit",
    "button[class*='submit']",
]


async def _first_matching_locator(page: Any, selectors: List[str]) -> Optional[Any]:
    """Find the first visible locator from candidate selectors."""
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
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                return loc.first
        except Exception:
            continue
    return None


async def _detect_captcha(page: Any) -> bool:
    for sel in CAPTCHA_SELECTORS:
        try:
            if await page.locator(sel).count() > 0:
                return True
        except Exception:
            pass
    for text_sel in CAPTCHA_TEXT_SELECTORS:
        try:
            if await page.locator(text_sel).count() > 0:
                return True
        except Exception:
            pass
    return False


async def _detect_login_required(page: Any) -> bool:
    current_url = getattr(page, "url", "") or ""
    if any(pat in current_url.lower() for pat in LOGIN_URL_PATTERNS):
        return True
    try:
        if await page.locator("input[type='password']").count() > 0:
            return True
    except Exception:
        pass
    try:
        body = await page.evaluate("() => document.body.innerText")
        for pat in LOGIN_TEXT_PATTERNS:
            if pat in body:
                return True
    except Exception:
        pass
    return False


async def _body(page: Any) -> str:
    try:
        return await page.evaluate("() => document.body.innerText")
    except Exception:
        return ""


async def _errors(page: Any) -> List[str]:
    try:
        return await page.evaluate("""() =>
            [...document.querySelectorAll('.text-danger,.error,.invalid-feedback,[class*=error],[class*=toast],[class*=alert],[class*=snack]')]
            .filter(e => e.offsetParent !== null)
            .map(e => (e.innerText||'').trim())
            .filter(t => t && t.length < 300)
            .slice(0, 8)""")
    except Exception:
        return []


_FA = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _num(s: str) -> Optional[int]:
    import re
    m = re.search(r"\d[\d,]*", (s or "").translate(_FA))
    return int(m.group(0).replace(",", "")) if m else None


class ParscodersAdapter:
    """Parscoders bid flow:
    project page -> 'ثبت پیشنهاد' button -> fill form (text, amount, days) -> submit.
    Uses SITE_SESSION_PARSCODERS for auth. Daily limit: 5 bids (shared runner limit).
    """

    @classmethod
    async def run_site_flow(
        cls,
        page: Any,
        context: Any,
        proposal_data: Dict[str, Any],
        mode: str = "preview",
    ) -> Dict[str, Any]:
        import re as _re

        fv: Dict[str, Any] = {}
        url = proposal_data.get("project_url")
        if not url:
            return {"status": "form_error", "filled_values": {}, "error_message": "project_url is missing"}

        submit = str(mode).strip().lower() == "submit"

        try:
            # 1. Navigate to project page
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)

            if await _detect_captcha(page):
                return {"status": "captcha_detected", "filled_values": fv, "error_message": "captcha on page"}

            if await _detect_login_required(page):
                return {"status": "login_required", "filled_values": {}, "error_message": "session expired or not logged in"}

            b = await _body(page)
            fv["url"] = page.url

            # Check already bid
            if _re.search(r"پیشنهاد شما ثبت شده|قبلا پیشنهاد داده‌اید|ویرایش پیشنهاد", b):
                return {"status": "already_bid", "filled_values": fv, "error_message": "already bid on this project"}

            # Check project closed
            if _re.search(r"پروژه (بسته|منقضی|به اتمام رسیده|غیرفعال)|مهلت (ارسال )?(پیشنهاد )?(تمام|به پایان)", b):
                return {"status": "closed", "filled_values": fv, "error_message": "project is closed"}

            # 2. Click expand/bid button
            expand_btn = await _first_matching_locator(page, EXPAND_BUTTON_SELECTORS)
            if expand_btn:
                await expand_btn.scroll_into_view_if_needed()
                await expand_btn.click()
                await page.wait_for_timeout(3000)
            else:
                logger.warning("No expand button found; form may already be visible")

            if await _detect_captcha(page):
                return {"status": "captcha_detected", "filled_values": fv, "error_message": "captcha after expand"}

            if await _detect_login_required(page):
                return {"status": "login_required", "filled_values": {}, "error_message": "login required after expand"}

            # 3. Fill form fields
            # Proposal text
            text_loc = await _first_matching_locator(page, TEXT_SELECTORS)
            if not text_loc:
                b2 = await _body(page)
                return {"status": "form_error", "filled_values": fv,
                        "error_message": "proposal textarea not found", "body": b2[:1500]}

            proposal_text = str(proposal_data.get("proposal_text", ""))
            await text_loc.click()
            await text_loc.fill(proposal_text)
            fv["proposal_len"] = len(await text_loc.input_value())

            # Bid amount
            amount_loc = await _first_matching_locator(page, AMOUNT_SELECTORS)
            if amount_loc and proposal_data.get("bid_amount") is not None:
                amt = int(round(int(proposal_data["bid_amount"]) / 1000.0) * 1000)
                await amount_loc.click()
                await amount_loc.fill("")
                await amount_loc.press_sequentially(str(amt), delay=30)
                fv["bid_amount"] = await amount_loc.input_value()

            # Delivery days
            days_loc = await _first_matching_locator(page, DAYS_SELECTORS)
            if days_loc and proposal_data.get("delivery_days") is not None:
                await days_loc.click()
                await days_loc.fill("")
                await days_loc.press_sequentially(str(int(proposal_data["delivery_days"])), delay=40)
                fv["delivery_days"] = await days_loc.input_value()

            await page.wait_for_timeout(800)
            fv["errors"] = await _errors(page)

            if not submit:
                return {"status": "preview_ready", "filled_values": fv, "error_message": None}

            # 4. Submit
            submit_btn = await _first_matching_locator(page, SUBMIT_BUTTON_SELECTORS)
            if not submit_btn:
                return {"status": "form_error", "filled_values": fv, "error_message": "submit button not found"}

            await submit_btn.scroll_into_view_if_needed()
            await submit_btn.click()
            await page.wait_for_timeout(6000)

            fv["after_errors"] = await _errors(page)
            b_after = await _body(page)

            # Confirm submission
            if _re.search(r"پیشنهاد (شما )?(با موفقیت )?(ارسال|ثبت) شد|پیشنهاد ثبت شده|با موفقیت ثبت شد", b_after):
                return {"status": "submitted", "filled_values": fv, "error_message": None}

            # Check still on form
            still_on_form = await page.locator(SUBMIT_BUTTON_SELECTORS[0]).count() > 0
            return {
                "status": "form_error",
                "filled_values": fv,
                "error_message": f"submission not confirmed (still_on_form={still_on_form}); errors={fv.get('after_errors',[])}",
                "body": b_after[:1200],
            }

        except Exception as exc:
            logger.exception("Unexpected error in Parscoders flow")
            return {
                "status": "form_error",
                "filled_values": fv,
                "error_message": f"{type(exc).__name__}: {exc}"[:400],
            }

    async def __call__(self, page: Any, context: Any, proposal_data: Dict[str, Any], mode: str = "preview") -> Dict[str, Any]:
        return await self.run_site_flow(page, context, proposal_data, mode)


async def run_site_flow(page: Any, context: Any, proposal_data: Dict[str, Any], mode: str = "preview") -> Dict[str, Any]:
    return await ParscodersAdapter.run_site_flow(page, context, proposal_data, mode)
