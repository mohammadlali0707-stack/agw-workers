"""Unit tests for site proposal DOM adapters (Ponisha and Karlancer).

Covers all SDD contracts and scenarios:
- get_site_adapter registry resolution and error handling
- Ponisha: preview_ready, submitted, login_required (redirect and text), captcha_detected
- Karlancer: preview_ready, submitted, login_required, captcha_detected
- Edge cases: missing project_url, form not found, missing submit button
"""

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from scripts.site_proposal.sites import (
    get_site_adapter,
    PonishaAdapter,
    KarlancerAdapter,
)

PONISHA_HTML = """<!DOCTYPE html><html lang="fa"><head><meta charset="utf-8"></head><body>
<form id="bid-form" onsubmit="event.preventDefault(); window._submitted = true;">
<textarea name="description" placeholder="متن پیشنهاد"></textarea>
<input type="text" name="amount" placeholder="مبلغ پیشنهادی" />
<input type="number" name="delivery_days" placeholder="مدت زمان" />
<input type="text" name="milestone_title" placeholder="عنوان قسط" />
<input type="text" name="milestone_amount" placeholder="مبلغ قسط" />
<input type="checkbox" name="agree_terms" />
<button type="submit">ارسال پیشنهاد</button>
</form><script>window._submitted = false;</script></body></html>"""

KARLANCER_HTML = """<!DOCTYPE html><html lang="fa"><head><meta charset="utf-8"></head><body>
<form id="bid-form" onsubmit="event.preventDefault(); window._submitted = true;">
<textarea name="proposal" placeholder="متن پیشنهاد"></textarea>
<input type="text" name="price" placeholder="مبلغ پیشنهادی" />
<input type="number" name="days" placeholder="مدت زمان" />
<button type="submit">ثبت پیشنهاد</button>
</form><script>window._submitted = false;</script></body></html>"""

CAPTCHA_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
<p>من ربات نیستم</p><div class="cf-turnstile"></div></body></html>"""

LOGIN_PROMPT_HTML = """<!DOCTYPE html><html lang="fa"><head><meta charset="utf-8"></head><body>
<div class="alert">برای ارسال پیشنهاد وارد شوید</div><a href="/login">ورود</a></body></html>"""


@pytest_asyncio.fixture
async def test_page():
    """Function-scoped browser page fixture."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        try:
            yield page
        finally:
            await browser.close()


# --- Registry Tests ---


def test_get_site_adapter_ponisha():
    adapter = get_site_adapter("ponisha")
    assert adapter is PonishaAdapter
    assert hasattr(adapter, "run_site_flow")


def test_get_site_adapter_karlancer():
    adapter = get_site_adapter("karlancer")
    assert adapter is KarlancerAdapter
    assert hasattr(adapter, "run_site_flow")


def test_get_site_adapter_case_insensitive():
    assert get_site_adapter("PoNiShA") is PonishaAdapter
    assert get_site_adapter("KARLANCER") is KarlancerAdapter


def test_get_site_adapter_unsupported():
    with pytest.raises(ValueError, match="Unsupported site"):
        get_site_adapter("freelancer")


def test_get_site_adapter_invalid_input():
    with pytest.raises(ValueError, match="non-empty string"):
        get_site_adapter("")
    with pytest.raises(ValueError, match="non-empty string"):
        get_site_adapter(None)  # type: ignore


# --- Ponisha Tests ---


@pytest.mark.asyncio
async def test_ponisha_preview_ready(test_page):
    page = test_page
    url = "https://ponisha.ir/project/101"
    await page.route(url, lambda r: r.fulfill(status=200, body=PONISHA_HTML, content_type="text/html; charset=utf-8"))
    proposal = {"project_url": url, "proposal_text": "متن پیشنهاد", "bid_amount": 4500000, "delivery_days": 5, "milestone_title": "قسط اول", "_wait_ms": 0}
    res = await PonishaAdapter.run_site_flow(page, None, proposal, mode="preview")
    assert res["status"] == "preview_ready" and res["error_message"] is None
    assert res["filled_values"]["proposal_text"] == proposal["proposal_text"]
    assert res["filled_values"]["bid_amount"] == 4500000
    assert res["filled_values"]["delivery_days"] == 5
    assert res["filled_values"]["milestone_title"] == "قسط اول"
    assert await page.evaluate("window._submitted") is False


@pytest.mark.asyncio
async def test_ponisha_submitted(test_page):
    page = test_page
    url = "https://ponisha.ir/project/102"
    await page.route(url, lambda r: r.fulfill(status=200, body=PONISHA_HTML, content_type="text/html; charset=utf-8"))
    proposal = {"project_url": url, "proposal_text": "پیشنهاد نهایی", "bid_amount": 5000000, "delivery_days": 7, "_wait_ms": 0}
    res = await PonishaAdapter.run_site_flow(page, None, proposal, mode="submit")
    assert res["status"] == "submitted" and res["error_message"] is None
    assert res["filled_values"]["bid_amount"] == 5000000
    assert await page.evaluate("window._submitted") is True


@pytest.mark.asyncio
async def test_ponisha_login_required_redirect(test_page):
    page = test_page
    url = "https://ponisha.ir/project/103"
    login_url = "https://ponisha.ir/login"
    await page.route(url, lambda r: r.fulfill(status=302, headers={"Location": login_url}))
    await page.route(login_url, lambda r: r.fulfill(status=200, body="<input type='password'>", content_type="text/html; charset=utf-8"))
    res = await PonishaAdapter.run_site_flow(page, None, {"project_url": url, "proposal_text": "t", "_wait_ms": 0}, mode="preview")
    assert res["status"] == "login_required"
    assert "لاگین" in res["error_message"]
    assert res["filled_values"] == {}


