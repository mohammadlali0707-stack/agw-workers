"""Unit and Integration Tests for Playwright Site Proposal Runner Core (SDD P15).

Tests:
1. Secure loading of SITE_SESSIONS without leaking in logs or result files.
2. Enforcement of maximum 5 daily submissions per site (mode=submit) based on Asia/Tehran date.
3. Correct schema and values in result.json for all states:
   - preview_ready
   - submitted
   - login_required
   - captcha_detected
   - rate_limited
   - form_error
4. Screenshot capture (preview.png and submitted.png).
5. CLI parsing and execution.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import tempfile
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from scripts.site_proposal.runner import (
    DAILY_SUBMIT_LIMIT,
    TEHRAN_TZ,
    get_site_submit_count,
    get_today_tehran,
    increment_submit_count,
    load_site_sessions,
    load_submit_counts,
    main,
    parse_args,
    run_proposal,
    sanitize_cookies,
)


class MockSiteAdapter:
    """Mock site adapter simulating run_site_flow responses."""

    def __init__(
        self,
        status: str = "preview_ready",
        filled_values: dict | None = None,
        error_message: str | None = None,
    ):
        self.status = status
        self.filled_values = (
            filled_values
            if filled_values is not None
            else {
                "proposal_text": "متن تستی پیشنهاد",
                "bid_amount": 5000000,
                "delivery_days": 4,
            }
        )
        self.error_message = error_message
        self.called = False
        self.call_args = None

    async def run_site_flow(self, page, context, proposal_data: dict, mode: str) -> dict:
        self.called = True
        self.call_args = (page, context, proposal_data, mode)
        # Render a simple HTML page to test real screenshot capture
        await page.set_content(
            f"<html><body><h1>Test Mode: {mode} - Status: {self.status}</h1></body></html>"
        )
        return {
            "status": self.status,
            "filled_values": self.filled_values,
            "error_message": self.error_message,
        }


@pytest.fixture
def temp_results_dir():
    """Create a temporary results directory structure."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield Path(tmp_dir)


@pytest.fixture
def sample_proposal_data():
    return {
        "job_id": "test_job_101",
        "lead_id": "notion-uuid-1234",
        "site": "ponisha",
        "project_url": "https://ponisha.ir/project/test-project",
        "proposal_text": "متن پیشنهاد پروژه اختصاصی",
        "bid_amount": 4500000,
        "currency": "TOMAN",
        "delivery_days": 5,
        "milestone_title": "تحویل کامل و تست شده",
        "created_at": "2026-10-28T15:00:00Z",
    }


