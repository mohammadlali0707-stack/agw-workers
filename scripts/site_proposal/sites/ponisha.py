"""Ponisha Playwright DOM Adapter.

Handles:
- Detecting login redirection / session expiration (status: login_required)
- Detecting captchas (status: captcha_detected)
- Navigating to project page
- Expanding bid form if needed
- Filling proposal text, bid amount, delivery days, milestone title
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
    "auth.ponisha.ir",
    "account/login",
]

LOGIN_TEXT_PATTERNS: List[str] = [
    "برای ارسال پیشنهاد وارد شوید",
    "برای ثبت پیشنهاد وارد شوید",
    "برای ارسال پیشنهاد باید وارد حساب خود شوید",
    "جهت ارسال پیشنهاد باید وارد شوید",
    "ابتدا وارد حساب کاربری خود شوید",
    "ابتدا وارد شوید یا ثبت نام کنید",
    "لطفا شماره موبایل یا ایمیل خود را وارد کنید",
    "شماره موبایل یا ایمیل خود را وارد کنید",
    "ثبت‌نام نکرده اید",
    "ورود به حساب کاربری",
]

TEXT_SELECTORS: List[str] = [
    "textarea[name='description']",
    "textarea[name='proposal_text']",
    "textarea[name='proposal']",
    "textarea[name='bid_text']",
    "textarea[name='comment']",
    "textarea[placeholder*='توضیحات']",
    "textarea[placeholder*='متن پیشنهاد']",
    "textarea[placeholder*='پیشنهاد']",
    "textarea[placeholder*='پیام']",
    "textarea#description",
    "textarea#proposal",
    "textarea",
]

AMOUNT_SELECTORS: List[str] = [
    "input[name='amount']",
    "input[name='bid_amount']",
    "input[name='price']",
    "input[name='cost']",
    "input[placeholder*='مبلغ پیشنهادی']",
    "input[placeholder*='مبلغ']",
    "input[placeholder*='قیمت']",
    "input[placeholder*='هزینه']",
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
    "input#delivery_days",
    "input#days",
    "input#duration",
]

MILESTONE_TITLE_SELECTORS: List[str] = [
    "input[name='milestone_title']",
    "input[name='milestone[0][title]']",
    "input[name='step_title']",
    "input[name='installment_title']",
    "input[placeholder*='عنوان قسط']",
    "input[placeholder*='عنوان مرحله']",
    "input[placeholder*='عنوان پرداخت']",
    "input[placeholder*='مرحله پرداخت']",
    "input#milestone_title",
    "input#step_title",
]

MILESTONE_AMOUNT_SELECTORS: List[str] = [
    "input[name='milestone_amount']",
    "input[name='milestone[0][amount]']",
    "input[name='step_amount']",
    "input[name='installment_amount']",
    "input[placeholder*='مبلغ قسط']",
    "input[placeholder*='مبلغ مرحله']",
    "input#milestone_amount",
]

EXPAND_BUTTON_SELECTORS: List[str] = [
    "button:has-text('ارسال پیشنهاد')",
    "button:has-text('ثبت پیشنهاد')",
    "button:has-text('پیشنهاد جدید')",
    "button:has-text('پیشنهاد دهید')",
    "a:has-text('ارسال پیشنهاد')",
    "a:has-text('ثبت پیشنهاد')",
]

SUBMIT_BUTTON_SELECTORS: List[str] = [
    "button[type='submit']",
    "input[type='submit']",
    "button:has-text('ارسال پیشنهاد')",
    "button:has-text('ثبت پیشنهاد')",
    "button:has-text('ارسال نهایی')",
    "button:has-text('ثبت نهایی')",
    "button:has-text('تایید و ارسال')",
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

    # 3. Explicit login text prompt when proposal form is absent
    try:
        for pat in LOGIN_TEXT_PATTERNS:
            loc = page.locator(f":has-text('{pat}')")
            if await loc.count() > 0:
                return True
    except Exception:
        pass

    return False


class PonishaAdapter:
    """Adapter for interacting with Ponisha bid forms."""

    @classmethod
    async def run_site_flow(
        cls,
        page: Any,
        context: Any,
        proposal_data: Dict[str, Any],
        mode: str = "preview",
    ) -> Dict[str, Any]:
        """Execute the proposal flow on Ponisha.

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

        wait_ms = int(proposal_data.get("_wait_ms", 1500))
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

            # 4. Check if proposal form / textarea is visible; if not, expand it
            text_loc = await _first_matching_locator(page, TEXT_SELECTORS)
            if not text_loc:
                expand_btn = await _first_matching_locator(page, EXPAND_BUTTON_SELECTORS)
                if expand_btn:
                    try:
                        await expand_btn.click()
                        if wait_ms > 0 and hasattr(page, "wait_for_timeout"):
                            await page.wait_for_timeout(wait_ms)
                    except Exception as e:
                        logger.warning(f"Failed clicking expand button on Ponisha: {e}")

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
                try:
                    b = await page.inner_text("body")
                except Exception:
                    b = ""
                if any(w in b for w in ("ویرایش پیشنهاد", "پیشنهاد شما ثبت", "انصراف از پیشنهاد")):
                    return {"status": "already_bid", "filled_values": {}, "error_message": None}
                if "کارفرما این پیشنهاد را انتخاب کرده" in b:
                    return {"status": "awarded_to_other", "filled_values": {}, "error_message": "کارفرما فریلنسر دیگری را انتخاب کرده است."}
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

            # 8. Fill milestone title if provided
            if proposal_data.get("milestone_title"):
                milestone_title_loc = await _first_matching_locator(page, MILESTONE_TITLE_SELECTORS)
                if milestone_title_loc:
                    m_title = str(proposal_data["milestone_title"])
                    await milestone_title_loc.fill(m_title)
                    m_val = await milestone_title_loc.input_value()
                    if m_val:
                        filled_values["milestone_title"] = m_val

                # If milestone amount field is present, fill with bid_amount
                if proposal_data.get("bid_amount") is not None:
                    milestone_amount_loc = await _first_matching_locator(page, MILESTONE_AMOUNT_SELECTORS)
                    if milestone_amount_loc:
                        try:
                            await milestone_amount_loc.fill(str(proposal_data["bid_amount"]))
                        except Exception:
                            pass

            if proposal_data.get("bid_amount") is not None:
                try:
                    ms = page.locator("xpath=//*[normalize-space(text())='به مبلغ']/following::input[1]")
                    if await ms.count() > 0 and await ms.first.is_visible() and not (await ms.first.input_value()).strip():
                        await ms.first.fill(str(proposal_data["bid_amount"]))
                        filled_values["milestone_amount"] = await ms.first.input_value()
                except Exception:
                    pass

            # 9. Agree to terms checkbox if present
            try:
                for chk_sel in [
                    "input[type='checkbox'][name*='term']",
                    "input[type='checkbox'][name*='rule']",
                    "input[type='checkbox'][name*='agree']",
                ]:
                    chk = page.locator(chk_sel)
                    if await chk.count() > 0:
                        first_chk = chk.first
                        if not await first_chk.is_checked():
                            await first_chk.check()
                            break
            except Exception:
                pass

            # 10. Check mode: Preview vs Submit
            is_submit_mode = str(mode).strip().lower() == "submit"
            if not is_submit_mode:
                # Preview mode: Never click submit button
                return {
                    "status": "preview_ready",
                    "filled_values": filled_values,
                    "error_message": None,
                }

            # Submit mode (owner 2026-10-29): click the modal's own "ثبت پیشنهاد" button and CONFIRM the result
            submit_btn = None
            for sel in ["button:has-text('ثبت پیشنهاد')", "button:has-text('ارسال نهایی')", "button:has-text('ثبت نهایی')", "button:has-text('تایید و ارسال')"]:
                loc = page.locator(sel)
                try:
                    n = await loc.count()
                    for i in range(n - 1, -1, -1):
                        if await loc.nth(i).is_visible():
                            submit_btn = loc.nth(i); break
                except Exception:
                    pass
                if submit_btn: break
            if not submit_btn:
                return {"status": "form_error", "filled_values": filled_values, "error_message": "دکمه ثبت نهایی پیشنهاد در صفحه یافت نشد."}
            posts = []
            async def _on_resp(resp):
                try:
                    if resp.request.method in ("POST", "PUT") and "ponisha" in resp.url:
                        t = ""
                        try: t = (await resp.text())[:400]
                        except Exception: pass
                        posts.append(f"{resp.status} {resp.url.split('?')[0][-60:]} {t}")
                except Exception:
                    pass
            page.on("response", _on_resp)
            await submit_btn.click()
            msg = ""
            for _ in range(16):
                await page.wait_for_timeout(500)
                try:
                    for sel in ["[role='alert']", ".Toastify__toast", ".toast", ".ant-message", ".swal2-popup", ".text-danger", ".invalid-feedback", ".error"]:
                        loc = page.locator(sel)
                        if await loc.count() > 0 and await loc.first.is_visible():
                            t = (await loc.first.inner_text()).strip()
                            if t: msg = t[:300]
                except Exception:
                    pass
                try:
                    still = await text_loc.is_visible()
                except Exception:
                    still = False
                if not still: break
            body = ""
            try:
                body = await page.inner_text("body")
            except Exception:
                pass
            ok_words = ("با موفقیت", "ثبت شد", "ارسال شد")
            if (msg and any(w in msg for w in ok_words)) or not still:
                return {"status": "submitted", "filled_values": filled_values, "error_message": None, "site_message": (msg or "") + " | POST: " + " || ".join(posts)[:600]}
            modal = ""
            try:
                modal = (await page.locator("[role='dialog'], .modal, .MuiDialog-root").last.inner_text())[:1500]
            except Exception:
                pass
            return {"status": "not_confirmed", "filled_values": filled_values, "error_message": (msg or "فرم بعد از کلیک بسته نشد؛ ارسال تایید نشد.") + " | POST: " + " || ".join(posts)[:900] + " | MODAL: " + modal.replace(str(proposal_data.get("proposal_text", "")), "[text]")}

        except Exception as exc:
            logger.exception("Unexpected error in Ponisha flow")
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


run_site_flow = PonishaAdapter.run_site_flow
