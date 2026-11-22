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
from typing import Any, Dict, List, Optional, Tuple

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
    "/register",
    "/signup",
    "parscoders.com/login",
    "parscoders.com/auth",
    "parscoders.com/register",
    "parscoders.com/signup",
    "connect-google",
    "connect/google",
    "accounts.google.com",
]

LOGIN_SELECTORS: List[str] = [
    # Google OAuth links and buttons (Persian and English)
    "a[href*='connect-google']", "a[href*='connect/google']", "a[href*='accounts.google.com']",
    "a[href*='auth/google']", "a.google-sign-in", "button.google-sign-in", ".btn-google",
    "[data-provider='google']", "[aria-label*='Google' i]",
    ":is(a, button):has-text('ورود با گوگل')", ":is(a, button):has-text('ورود از طریق گوگل')",
    ":is(a, button):has-text('ثبت نام با گوگل')", ":is(a, button):has-text('ثبت‌نام با گوگل')",
    ":is(a, button):has-text('اتصال با گوگل')", ":is(a, button):has-text('Sign in with Google')",
    ":is(a, button):has-text('Log in with Google')", ":is(a, button):has-text('Continue with Google')",
    # Password inputs and login forms
    "input[type='password']", "input[name='password']", "form[action*='login']", "form[action*='auth']",
    # Login prompts & modals
    ".login-modal", "#login-modal", ".auth-modal", "#auth-modal", ".login-box", ".login-prompt",
    "[class*='login-modal']", "[class*='auth-modal']", "[class*='login-prompt']",
    # Proposal links leading to auth
    "a[href*='login']:has-text('پیشنهاد')", "a[href*='auth']:has-text('پیشنهاد')",
    # Registration banners & guest alerts
    ".register-banner", ".registration-banner", ".signup-banner", ".guest-banner", ".auth-banner",
    "#register-banner", "#registration-banner", "#guest-banner",
    "[class*='register-banner']", "[class*='registration-banner']", "[class*='guest-banner']",
]

LOGIN_TEXT_PATTERNS: List[str] = [
    "برای ثبت پیشنهاد وارد شوید", "برای ثبت پیشنهاد ابتدا وارد شوید", "برای ارسال پیشنهاد وارد شوید",
    "برای ارسال پیشنهاد ابتدا وارد شوید", "برای ارسال پیشنهاد باید وارد شوید", "جهت ارسال پیشنهاد باید وارد شوید",
    "جهت ثبت پیشنهاد باید وارد شوید", "ابتدا وارد حساب کاربری خود شوید", "ابتدا وارد شوید",
    "وارد شوید یا ثبت نام کنید", "وارد شوید یا ثبت‌نام کنید", "ثبت نام در پارس‌کدرز", "ثبت‌نام در پارس‌کدرز",
    "قبلا عضو پارس‌کدرز", "عضو پارس‌کدرز نیستید", "هنوز عضو پارس‌کدرز نیستید", "ورود به حساب کاربری",
    "ورود به حساب", "ورود به سایت", "ورود / ثبت‌نام", "ورود یا ثبت نام", "ورود کاربران",
    "ورود با گوگل", "ورود از طریق گوگل", "ورود با حساب گوگل", "ثبت نام با گوگل", "ثبت‌نام با گوگل",
    "اتصال با گوگل", "با گوگل وارد شوید", "حساب کاربری گوگل", "ایمیل یا رمز عبور", "نام کاربری یا رمز عبور",
    "نام کاربری یا ایمیل", "فراموشی رمز عبور", "ایجاد حساب کاربری", "حساب کاربری ندارید",
    "هنوز ثبت نام نکرده اید", "Sign in with Google", "Log in with Google", "Continue with Google",
]