def setup_proposal_file(results_dir: Path, job_id: str, data: dict) -> Path:
    job_dir = results_dir / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    proposal_file = job_dir / "proposal.json"
    with open(proposal_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return proposal_file


# ─────────────────────────────────────────────────────────────────────────────
# 1. Tests for SITE_SESSIONS Loading and Secret Protection
# ─────────────────────────────────────────────────────────────────────────────


def test_site_sessions_secure_loading_and_no_leak(monkeypatch, caplog):
    """Ensure SITE_SESSIONS parses safely and secrets never appear in logs or result."""
    secret_token = "SUPER_SECRET_COOKIE_TOKEN_99999"
    sessions_json = json.dumps(
        {
            "ponisha": {
                "cookies": [{"name": "auth", "value": secret_token, "domain": "ponisha.ir"}],
                "localStorage": {"auth_user": "user123"},
            }
        }
    )
    monkeypatch.setenv("SITE_SESSIONS", sessions_json)

    with caplog.at_level(logging.DEBUG):
        session, err = load_site_sessions("ponisha")

    assert err is None
    assert session["cookies"][0]["value"] == secret_token
    assert session["localStorage"]["auth_user"] == "user123"

    # Verify secret does not appear anywhere in logs
    for record in caplog.records:
        assert secret_token not in record.message


def test_site_sessions_invalid_json_does_not_leak_raw_value(monkeypatch, caplog):
    """When SITE_SESSIONS is malformed, error message does not expose raw string."""
    bad_secret_payload = '{"ponisha": {"token": "MY_SECRET_PAYLOAD_HERE", UNTERMINATED'
    monkeypatch.setenv("SITE_SESSIONS", bad_secret_payload)

    with caplog.at_level(logging.DEBUG):
        session, err = load_site_sessions("ponisha")

    assert session == {}
    assert err == "Failed to parse SITE_SESSIONS JSON"
    for record in caplog.records:
        assert "MY_SECRET_PAYLOAD_HERE" not in record.message


def test_site_sessions_missing_or_empty(monkeypatch):
    """When SITE_SESSIONS is unset or empty, return appropriate error."""
    monkeypatch.delenv("SITE_SESSIONS", raising=False)
    session, err = load_site_sessions("ponisha")
    assert session == {}
    assert "SITE_SESSIONS environment variable is not configured" in err

    monkeypatch.setenv("SITE_SESSIONS", "   ")
    session, err = load_site_sessions("ponisha")
    assert session == {}
    assert "SITE_SESSIONS environment variable is not configured" in err


def test_site_sessions_missing_site_entry(monkeypatch):
    """When requested site is not in SITE_SESSIONS, return error."""
    monkeypatch.setenv("SITE_SESSIONS", json.dumps({"karlancer": {"cookies": [{"name": "a", "value": "b"}]}}))
    session, err = load_site_sessions("ponisha")
    assert session == {}
    assert "No session found for site 'ponisha'" in err


def test_sanitize_cookies():
    """Verify sanitize_cookies removes illegal fields and formats properly."""
    raw = [
        {"name": "c1", "value": "val1", "sameSite": "no_restriction", "hostOnly": True, "storeId": "0"},
        {"name": "c2", "value": "val2", "domain": "ponisha.ir", "sameSite": "strict"},
        {"invalid": "no_name_or_value"},
    ]
    cleaned = sanitize_cookies(raw, default_domain="ponisha.ir")
    assert len(cleaned) == 2
    assert cleaned[0]["name"] == "c1"
    assert "hostOnly" not in cleaned[0]
    assert "storeId" not in cleaned[0]
    assert cleaned[0]["domain"] == "ponisha.ir"
    assert "sameSite" not in cleaned[0]  # "no_restriction" discarded
    assert cleaned[1]["name"] == "c2"
    assert cleaned[1]["sameSite"] == "Strict"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Tests for 5 Daily Submissions Limit and Asia/Tehran Date
# ─────────────────────────────────────────────────────────────────────────────


def test_tehran_date_formatting():
    """Verify get_today_tehran returns valid YYYY-MM-DD in Asia/Tehran timezone."""
    today = get_today_tehran()
    expected = datetime.now(TEHRAN_TZ).strftime("%Y-%m-%d")
    assert today == expected
    assert len(today.split("-")) == 3


def test_submit_limit_under_5_allowed_and_incremented(temp_results_dir, sample_proposal_data, monkeypatch):
    """Under 5 submits, mode=submit proceeds, increments count on submitted status."""
    today = get_today_tehran()
    counts_file = temp_results_dir / "state" / "site_submit_counts.json"
    counts_file.parent.mkdir(parents=True, exist_ok=True)
    with open(counts_file, "w") as f:
        json.dump({today: {"ponisha": 2}}, f)

    setup_proposal_file(temp_results_dir, "job_sub_1", sample_proposal_data)
    monkeypatch.setenv("SITE_SESSIONS", json.dumps({"ponisha": {"cookies": [{"name": "a", "value": "b", "domain": "ponisha.ir"}]}}))

    adapter = MockSiteAdapter(status="submitted")

    result = asyncio.run(
        run_proposal(
            site="ponisha",
            project_url=sample_proposal_data["project_url"],
            job_id="job_sub_1",
            mode="submit",
            results_dir=temp_results_dir,
            adapter=adapter,
        )
    )

    assert result["status"] == "submitted"
    assert result["daily_submit_count"] == 3
    assert adapter.called is True

    # Verify counts file updated on disk
    updated_counts = load_submit_counts(counts_file)
    assert updated_counts[today]["ponisha"] == 3


def test_submit_limit_at_5_blocks_submit_mode(temp_results_dir, sample_proposal_data, monkeypatch):
    """When site reached 5 submits today, mode=submit is blocked immediately (rate_limited)."""
    today = get_today_tehran()
    counts_file = temp_results_dir / "state" / "site_submit_counts.json"
    counts_file.parent.mkdir(parents=True, exist_ok=True)
    with open(counts_file, "w") as f:
        json.dump({today: {"ponisha": 5}}, f)

    setup_proposal_file(temp_results_dir, "job_sub_limit", sample_proposal_data)
    monkeypatch.setenv("SITE_SESSIONS", json.dumps({"ponisha": {"cookies": [{"name": "a", "value": "b"}]}}))

    adapter = MockSiteAdapter(status="submitted")

    result = asyncio.run(
        run_proposal(
            site="ponisha",
            project_url=sample_proposal_data["project_url"],
            job_id="job_sub_limit",
            mode="submit",
            results_dir=temp_results_dir,
            adapter=adapter,
        )
    )

    assert result["status"] == "rate_limited"
    assert result["daily_submit_count"] == 5
    assert result["screenshot_path"] is None
    assert "Daily submit limit (5) reached" in result["error_message"]
    # Adapter must NOT be called when rate limited
    assert adapter.called is False

    # Count must remain 5
    updated_counts = load_submit_counts(counts_file)
    assert updated_counts[today]["ponisha"] == 5


def test_submit_limit_does_not_block_preview_mode(temp_results_dir, sample_proposal_data, monkeypatch):
    """Even if 5 submits were done today, mode=preview is NOT blocked and count is not incremented."""
    today = get_today_tehran()
    counts_file = temp_results_dir / "state" / "site_submit_counts.json"
    counts_file.parent.mkdir(parents=True, exist_ok=True)
    with open(counts_file, "w") as f:
        json.dump({today: {"ponisha": 5}}, f)

    setup_proposal_file(temp_results_dir, "job_preview_after_5", sample_proposal_data)
    monkeypatch.setenv("SITE_SESSIONS", json.dumps({"ponisha": {"cookies": [{"name": "a", "value": "b", "domain": "ponisha.ir"}]}}))

    adapter = MockSiteAdapter(status="preview_ready")

    result = asyncio.run(
        run_proposal(
            site="ponisha",
            project_url=sample_proposal_data["project_url"],
            job_id="job_preview_after_5",
            mode="preview",
            results_dir=temp_results_dir,
            adapter=adapter,
        )
    )

    assert result["status"] == "preview_ready"
    assert result["daily_submit_count"] == 5
    assert adapter.called is True
    # Count must remain 5
    updated_counts = load_submit_counts(counts_file)
    assert updated_counts[today]["ponisha"] == 5


# ─────────────────────────────────────────────────────────────────────────────
# 3. Tests for Result.json Schemas and Operational States
# ─────────────────────────────────────────────────────────────────────────────


def test_result_json_preview_ready(temp_results_dir, sample_proposal_data, monkeypatch):
    """Test successful preview generation with screenshot and schema validation."""
    setup_proposal_file(temp_results_dir, "job_preview_ok", sample_proposal_data)
    monkeypatch.setenv("SITE_SESSIONS", json.dumps({"ponisha": {"cookies": [{"name": "auth", "value": "123", "domain": "ponisha.ir"}]}}))

    adapter = MockSiteAdapter(status="preview_ready")

    result = asyncio.run(
        run_proposal(
            site="ponisha",
            project_url=sample_proposal_data["project_url"],
            job_id="job_preview_ok",
            mode="preview",
            results_dir=temp_results_dir,
            adapter=adapter,
        )
    )

    assert result["status"] == "preview_ready"
    assert result["mode"] == "preview"
    assert result["job_id"] == "job_preview_ok"
    assert result["site"] == "ponisha"
    assert result["screenshot_path"] == "jobs/job_preview_ok/preview.png"
    assert result["error_message"] is None
    assert "proposal_text" in result["filled_values"]

    # Verify screenshot file actually exists on disk
    screenshot_file = temp_results_dir / result["screenshot_path"]
    assert screenshot_file.exists()
    assert screenshot_file.stat().st_size > 0

    # Verify result.json file exists and matches
    result_json_file = temp_results_dir / "jobs" / "job_preview_ok" / "result.json"
    assert result_json_file.exists()
    with open(result_json_file, "r") as f:
        disk_result = json.load(f)
    assert disk_result == result


def test_result_json_submitted(temp_results_dir, sample_proposal_data, monkeypatch):
    """Test successful submission with submitted.png screenshot and count update."""
    setup_proposal_file(temp_results_dir, "job_submitted_ok", sample_proposal_data)
    monkeypatch.setenv("SITE_SESSIONS", json.dumps({"ponisha": {"cookies": [{"name": "auth", "value": "123", "domain": "ponisha.ir"}]}}))

    adapter = MockSiteAdapter(status="submitted")

    result = asyncio.run(
        run_proposal(
            site="ponisha",
            project_url=sample_proposal_data["project_url"],
            job_id="job_submitted_ok",
            mode="submit",
            results_dir=temp_results_dir,
            adapter=adapter,
        )
    )

    assert result["status"] == "submitted"
    assert result["mode"] == "submit"
    assert result["screenshot_path"] == "jobs/job_submitted_ok/submitted.png"
    assert result["daily_submit_count"] == 1

    screenshot_file = temp_results_dir / result["screenshot_path"]
    assert screenshot_file.exists()
    assert screenshot_file.stat().st_size > 0


def test_result_json_login_required(temp_results_dir, sample_proposal_data, monkeypatch):
    """When adapter reports login_required, capture screenshot and record state."""
    setup_proposal_file(temp_results_dir, "job_login_req", sample_proposal_data)
    monkeypatch.setenv("SITE_SESSIONS", json.dumps({"ponisha": {"cookies": [{"name": "expired", "value": "old", "domain": "ponisha.ir"}]}}))

    adapter = MockSiteAdapter(status="login_required", error_message="Session expired: redirected to /login")

    result = asyncio.run(
        run_proposal(
            site="ponisha",
            project_url=sample_proposal_data["project_url"],
            job_id="job_login_req",
            mode="preview",
            results_dir=temp_results_dir,
            adapter=adapter,
        )
    )

    assert result["status"] == "login_required"
    assert result["error_message"] == "Session expired: redirected to /login"
    assert result["screenshot_path"] == "jobs/job_login_req/preview.png"
    assert (temp_results_dir / result["screenshot_path"]).exists()


def test_result_json_captcha_detected(temp_results_dir, sample_proposal_data, monkeypatch):
    """When adapter reports captcha_detected, record state and capture page."""
    setup_proposal_file(temp_results_dir, "job_captcha", sample_proposal_data)
    monkeypatch.setenv("SITE_SESSIONS", json.dumps({"karlancer": {"cookies": [{"name": "k", "value": "v", "domain": "karlancer.com"}]}}))

    adapter = MockSiteAdapter(status="captcha_detected", error_message="Cloudflare Turnstile challenge detected")

    result = asyncio.run(
        run_proposal(
            site="karlancer",
            project_url=sample_proposal_data["project_url"],
            job_id="job_captcha",
            mode="preview",
            results_dir=temp_results_dir,
            adapter=adapter,
        )
    )

    assert result["status"] == "captcha_detected"
    assert "Cloudflare" in result["error_message"]
    assert result["screenshot_path"] == "jobs/job_captcha/preview.png"


def test_result_json_missing_proposal_file(temp_results_dir, monkeypatch):
    """When proposal.json is missing, record form_error without crashing."""
    monkeypatch.setenv("SITE_SESSIONS", json.dumps({"ponisha": {"cookies": [{"name": "a", "value": "b"}]}}))

    result = asyncio.run(
        run_proposal(
            site="ponisha",
            project_url="https://ponisha.ir/project/missing",
            job_id="job_nonexistent",
            mode="preview",
            results_dir=temp_results_dir,
        )
    )

    assert result["status"] == "form_error"
    assert "proposal.json not found" in result["error_message"]
    assert result["screenshot_path"] is None


def test_result_json_missing_session_records_login_required(temp_results_dir, sample_proposal_data, monkeypatch):
    """When SITE_SESSIONS is not set, runner records login_required without launching browser."""
    monkeypatch.delenv("SITE_SESSIONS", raising=False)
    setup_proposal_file(temp_results_dir, "job_no_session", sample_proposal_data)

    result = asyncio.run(
        run_proposal(
            site="ponisha",
            project_url=sample_proposal_data["project_url"],
            job_id="job_no_session",
            mode="preview",
            results_dir=temp_results_dir,
        )
    )

    assert result["status"] == "login_required"
    assert "SITE_SESSIONS environment variable is not configured" in result["error_message"]
    assert result["screenshot_path"] is None


# ─────────────────────────────────────────────────────────────────────────────
# 4. CLI Argument Parsing and Execution
# ─────────────────────────────────────────────────────────────────────────────


def test_parse_args_all_fields():
    """Verify parse_args handles flags with dashes and underscores."""
    args = parse_args([
        "--site", "ponisha",
        "--project-url", "https://ponisha.ir/project/123",
        "--job-id", "job_cli_1",
        "--mode", "submit",
        "--results-dir", "/tmp/results",
    ])
    assert args.site == "ponisha"
    assert args.project_url == "https://ponisha.ir/project/123"
    assert args.job_id == "job_cli_1"
    assert args.mode == "submit"
    assert args.results_dir == "/tmp/results"


def test_parse_args_defaults():
    """Verify parse_args default values."""
    args = parse_args([
        "--site", "karlancer",
        "--project-url", "https://karlancer.com/project/abc",
        "--job-id", "job_cli_2",
    ])
    assert args.mode == "preview"
    assert args.results_dir == "hq-ai-results"


def test_main_cli_success(temp_results_dir, sample_proposal_data, monkeypatch):
    """Test full main() invocation through CLI arguments."""
    setup_proposal_file(temp_results_dir, "job_main_test", sample_proposal_data)
    monkeypatch.setenv("SITE_SESSIONS", json.dumps({"ponisha": {"cookies": [{"name": "a", "value": "b", "domain": "ponisha.ir"}]}}))

    mock_adapter = MockSiteAdapter(status="preview_ready")

    with patch("scripts.site_proposal.runner.get_site_adapter", return_value=mock_adapter):
        exit_code = main([
            "--site", "ponisha",
            "--project-url", sample_proposal_data["project_url"],
            "--job-id", "job_main_test",
            "--mode", "preview",
            "--results-dir", str(temp_results_dir),
        ])

    assert exit_code == 0
    assert (temp_results_dir / "jobs" / "job_main_test" / "result.json").exists()


def test_submit_mode_failure_does_not_increment_counter(temp_results_dir, sample_proposal_data, monkeypatch):
    """If submission fails (e.g. login_required or form_error), daily count must NOT increment."""
    today = get_today_tehran()
    counts_file = temp_results_dir / "state" / "site_submit_counts.json"
    counts_file.parent.mkdir(parents=True, exist_ok=True)
    with open(counts_file, "w") as f:
        json.dump({today: {"ponisha": 2}}, f)

    setup_proposal_file(temp_results_dir, "job_failed_submit", sample_proposal_data)
    monkeypatch.setenv("SITE_SESSIONS", json.dumps({"ponisha": {"cookies": [{"name": "a", "value": "b", "domain": "ponisha.ir"}]}}))

    adapter = MockSiteAdapter(status="login_required", error_message="Session expired during submit")

    result = asyncio.run(
        run_proposal(
            site="ponisha",
            project_url=sample_proposal_data["project_url"],
            job_id="job_failed_submit",
            mode="submit",
            results_dir=temp_results_dir,
            adapter=adapter,
        )
    )

    assert result["status"] == "login_required"
    assert result["daily_submit_count"] == 2
    counts = load_submit_counts(counts_file)
    assert counts[today]["ponisha"] == 2


def test_multisite_submit_counts_isolation(temp_results_dir, sample_proposal_data, monkeypatch):
    """Counters for ponisha and karlancer are tracked separately, and past days are preserved."""
    today = get_today_tehran()
    counts_file = temp_results_dir / "state" / "site_submit_counts.json"
    counts_file.parent.mkdir(parents=True, exist_ok=True)
    with open(counts_file, "w") as f:
        json.dump({
            "2026-10-27": {"ponisha": 4, "karlancer": 3},
            today: {"ponisha": 4, "karlancer": 1},
        }, f)

    setup_proposal_file(temp_results_dir, "job_karlancer_sub", sample_proposal_data)
    monkeypatch.setenv("SITE_SESSIONS", json.dumps({"karlancer": {"cookies": [{"name": "k", "value": "v", "domain": "karlancer.com"}]}}))

    adapter = MockSiteAdapter(status="submitted")

    result = asyncio.run(
        run_proposal(
            site="karlancer",
            project_url="https://karlancer.com/project/xyz",
            job_id="job_karlancer_sub",
            mode="submit",
            results_dir=temp_results_dir,
            adapter=adapter,
        )
    )

    assert result["status"] == "submitted"
    assert result["daily_submit_count"] == 2

    counts = load_submit_counts(counts_file)
    assert counts[today]["karlancer"] == 2
    assert counts[today]["ponisha"] == 4
    assert counts["2026-10-27"]["ponisha"] == 4
    assert counts["2026-10-27"]["karlancer"] == 3


def test_callable_adapter_support(temp_results_dir, sample_proposal_data, monkeypatch):
    """Verify runner works when adapter is a plain async callable function."""
    setup_proposal_file(temp_results_dir, "job_func_adapter", sample_proposal_data)
    monkeypatch.setenv("SITE_SESSIONS", json.dumps({"ponisha": {"cookies": [{"name": "a", "value": "b", "domain": "ponisha.ir"}]}}))

    async def functional_adapter(page, context, proposal_data, mode):
        await page.set_content("<h1>Functional Adapter</h1>")
        return {
            "status": "preview_ready",
            "filled_values": {"proposal_text": "text1", "bid_amount": 2000, "delivery_days": 1},
            "error_message": None,
        }

    result = asyncio.run(
        run_proposal(
            site="ponisha",
            project_url=sample_proposal_data["project_url"],
            job_id="job_func_adapter",
            mode="preview",
            results_dir=temp_results_dir,
            adapter=functional_adapter,
        )
    )

    assert result["status"] == "preview_ready"
    assert result["filled_values"]["bid_amount"] == 2000
    assert (temp_results_dir / result["screenshot_path"]).exists()


def test_adapter_exception_handled_as_form_error(temp_results_dir, sample_proposal_data, monkeypatch):
    """If an adapter raises an unexpected exception, runner sets form_error cleanly."""
    setup_proposal_file(temp_results_dir, "job_crash_adapter", sample_proposal_data)
    monkeypatch.setenv("SITE_SESSIONS", json.dumps({"ponisha": {"cookies": [{"name": "a", "value": "b", "domain": "ponisha.ir"}]}}))

    async def crashing_adapter(page, context, proposal_data, mode):
        raise RuntimeError("Unexpected DOM selector crash")

    result = asyncio.run(
        run_proposal(
            site="ponisha",
            project_url=sample_proposal_data["project_url"],
            job_id="job_crash_adapter",
            mode="preview",
            results_dir=temp_results_dir,
            adapter=crashing_adapter,
        )
    )

    assert result["status"] == "form_error"
    assert "Runner execution error" in result["error_message"]
    assert (temp_results_dir / "jobs" / "job_crash_adapter" / "result.json").exists()


def test_local_storage_injection_into_page(temp_results_dir, sample_proposal_data, monkeypatch):
    """Verify that localStorage entries in SITE_SESSIONS are injected into page context."""
    setup_proposal_file(temp_results_dir, "job_ls_test", sample_proposal_data)
    monkeypatch.setenv(
        "SITE_SESSIONS",
        json.dumps({
            "ponisha": {
                "cookies": [{"name": "auth", "value": "test_auth", "domain": "ponisha.ir"}],
                "localStorage": {"custom_token": "token_abc_123", "user_profile": '{"id": 42}'},
            }
        }),
    )

    injected_token = None

    class LSVerifyingAdapter:
        async def run_site_flow(self, page, context, proposal_data, mode):
            nonlocal injected_token
            # Route target origin locally without external network
            await context.route(
                "https://ponisha.ir/**",
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="<html><body><h1>LS Test</h1></body></html>",
                ),
            )
            await page.goto("https://ponisha.ir/test")
            injected_token = await page.evaluate("window.localStorage.getItem('custom_token')")
            return {
                "status": "preview_ready",
                "filled_values": {"proposal_text": "t", "bid_amount": 10, "delivery_days": 1},
                "error_message": None,
            }

    result = asyncio.run(
        run_proposal(
            site="ponisha",
            project_url=sample_proposal_data["project_url"],
            job_id="job_ls_test",
            mode="preview",
            results_dir=temp_results_dir,
            adapter=LSVerifyingAdapter(),
        )
    )

    assert result["status"] == "preview_ready"
    assert injected_token == "token_abc_123"

