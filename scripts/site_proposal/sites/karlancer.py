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


class KarlancerAdapter:
    """Adapter for interacting with Karlancer bid forms."""

    @classmethod
    async def run_site_flow(
        cls,
        page: Any,
        context: Any,
        proposal_data: Dict[str, Any],
        mode: str = "preview",
    ) -> Dict[str, Any]:
        """Execute the proposal flow on Karlancer.

        Args:
            page: Playwright Page instance.
            context: Playwright BrowserContext instance.
            proposal_data: Dictionary containing project proposal details.
            mode: 'preview' (default, fill and stop) or 'submit' (submit proposal).

        Returns:
            Dict containing:
                - status: 'preview_ready' | 'submitted' | 'login_required' | 'captcha_detected' | 'form_error'
                - filled_values: dict of values populated in the form
                - error_message: str or None
        """
        filled_values: Dict[str, Any] = {}

        project_url = proposal_data.get("project_url")
        if not project_url:
            return {
                "status": "form_error",
                "filled_values": {},
                "error_message": "project_url is missing in proposal_data",
            }

        wait_ms = int(proposal_data.get("_wait_ms", 500))
        try:
            # 1. Navigate to project page
            await page.goto(project_url, wait_until="domcontentloaded")
            if wait_ms > 0 and hasattr(page, "wait_for_timeout"):
                try:
                    await page.wait_for_timeout(wait_ms)
                except Exception:
                    pass

            # 2. Check for captcha
            if await _detect_captcha(page):
                return {
                    "status": "captcha_detected",
                    "filled_values": {},
                    "error_message": "کپچا در صفحه تشخیص داده شد؛ عملیات متوقف گردید.",
                }

            # 3. Check for login requirement
            if await _detect_login_required(page):
                return {
                    "status": "login_required",
                    "filled_values": {},
                    "error_message": "نشست کاربری منقضی شده یا نیاز به لاگین مجدد دارد.",
                }

            # 4. Check if proposal form / textarea is visible; if not, open modal/form
            text_loc = await _first_matching_locator(page, TEXT_SELECTORS)
            if not text_loc:
                expand_btn = await _first_matching_locator(page, EXPAND_BUTTON_SELECTORS)
                if expand_btn:
                    try:
                        await expand_btn.click()
                        if wait_ms > 0 and hasattr(page, "wait_for_timeout"):
                            await page.wait_for_timeout(wait_ms)
                    except Exception as e:
                        logger.warning(f"Failed clicking expand button on Karlancer: {e}")

                # Re-check login and captcha in case expand opened a modal or redirected
                if await _detect_captcha(page):
                    return {
                        "status": "captcha_detected",
                        "filled_values": {},
                        "error_message": "کپچا در صفحه تشخیص داده شد؛ عملیات متوقف گردید.",
                    }

                if await _detect_login_required(page):
                    return {
                        "status": "login_required",
                        "filled_values": {},
                        "error_message": "نشست کاربری منقضی شده یا نیاز به لاگین مجدد دارد.",
                    }

                text_loc = await _first_matching_locator(page, TEXT_SELECTORS)

            if not text_loc:
                return {
                    "status": "form_error",
                    "filled_values": {},
                    "error_message": "فرم ارسال پیشنهاد یا فیلد متن پیشنهاد در صفحه یافت نشد.",
                }

            # 5. Fill proposal text
            proposal_text = str(proposal_data.get("proposal_text", ""))
            await text_loc.fill(proposal_text)
            filled_values["proposal_text"] = await text_loc.input_value()

            # 6. Fill bid amount if provided
            if proposal_data.get("bid_amount") is not None:
                amount_loc = await _first_matching_locator(page, AMOUNT_SELECTORS)
                if amount_loc:
                    amount_str = str(proposal_data["bid_amount"])
                    await amount_loc.fill(amount_str)
                    raw_val = await amount_loc.input_value()
                    clean_val = raw_val.replace(",", "").strip()
                    filled_values["bid_amount"] = int(clean_val) if clean_val.isdigit() else raw_val

            # 7. Fill delivery days if provided
            if proposal_data.get("delivery_days") is not None:
                days_loc = await _first_matching_locator(page, DAYS_SELECTORS)
                if days_loc:
                    days_str = str(proposal_data["delivery_days"])
                    await days_loc.fill(days_str)
                    raw_val = await days_loc.input_value()
                    clean_val = raw_val.strip()
                    filled_values["delivery_days"] = int(clean_val) if clean_val.isdigit() else raw_val

            # 8. Check mode: Preview vs Submit
            is_submit_mode = str(mode).strip().lower() == "submit"
            if not is_submit_mode:
                # Preview mode: Never click submit button
                return {
                    "status": "preview_ready",
                    "filled_values": filled_values,
                    "error_message": None,
                }

            # Submit mode: Click submit button
            submit_btn = await _first_matching_locator(page, SUBMIT_BUTTON_SELECTORS)
            if not submit_btn:
                return {
                    "status": "form_error",
                    "filled_values": filled_values,
                    "error_message": "دکمه ثبت نهایی پیشنهاد در صفحه یافت نشد.",
                }

            await submit_btn.click()
            if wait_ms > 0 and hasattr(page, "wait_for_timeout"):
                try:
                    await page.wait_for_timeout(min(wait_ms * 2, 2000))
                except Exception:
                    pass

            return {
                "status": "submitted",
                "filled_values": filled_values,
                "error_message": None,
            }

        except Exception as exc:
            logger.exception("Unexpected error in Karlancer flow")
            return {
                "status": "form_error",
                "filled_values": filled_values,
                "error_message": str(exc),
            }

    async def __call__(
        self,
        page: Any,
        context: Any,
        proposal_data: Dict[str, Any],
        mode: str = "preview",
    ) -> Dict[str, Any]:
        return await self.run_site_flow(page, context, proposal_data, mode)


run_site_flow = KarlancerAdapter.run_site_flow