TEXT_SELECTORS: List[str] = [
    "textarea[name='message']",
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

EDITOR_SELECTORS: List[str] = [
    "[contenteditable='true']",
    ".ql-editor",
    "[contenteditable]",
]

AMOUNT_SELECTORS: List[str] = [
    # Live modal DOM (623370, explore 2026-11-22): the amount input is
    # name=bidAmount / id=init_message_bidAmount, label "مبلغ پیشنهاد".
    # The older selectors below never matched it — amount stayed empty.
    "input#init_message_bidAmount",
    "input[name='bidAmount']",
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
    # Live modal DOM (623370): days input is name=deadline /
    # id=init_message_deadline, label "مهلت انجام", prefilled "10".
    "input#init_message_deadline",
    "input[name='deadline']",
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

VALID_DAYS_SELECTORS: List[str] = [
    # Live modal DOM (623370): validity input is name=validDays /
    # id=init_message_validDays, label "مدت اعتبار پیشنهاد". The site
    # REJECTS the submit when it is empty: "مدت اعتبار پیشنهاد می‌تواند
    # بین 3 تا 45 روز باشد" (T5, 2026-11-22). Default 7 days.
    "input#init_message_validDays",
    "input[name='validDays']",
    "input[placeholder*='مدت اعتبار']",
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
    # Live modal DOM (623370): the form's own submit is #msg_submit_btn
    # ("ارسال پیشنهاد/ توضیح"). Put it first: the generic "ارسال پیشنهاد"
    # button also matches the page's open-modal button behind the modal.
    "#msg_submit_btn",
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
    """Find the first visible locator from candidate selectors.

    Iterates ALL matches of each selector, not just .first: ParsCoders
    duplicates some controls in the DOM (e.g. a hidden copy of the bid
    form), so .first can be the hidden duplicate while a later match is
    the visible one (2026-11-22 T2: the amount click timed out on the
    hidden #init_message_bidAmount while the modal's own input sat visible).
    """
    for sel in selectors:
        try:
            loc = page.locator(sel)
            cnt = await loc.count()
            for i in range(cnt):
                try:
                    cand = loc.nth(i)
                    if await cand.is_visible():
                        return cand
                except Exception:
                    continue
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




async def _wait_visible_textarea(page: Any, tries: int = 15) -> Optional[Any]:
    """Poll until a proposal textarea is VISIBLE; None on timeout.

    Short-circuits immediately if login_required is detected to avoid 15s wait.
    """
    for _ in range(tries):
        if await _detect_login_required(page):
            return None
        loc = await _first_matching_locator(page, TEXT_SELECTORS)
        if loc is not None:
            try:
                if await loc.is_visible():
                    return loc
            except Exception:
                pass
        await page.wait_for_timeout(1000)
    return None


async def _bid_form_open(page: Any, tries: int = 8) -> bool:
    """True only if the bid modal itself is open: its amount input is visible.

    The plain project page contains other visible editors (report form, chat
    widget), so "a description field is visible" does NOT mean the bid form
    is open (2026-11-22 T2 on 623370: the proposal text went into the wrong
    editor while the bid modal never opened, then the amount click timed
    out on the hidden input). The amount input #init_message_bidAmount
    exists only inside the bid modal, so its visibility is the real signal.
    """
    for _ in range(max(1, tries)):
        loc = await _first_matching_locator(page, AMOUNT_SELECTORS)
        if loc is not None:
            try:
                if await loc.is_visible():
                    return True
            except Exception:
                pass
        await page.wait_for_timeout(1000)
    return False


async def _wait_visible_description(page: Any, tries: int = 15) -> Tuple[Optional[Any], bool]:
    """Poll for the proposal description field; returns (locator, is_editor).

    Short-circuits immediately if login_required is detected to avoid 15s wait.
    """
    for _ in range(tries):
        if await _detect_login_required(page):
            return None, False
        loc = await _first_matching_locator(page, EDITOR_SELECTORS)
        if loc is not None:
            try:
                if await loc.is_visible():
                    return loc, True
            except Exception:
                pass
        try:
            for fr in page.frames:
                try:
                    body = fr.locator("body[contenteditable='true']")
                    if await body.count() > 0 and await body.first.is_visible():
                        return body.first, True
                except Exception:
                    continue
        except Exception:
            pass
        loc = await _first_matching_locator(page, TEXT_SELECTORS)
        if loc is not None:
            try:
                if await loc.is_visible():
                    return loc, False
            except Exception:
                pass
        await page.wait_for_timeout(1000)
    return None, False



async def _form_diagnostics(page: Any) -> str:
    """Short snapshot of the page state when the proposal form won't open."""
    try:
        info = await page.evaluate("""() => {
            const tas = [...document.querySelectorAll('textarea')].map(t => (t.name || '') + ':' + (t.offsetParent ? 'vis' : 'hid'));
            const btns = [...document.querySelectorAll('button,a')].map(e => (e.innerText || '').trim()).filter(t => t && t.length < 40).slice(0, 12);
            const ce = [...document.querySelectorAll('[contenteditable]')].map(e => (e.tagName || '') + '.' + (e.className || '').toString().slice(0, 30) + ':' + (e.offsetParent ? 'vis' : 'hid'));
            const ifs = [...document.querySelectorAll('iframe')].map(f => (f.title || f.id || f.src || '').toString().slice(0, 40));
            return 'textareas=[' + tas.join(',') + '] buttons=[' + btns.join('|') + '] contenteditable=[' + ce.join(',') + '] iframes=[' + ifs.join(',') + ']';
        }""")
        return str(info)[:600]
    except Exception:
        return "diagnostics unavailable"


async def _detect_captcha(page: Any) -> bool:
    """True only if a captcha challenge is actually VISIBLE.

    The signup/login form embeds its captcha widget in the DOM even when no
    challenge is shown, so counting matching elements (hidden included)
    misreports 'captcha' on a plain logged-out page (2026-11-16: the signup
    page was reported as 'captcha after expand'). Visibility is checked in
    Python via is_visible(); the text check uses rendered innerText, which
    already excludes hidden text. (No JS evaluate here: an earlier version
    carried an unbalanced paren that silently disabled this check.)
    """
    for sel in CAPTCHA_SELECTORS:
        try:
            loc = page.locator(sel)
            for i in range(await loc.count()):
                try:
                    if await loc.nth(i).is_visible():
                        return True
                except Exception:
                    pass
        except Exception:
            pass
    try:
        body = await _body(page)
        for pat in CAPTCHA_TEXT_SELECTORS:
            text = pat[len(":has-text('"):-2] if pat.startswith(":has-text('") else pat
            if text and text in body:
                return True
    except Exception:
        pass
    return False


async def _detect_login_required(page: Any) -> bool:
    """Detect unauthenticated state, login redirects, prompts, Google buttons, or banners."""
    current_url = getattr(page, "url", "") or ""
    if any(pat in current_url.lower() for pat in LOGIN_URL_PATTERNS):
        return True

    for sel in LOGIN_SELECTORS:
        try:
            loc = page.locator(sel)
            cnt = await loc.count()
            if cnt > 0:
                for i in range(min(cnt, 5)):
                    try:
                        if await loc.nth(i).is_visible():
                            return True
                    except Exception:
                        pass
                if any(k in sel for k in ("password", "connect-google", "google-sign-in", "btn-google", "register-banner", "registration-banner", "signup-banner", "guest-banner", "auth-banner")):
                    return True
        except Exception:
            continue

    try:
        body = await page.evaluate("() => (document.body ? document.body.innerText : '')")
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


# Markers that prove YOU have already bid on a project (shown by the project
# page itself after a bid). This is the ONLY accepted proof of a submission.
# The static counter "تا کنون N پیشنهاد ارسال شده" appears on EVERY project
# page and must NEVER count: the old success regex matched it and reported
# "submitted" twice (2026-11-17/18) without any bid being sent.
ALREADY_BID_RE = r"پیشنهاد شما ثبت شده|قبلا پیشنهاد داده‌اید|ویرایش پیشنهاد"


async def _confirm_submitted(page: Any, url: str) -> Tuple[bool, str]:
    """Reload the project page fresh and check for already-bid markers.

    Returns (confirmed, body_snippet). A fresh reload is required: the text
    right after the submit click still contains the static proposal counter,
    which the old regex mistook for a success message.
    """
    import re as _re
    try:
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        b = await _body(page)
        if _re.search(ALREADY_BID_RE, b):
            return True, b[:1200]
        return False, b[:1200]
    except Exception:
        return False, ""


class ParscodersAdapter:
    """Parscoders bid flow:
    project page -> 'ثبت پیشنهاد' button -> fill form (text, amount, days) -> submit.
    Uses SITE_SESSION_PARSCODERS for auth. Daily limit: 5 bids (shared runner limit).

    Operational Requirement:
    When a session expires, Parscoders renders unauthenticated project pages, login prompts,
    Google OAuth buttons ('ورود با گوگل'), or registration banners. The adapter immediately
    returns status 'login_required' (exit code 0 in runner.py). The SITE_SESSION_PARSCODERS
    secret must then be refreshed by triggering `.github/workflows/capture-parscoders-session.yml`
    or running `scripts/site_proposal/capture_parscoders.py`.
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
        wait_ms = int(proposal_data.get("_wait_ms", 5000))

        try:
            # 1. Navigate to project page
            await page.goto(url, wait_until="domcontentloaded")
            if wait_ms > 0:
                await page.wait_for_timeout(wait_ms)

            if await _detect_captcha(page):
                return {"status": "captcha_detected", "filled_values": fv, "error_message": "captcha on page"}

            if await _detect_login_required(page):
                return {"status": "login_required", "filled_values": {}, "error_message": "session expired or not logged in"}

            b = await _body(page)
            fv["url"] = page.url

            # Check already bid
            if _re.search(r"پیشنهاد شما ثبت شده|قبلا پیشنهاد داده‌اید|ویرایش پیشنهاد", b):
                return {"status": "already_bid", "filled_values": fv, "error_message": "already bid on this project"}

            # Check project closed (wordings seen live 2026-11-22 on 621891:
            # status badge "وضعیت پروژه: بسته" and "زمان مناقصه پایان یافته";
            # the old regex missed both — "پروژه: بسته" has a colon in between).
            if _re.search(r"پروژه (بسته|منقضی|به اتمام رسیده|غیرفعال)|وضعیت پروژه\s*:\s*بسته|زمان مناقصه پایان یافته|مناقصه پایان یافته|مهلت (ارسال )?(پیشنهاد )?(تمام|به پایان)", b):
                return {"status": "closed", "filled_values": fv, "error_message": "project is closed"}

            # 2. Open the proposal form. If login is required, immediately return login_required
            # The open signal is the bid modal's amount input, NOT any visible
            # description field (the page has other editors — see _bid_form_open).
            expand_clicked = False
            desc_tries = 1 if wait_ms == 0 else 3
            if not await _bid_form_open(page, tries=desc_tries):
                if await _detect_login_required(page):
                    return {"status": "login_required", "filled_values": {}, "error_message": "session expired or not logged in"}

                for sel in EXPAND_BUTTON_SELECTORS:
                    try:
                        loc = page.locator(sel)
                        if await loc.count() == 0:
                            continue
                        # Click the first VISIBLE match, not just .first:
                        # the page duplicates the bid button (header + mid-page).
                        first = None
                        for i in range(await loc.count()):
                            try:
                                cand = loc.nth(i)
                                if await cand.is_visible():
                                    first = cand
                                    break
                            except Exception:
                                continue
                        if first is None:
                            continue
                        await first.scroll_into_view_if_needed()
                        await first.click()
                        expand_clicked = True
                        logger.info("Clicked expand button: %s", sel)
                        if wait_ms > 0:
                            await page.wait_for_timeout(1000)
                        if await _detect_login_required(page):
                            return {"status": "login_required", "filled_values": {}, "error_message": "login required after expand"}
                        if await _bid_form_open(page, tries=1 if wait_ms == 0 else 8):
                            break
                    except Exception:
                        continue

            if await _detect_captcha(page):
                return {"status": "captcha_detected", "filled_values": fv, "error_message": "captcha after expand"}

            if await _detect_login_required(page):
                return {"status": "login_required", "filled_values": {}, "error_message": "login required after expand"}

            # 3. Fill form fields
            text_loc, is_editor = await _wait_visible_description(page, tries=1 if wait_ms == 0 else 15)
            if not text_loc:
                if await _detect_login_required(page):
                    return {"status": "login_required", "filled_values": {}, "error_message": "login required: proposal form not accessible"}
                # No bid button anywhere on an OPEN project means we already
                # bid (the closed check above already ruled out a closed
                # project). Before this, it misreported as a form error.
                if not expand_clicked:
                    return {"status": "already_bid", "filled_values": fv,
                            "error_message": "no bid button on an open project — already bid"}
                diag = await _form_diagnostics(page)
                b2 = await _body(page)
                return {"status": "form_error", "filled_values": fv,
                        "error_message": f"proposal form did not open (expand_clicked={expand_clicked}); {diag}",
                        "body": b2[:1500]}

            # Guard: the description must belong to the bid modal, not to
            # another visible editor on the page (report form / chat widget).
            if not await _bid_form_open(page, tries=3):
                return {"status": "form_error", "filled_values": fv,
                        "error_message": "bid form did not open (description found outside the bid modal)"}

            proposal_text = str(proposal_data.get("proposal_text", ""))
            if is_editor:
                # Rich-text body (TinyMCE/CKEditor): a plain click() can hang
                # 30s on actionability (run 43829285293+1: click on the
                # TinyMCE body never completed). press_sequentially focuses
                # the element itself and types, no click-point needed.
                try:
                    await text_loc.press_sequentially(proposal_text, delay=10)
                except Exception:
                    await text_loc.evaluate(
                        "(el, txt) => { el.focus(); document.execCommand('insertText', false, txt); }",
                        proposal_text,
                    )
                fv["proposal_len"] = len((await text_loc.inner_text() or "").strip())
            else:
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

            # Proposal validity days (مدت اعتبار پیشنهاد): the site rejects
            # the submit when this is empty ("می‌تواند بین 3 تا 45 روز باشد").
            # Default 7 days; overridable via proposal_data["valid_days"].
            valid_loc = await _first_matching_locator(page, VALID_DAYS_SELECTORS)
            if valid_loc:
                valid_days = int(proposal_data.get("valid_days") or 7)
                valid_days = max(3, min(45, valid_days))
                await valid_loc.click()
                await valid_loc.fill("")
                await valid_loc.press_sequentially(str(valid_days), delay=40)
                fv["valid_days"] = await valid_loc.input_value()

            await page.wait_for_timeout(800)
            fv["errors"] = await _errors(page)

            if not submit:
                return {"status": "preview_ready", "filled_values": fv, "error_message": None}

            # Hard gate (2026-11-22): never click submit with an empty amount
            # or empty days. On 2026-11-17/18 the amount input matched no
            # selector, so the form went in with an empty amount and the
            # adapter still reported "submitted" off the static counter.
            if submit:
                amt_num = _num(str(fv.get("bid_amount") or ""))
                if proposal_data.get("bid_amount") is not None and (amt_num is None or amt_num <= 0):
                    return {"status": "form_error", "filled_values": fv,
                            "error_message": "amount empty after fill (read-back empty) — refusing to submit"}
                days_num = _num(str(fv.get("delivery_days") or ""))
                if proposal_data.get("delivery_days") is not None and (days_num is None or days_num <= 0):
                    return {"status": "form_error", "filled_values": fv,
                            "error_message": "delivery days empty after fill (read-back empty) — refusing to submit"}

            # 4. Submit
            submit_btn = await _first_matching_locator(page, SUBMIT_BUTTON_SELECTORS)
            if not submit_btn:
                return {"status": "form_error", "filled_values": fv, "error_message": "submit button not found"}

            await submit_btn.scroll_into_view_if_needed()
            await submit_btn.click()
            await page.wait_for_timeout(6000)

            fv["after_errors"] = await _errors(page)

            # Confirm submission: ONLY a fresh reload of the project page
            # showing already-bid markers counts. Never trust the text right
            # after the click — the page's static proposal counter
            # ("تا کنون N پیشنهاد ارسال شده") matches the old success regex.
            confirmed, b_reload = await _confirm_submitted(page, url)
            if confirmed:
                return {"status": "submitted", "filled_values": fv, "error_message": None}

            # Check still on form
            still_on_form = await page.locator(SUBMIT_BUTTON_SELECTORS[0]).count() > 0
            return {
                "status": "form_error",
                "filled_values": fv,
                "error_message": f"submission not confirmed on reload (still_on_form={still_on_form}); errors={fv.get('after_errors',[])}",
                "body": b_reload[:1200],
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