@pytest.mark.asyncio
async def test_ponisha_login_required_prompt(test_page):
    page = test_page
    url = "https://ponisha.ir/project/104"
    await page.route(url, lambda r: r.fulfill(status=200, body=LOGIN_PROMPT_HTML, content_type="text/html; charset=utf-8"))
    res = await PonishaAdapter.run_site_flow(page, None, {"project_url": url, "proposal_text": "t", "_wait_ms": 0}, mode="preview")
    assert res["status"] == "login_required"
    assert "لاگین" in res["error_message"]
    assert res["filled_values"] == {}


@pytest.mark.asyncio
async def test_ponisha_captcha_detected(test_page):
    page = test_page
    url = "https://ponisha.ir/project/105"
    await page.route(url, lambda r: r.fulfill(status=200, body=CAPTCHA_HTML, content_type="text/html; charset=utf-8"))
    res = await PonishaAdapter.run_site_flow(page, None, {"project_url": url, "proposal_text": "t", "_wait_ms": 0}, mode="preview")
    assert res["status"] == "captcha_detected"
    assert "کپچا" in res["error_message"]
    assert res["filled_values"] == {}


# --- Karlancer Tests ---


@pytest.mark.asyncio
async def test_karlancer_preview_ready(test_page):
    page = test_page
    url = "https://karlancer.com/projects/201"
    await page.route(url, lambda r: r.fulfill(status=200, body=KARLANCER_HTML, content_type="text/html; charset=utf-8"))
    proposal = {"project_url": url, "proposal_text": "متن پیشنهاد کارلنسر", "bid_amount": 3000000, "delivery_days": 3, "_wait_ms": 0}
    res = await KarlancerAdapter.run_site_flow(page, None, proposal, mode="preview")
    assert res["status"] == "preview_ready" and res["error_message"] is None
    assert res["filled_values"]["proposal_text"] == proposal["proposal_text"]
    assert res["filled_values"]["bid_amount"] == 3000000
    assert res["filled_values"]["delivery_days"] == 3
    assert await page.evaluate("window._submitted") is False


@pytest.mark.asyncio
async def test_karlancer_submitted(test_page):
    page = test_page
    url = "https://karlancer.com/projects/202"
    await page.route(url, lambda r: r.fulfill(status=200, body=KARLANCER_HTML, content_type="text/html; charset=utf-8"))
    proposal = {"project_url": url, "proposal_text": "پیشنهاد نهایی کارلنسر", "bid_amount": 3500000, "delivery_days": 4, "_wait_ms": 0}
    res = await KarlancerAdapter.run_site_flow(page, None, proposal, mode="submit")
    assert res["status"] == "submitted" and res["error_message"] is None
    assert res["filled_values"]["bid_amount"] == 3500000
    assert await page.evaluate("window._submitted") is True


@pytest.mark.asyncio
async def test_karlancer_login_required(test_page):
    page = test_page
    url = "https://karlancer.com/projects/203"
    await page.route(
        url,
        lambda r: r.fulfill(
            status=200,
            body='<meta charset="utf-8"><p>برای ثبت پیشنهاد ابتدا وارد شوید</p>',
            content_type="text/html; charset=utf-8",
        ),
    )
    res = await KarlancerAdapter.run_site_flow(page, None, {"project_url": url, "proposal_text": "t", "_wait_ms": 0}, mode="preview")
    assert res["status"] == "login_required"
    assert "لاگین" in res["error_message"]
    assert res["filled_values"] == {}


@pytest.mark.asyncio
async def test_karlancer_captcha_detected(test_page):
    page = test_page
    url = "https://karlancer.com/projects/204"
    await page.route(url, lambda r: r.fulfill(status=200, body=CAPTCHA_HTML, content_type="text/html; charset=utf-8"))
    res = await KarlancerAdapter.run_site_flow(page, None, {"project_url": url, "proposal_text": "t", "_wait_ms": 0}, mode="preview")
    assert res["status"] == "captcha_detected"
    assert "کپچا" in res["error_message"]
    assert res["filled_values"] == {}


# --- Edge Cases & Robustness Tests ---


@pytest.mark.asyncio
async def test_missing_project_url():
    res = await PonishaAdapter.run_site_flow(None, None, {})
    assert res["status"] == "form_error"
    assert "project_url" in res["error_message"]


@pytest.mark.asyncio
async def test_form_not_found(test_page):
    page = test_page
    url = "https://ponisha.ir/project/empty"
    await page.route(url, lambda r: r.fulfill(status=200, body="<h1>پروژه بسته شده</h1>", content_type="text/html; charset=utf-8"))
    res = await PonishaAdapter.run_site_flow(page, None, {"project_url": url, "proposal_text": "t", "_wait_ms": 0}, mode="preview")
    assert res["status"] == "form_error"
    assert "فرم ارسال پیشنهاد" in res["error_message"]


@pytest.mark.asyncio
async def test_submit_button_missing(test_page):
    page = test_page
    url = "https://karlancer.com/projects/no-btn"
    html = '<textarea name="proposal"></textarea><input name="price" value="100">'
    await page.route(url, lambda r: r.fulfill(status=200, body=html, content_type="text/html; charset=utf-8"))
    res = await KarlancerAdapter.run_site_flow(page, None, {"project_url": url, "proposal_text": "t", "_wait_ms": 0}, mode="submit")
    assert res["status"] == "form_error"
    assert "دکمه ثبت نهایی" in res["error_message"]


def test_callable_instance_support():
    adapter = PonishaAdapter()
    assert callable(adapter)
    k_adapter = KarlancerAdapter()
    assert callable(k_adapter)
