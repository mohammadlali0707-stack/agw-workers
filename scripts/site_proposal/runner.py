"""Playwright Runner Core for Freelance Platform Proposals (SDD P15).

Executes two-phase proposal flows (preview and submit) using Playwright Chromium
with pre-authenticated sessions from SITE_SESSIONS.
Enforces the maximum 5 daily submissions limit per platform based on Asia/Tehran date.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import fcntl
import json
import logging
import os
from pathlib import Path
import re
import sys
from typing import Any
import urllib.parse
from zoneinfo import ZoneInfo

logger = logging.getLogger("site_proposal.runner")
if not logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)

TEHRAN_TZ = ZoneInfo("Asia/Tehran")
DAILY_SUBMIT_LIMIT = 5

try:
    from scripts.site_proposal.sites import get_site_adapter
except (ImportError, ModuleNotFoundError):
    def get_site_adapter(site: str):
        raise NotImplementedError(
            f"Site adapter for '{site}' is not available (implemented by parallel subtask)"
        )


def mask_secrets(text: str | None, extra_secrets: list[str] | None = None) -> str | None:
    """Mask known sensitive patterns and credentials to prevent log/result leakage."""
    if not text:
        return text
    masked = str(text)

    # Mask known secrets if provided or found in environment
    candidates = list(extra_secrets or [])
    raw_env = os.environ.get("SITE_SESSIONS", "")
    if raw_env:
        try:
            parsed = json.loads(raw_env)
            if isinstance(parsed, dict):
                for s_data in parsed.values():
                    if isinstance(s_data, dict):
                        for c in s_data.get("cookies", []):
                            if isinstance(c, dict) and c.get("value") and len(str(c["value"])) > 4:
                                candidates.append(str(c["value"]))
                        for v in s_data.get("localStorage", {}).values():
                            if v and len(str(v)) > 4:
                                candidates.append(str(v))
                        for k, val in s_data.items():
                            if k in ("password", "token", "secret", "api_key", "access_token") and isinstance(val, str) and len(val) > 3:
                                candidates.append(val)
        except Exception:
            pass

    for env_k, env_v in os.environ.items():
        if env_k.startswith("SITE_SESSION_") and env_v:
            try:
                parsed_one = json.loads(env_v)
                if isinstance(parsed_one, dict):
                    for c in parsed_one.get("cookies", []):
                        if isinstance(c, dict) and c.get("value") and len(str(c["value"])) > 4:
                            candidates.append(str(c["value"]))
                    for v in parsed_one.get("localStorage", {}).values():
                        if v and len(str(v)) > 4:
                            candidates.append(str(v))
                    for k, val in parsed_one.items():
                        if k in ("password", "token", "secret", "api_key", "access_token") and isinstance(val, str) and len(val) > 3:
                            candidates.append(val)
            except Exception:
                pass

    for s in set(candidates):
        if s and len(s) > 3:
            masked = masked.replace(s, "[REDACTED]")

    # Generic patterns: tokens, auth headers, passwords
    masked = re.sub(r"(bearer\s+)[A-Za-z0-9_\-\.]{8,}", r"\1[REDACTED]", masked, flags=re.I)
    masked = re.sub(
        r"((?:[?&]|\b)(?:token|auth|key|secret|password|access_token|cookie)=)[^& \t\n\r]+",
        r"\1[REDACTED]",
        masked,
        flags=re.I,
    )
    masked = re.sub(
        r"(\b(?:password|passwd|secret|token|api_key)\s*[:=]\s*)[^\s&,;]+",
        r"\1[REDACTED]",
        masked,
        flags=re.I,
    )
    return masked


def mask_secrets_in_obj(data: Any, extra_secrets: list[str] | None = None) -> Any:
    """Recursively mask secrets across strings, dicts, and lists."""
    if isinstance(data, str):
        return mask_secrets(data, extra_secrets=extra_secrets)
    if isinstance(data, dict):
        return {k: mask_secrets_in_obj(v, extra_secrets=extra_secrets) for k, v in data.items()}
    if isinstance(data, list):
        return [mask_secrets_in_obj(v, extra_secrets=extra_secrets) for v in data]
    return data


def get_today_tehran() -> str:
    """Return today's date formatted as YYYY-MM-DD in Asia/Tehran timezone."""
    return datetime.now(TEHRAN_TZ).strftime("%Y-%m-%d")


def load_site_sessions(site: str) -> tuple[dict[str, Any], str | None]:
    """Safely load session credentials for the given site without leaking secrets."""
    one = os.environ.get("SITE_SESSION_" + site.strip().upper(), "").strip()   # owner 2026-10-29: per-site secret wins (easier to renew one site)
    if one:
        try:
            sess = json.loads(one)
            if isinstance(sess, dict) and (sess.get("cookies") or sess.get("localStorage")): return sess, None
        except Exception:
            pass
    raw = os.environ.get("SITE_SESSIONS", "").strip()
    if not raw:
        return {}, "SITE_SESSIONS environment variable is not configured"

    try:
        data = json.loads(raw)
    except Exception as e:
        logger.error("Failed to parse SITE_SESSIONS JSON: %s", type(e).__name__)
        return {}, "Failed to parse SITE_SESSIONS JSON"

    if not isinstance(data, dict):
        return {}, "SITE_SESSIONS root must be a JSON object"

    site_key = site.strip().lower()
    session = data.get(site_key)
    if not session or not isinstance(session, dict):
        return {}, f"No session found for site '{site}' in SITE_SESSIONS"

    cookies = session.get("cookies", [])
    local_storage = session.get("localStorage", {})
    if not cookies and not local_storage:
        return {}, f"Empty session (no cookies or localStorage) for site '{site}'"

    return session, None


def sanitize_cookies(cookies: list[dict], default_domain: str | None = None) -> list[dict]:
    """Clean and sanitize cookie dicts for Playwright context.add_cookies."""
    sanitized: list[dict] = []
    for c in cookies:
        if not isinstance(c, dict):
            continue
        clean: dict[str, Any] = {}
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

        sanitized.append(clean)
    return sanitized


def load_submit_counts(counts_file: Path) -> dict[str, dict[str, int]]:
    """Read daily submission counts dictionary from state/site_submit_counts.json."""
    if not counts_file.exists():
        return {}
    try:
        with open(counts_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.warning("Could not read submit counts file: %s", type(e).__name__)
    return {}


def get_site_submit_count(counts: dict[str, dict[str, int]], today_str: str, site: str) -> int:
    """Get the current submit count for the given site and date."""
    site_key = site.strip().lower()
    return counts.get(today_str, {}).get(site_key, 0)


def increment_submit_count(counts_file: Path, today_str: str, site: str) -> int:
    """Atomically increment submit count for the site on the given date and persist."""
    counts_file.parent.mkdir(parents=True, exist_ok=True)
    site_key = site.strip().lower()

    lock_file = counts_file.with_suffix(".lock")
    with open(lock_file, "w") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            counts = load_submit_counts(counts_file)
            if today_str not in counts or not isinstance(counts[today_str], dict):
                counts[today_str] = {}
            new_count = counts[today_str].get(site_key, 0) + 1
            counts[today_str][site_key] = new_count

            tmp_file = counts_file.with_suffix(".tmp")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(counts, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, counts_file)
            return new_count
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def save_result(jobs_dir: Path, result_data: dict[str, Any]) -> None:
    """Atomically save proposal run result to jobs/<job_id>/result.json."""
    jobs_dir.mkdir(parents=True, exist_ok=True)
    result_file = jobs_dir / "result.json"
    tmp_file = jobs_dir / "result.json.tmp"
    clean_data = mask_secrets_in_obj(result_data)
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(clean_data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_file, result_file)


def validate_proposal_payload(proposal_data: Any) -> tuple[bool, str | None]:
    """Validate proposal payload structure and mandatory fields before browser launch.

    Returns:
        tuple[bool, str | None]: (is_valid, error_message)
    """
    if not isinstance(proposal_data, dict):
        return False, "proposal.json root must be a JSON object"

    # In explore mode, proposal_text is not required
    if proposal_data.get("_explore"):
        return True, None

    # Standard proposals require non-empty proposal_text
    proposal_text = proposal_data.get("proposal_text")
    if proposal_text is None or not str(proposal_text).strip():
        return False, "proposal.json missing or empty required field: proposal_text"

    # Validate bid_amount if provided
    bid_amount = proposal_data.get("bid_amount")
    if bid_amount is not None:
        try:
            amt = float(bid_amount)
            if amt < 0:
                return False, "proposal.json bid_amount must be a positive number"
        except (ValueError, TypeError):
            return False, f"proposal.json invalid bid_amount value: {bid_amount}"

    # Validate delivery_days if provided
    delivery_days = proposal_data.get("delivery_days")
    if delivery_days is not None:
        try:
            days = int(delivery_days)
            if days <= 0:
                return False, "proposal.json delivery_days must be a positive integer"
        except (ValueError, TypeError):
            return False, f"proposal.json invalid delivery_days value: {delivery_days}"

    return True, None


async def run_proposal(
    site: str,
    project_url: str,
    job_id: str,
    mode: str = "preview",
    results_dir: str | Path = "hq-ai-results",
    adapter: Any = None,
    playwright_instance: Any = None,
) -> dict[str, Any]:
    """Execute proposal flow for a freelance platform (preview or submit)."""
    results_path = Path(results_dir).resolve()
    jobs_dir = results_path / "jobs" / job_id
    counts_file = results_path / "state" / "site_submit_counts.json"
    today_tehran = get_today_tehran()

    site = site.strip().lower()
    mode = mode.strip().lower() if mode else "preview"

    counts = load_submit_counts(counts_file)
    current_submit_count = get_site_submit_count(counts, today_tehran, site)

    # 1. Enforce rate limit for submit mode
    if mode == "submit" and current_submit_count >= DAILY_SUBMIT_LIMIT:
        logger.warning(
            "Rate limited: %s has %d submits today (limit %d)",
            site,
            current_submit_count,
            DAILY_SUBMIT_LIMIT,
        )
        result_data = {
            "job_id": job_id,
            "site": site,
            "project_url": project_url,
            "mode": mode,
            "status": "rate_limited",
            "screenshot_path": None,
            "filled_values": {},
            "error_message": f"Daily submit limit ({DAILY_SUBMIT_LIMIT}) reached for {site} on {today_tehran}",
            "daily_submit_count": current_submit_count,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        save_result(jobs_dir, result_data)
        return result_data

    # 2. Read proposal.json
    proposal_file = jobs_dir / "proposal.json"
    if not proposal_file.exists():
        if mode == "preview":
            logger.warning(
                "Diagnostic: proposal.json absent at %s for job '%s' (preview mode). Handled gracefully.",
                proposal_file,
                job_id,
            )
            error_status = "missing_proposal"
        else:
            logger.error(
                "Submit aborted: proposal.json absent at %s for job '%s' (submit mode).",
                proposal_file,
                job_id,
            )
            error_status = "form_error"

        err_details = {
            "error_code": "MISSING_PROPOSAL",
            "code": "MISSING_PROPOSAL",
            "reason": "proposal_file_not_found",
            "message": f"proposal.json not found in jobs/{job_id}",
            "job_id": job_id,
            "site": site,
            "mode": mode,
            "expected_path": str(proposal_file),
            "results_dir": str(results_path),
        }
        result_data = {
            "job_id": job_id,
            "site": site,
            "project_url": project_url,
            "mode": mode,
            "status": error_status,
            "screenshot_path": None,
            "filled_values": {},
            "error_message": f"proposal.json not found in jobs/{job_id}",
            "error_code": "MISSING_PROPOSAL",
            "error_details": err_details,
            "error_metadata": err_details,
            "daily_submit_count": current_submit_count,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        save_result(jobs_dir, result_data)
        return result_data

    try:
        with open(proposal_file, "r", encoding="utf-8") as f:
            proposal_data = json.load(f)
    except Exception as e:
        logger.error("Failed to parse proposal.json: %s", type(e).__name__)
        err_details = {
            "error_code": "INVALID_PROPOSAL_JSON",
            "code": "INVALID_PROPOSAL_JSON",
            "reason": "json_parse_error",
            "message": f"Failed to parse proposal.json: {type(e).__name__}",
            "job_id": job_id,
            "site": site,
            "mode": mode,
            "expected_path": str(proposal_file),
            "exception_type": type(e).__name__,
        }
        result_data = {
            "job_id": job_id,
            "site": site,
            "project_url": project_url,
            "mode": mode,
            "status": "form_error",
            "screenshot_path": None,
            "filled_values": {},
            "error_message": f"Failed to parse proposal.json: {type(e).__name__}",
            "error_code": "INVALID_PROPOSAL_JSON",
            "error_details": err_details,
            "error_metadata": err_details,
            "daily_submit_count": current_submit_count,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        save_result(jobs_dir, result_data)
        return result_data

    if not isinstance(proposal_data, dict) or not proposal_data:
        err_msg = f"proposal.json contains empty or invalid data in jobs/{job_id}"
        if mode == "preview":
            logger.warning("Diagnostic: %s (preview mode). Handled gracefully.", err_msg)
            error_status = "missing_proposal"
        else:
            logger.error("Submit aborted: %s (submit mode).", err_msg)
            error_status = "form_error"

        err_details = {
            "error_code": "EMPTY_PROPOSAL_DATA",
            "code": "EMPTY_PROPOSAL_DATA",
            "reason": "empty_or_invalid_proposal_data",
            "message": err_msg,
            "job_id": job_id,
            "site": site,
            "mode": mode,
            "expected_path": str(proposal_file),
        }
        result_data = {
            "job_id": job_id,
            "site": site,
            "project_url": project_url,
            "mode": mode,
            "status": error_status,
            "screenshot_path": None,
            "filled_values": {},
            "error_message": err_msg,
            "error_code": "EMPTY_PROPOSAL_DATA",
            "error_details": err_details,
            "error_metadata": err_details,
            "daily_submit_count": current_submit_count,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        save_result(jobs_dir, result_data)
        return result_data

    is_valid, validation_err = validate_proposal_payload(proposal_data)
    if not is_valid:
        logger.error("Invalid proposal payload: %s", validation_err)
        result_data = {
            "job_id": job_id,
            "site": site,
            "project_url": project_url,
            "mode": mode,
            "status": "form_error",
            "screenshot_path": None,
            "filled_values": {},
            "error_message": validation_err,
            "daily_submit_count": current_submit_count,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        save_result(jobs_dir, result_data)
        return result_data

    proposal_data.setdefault("job_id", job_id)
    proposal_data.setdefault("site", site)
    proposal_data.setdefault("project_url", project_url)

    # 3. Load authenticated session
    site_session, session_err = load_site_sessions(site)
    if session_err and site == "freelancer":   # owner 2026-11-02: freelancer signs in with Google (momona) inside the adapter
        site_session, session_err = {}, None
    if session_err:
        logger.warning("Session unavailable for %s: %s", site, session_err)
        result_data = {
            "job_id": job_id,
            "site": site,
            "project_url": project_url,
            "mode": mode,
            "status": "login_required",
            "screenshot_path": None,
            "filled_values": {},
            "error_message": mask_secrets(session_err),
            "daily_submit_count": current_submit_count,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        save_result(jobs_dir, result_data)
        return result_data

    # 4. Resolve adapter
    if adapter is None:
        try:
            adapter = get_site_adapter(site)
        except Exception as e:
            logger.error("Adapter unavailable for %s: %s", site, type(e).__name__)
            result_data = {
                "job_id": job_id,
                "site": site,
                "project_url": project_url,
                "mode": mode,
                "status": "form_error",
                "screenshot_path": None,
                "filled_values": {},
                "error_message": f"Adapter for site '{site}' not available: {str(e)}",
                "daily_submit_count": current_submit_count,
                "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            save_result(jobs_dir, result_data)
            return result_data

    # 5. Launch Playwright and execute site adapter
    screenshot_filename = "preview.png" if mode == "preview" else "submitted.png"
    screenshot_path = None
    status = "form_error"
    filled_values = {}
    error_message = None

    from playwright.async_api import async_playwright

    @asynccontextmanager
    async def _playwright_context():
        if playwright_instance is not None:
            yield playwright_instance
        else:
            async with async_playwright() as pw:
                yield pw

    try:
        async with _playwright_context() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(viewport={"width": 1280, "height": 800})
                try:
                    # Inject cookies
                    cookies = site_session.get("cookies", [])
                    if cookies:
                        target_domain = urllib.parse.urlparse(project_url).hostname
                        clean_cookies = sanitize_cookies(cookies, default_domain=target_domain)
                        if clean_cookies:
                            await context.add_cookies(clean_cookies)

                    # Inject localStorage via add_init_script
                    ls_data = site_session.get("localStorage", {})
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
                        await context.add_init_script(init_script)

                    page = await context.new_page()

                    # Run site adapter flow (explore = dump form only, never submits)
                    if proposal_data.get("_explore"):
                        from scripts.site_proposal.sites.explore import explore
                        flow_result = await explore(page, proposal_data)
                    elif hasattr(adapter, "run_site_flow") and callable(adapter.run_site_flow):
                        flow_result = await adapter.run_site_flow(page, context, proposal_data, mode)
                    elif callable(adapter):
                        flow_result = await adapter(page, context, proposal_data, mode)
                    else:
                        raise TypeError(f"Adapter for '{site}' has no run_site_flow method and is not callable")

                    if not isinstance(flow_result, dict):
                        flow_result = {
                            "status": "form_error",
                            "error_message": "Adapter returned invalid response format",
                        }

                    status = flow_result.get("status", "form_error")
                    filled_values = mask_secrets_in_obj(flow_result.get("filled_values", {}))
                    error_message = mask_secrets(flow_result.get("error_message"))

                    # Fallback filled_values from proposal_data if adapter omitted them on success
                    if not filled_values and status in ("preview_ready", "submitted"):
                        filled_values = {
                            k: proposal_data[k]
                            for k in ("proposal_text", "bid_amount", "delivery_days")
                            if k in proposal_data
                        }

                    # Take full-page screenshot
                    if not page.is_closed():
                        screenshot_file = jobs_dir / screenshot_filename
                        try:
                            await page.screenshot(path=str(screenshot_file), full_page=True)
                            screenshot_path = f"jobs/{job_id}/{screenshot_filename}"
                        except Exception as ss_err:
                            logger.warning("Could not take screenshot: %s", type(ss_err).__name__)

                finally:
                    await context.close()
            finally:
                await browser.close()

    except Exception as err:
        logger.error("Runner execution encountered error: %s", type(err).__name__)
        status = "form_error"
        error_message = mask_secrets(f"Runner execution error: {type(err).__name__}")

    # 6. Increment submit count if submission succeeded
    if mode == "submit" and status == "submitted":
        current_submit_count = increment_submit_count(counts_file, today_tehran, site)

    result_data = {
        "job_id": job_id,
        "site": site,
        "project_url": project_url,
        "mode": mode,
        "status": status,
        "screenshot_path": screenshot_path,
        "filled_values": filled_values,
        "error_message": error_message,
        "daily_submit_count": current_submit_count,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    save_result(jobs_dir, result_data)
    return result_data


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for site proposal runner."""
    parser = argparse.ArgumentParser(description="Site proposal runner")
    parser.add_argument("--site", required=True, help="Freelance site (e.g. ponisha, karlancer)")
    parser.add_argument("--project-url", "--project_url", required=True, dest="project_url", help="Project URL")
    parser.add_argument("--job-id", "--job_id", required=True, dest="job_id", help="Job ID")
    parser.add_argument(
        "--mode",
        default="preview",
        choices=["preview", "submit"],
        help="Mode: preview or submit (default: preview)",
    )
    parser.add_argument(
        "--results-dir",
        "--results_dir",
        default="hq-ai-results",
        dest="results_dir",
        help="Path to results directory",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for site proposal runner.

    Returns:
        0 on success or expected operational conditions (preview_ready, submitted,
        rate_limited, login_required, captcha_detected, closed, already_bid;
        missing_proposal in preview mode),
        preventing spurious CI workflow failure while accurately persisting status to result.json.
        1 on unexpected fatal failures or form_error in submit mode.
    """
    args = parse_args(argv)
    result = asyncio.run(
        run_proposal(
            site=args.site,
            project_url=args.project_url,
            job_id=args.job_id,
            mode=args.mode,
            results_dir=args.results_dir,
        )
    )
    status = result.get("status")
    logger.info("Runner finished for job %s with status: %s", args.job_id, status)
    # Expected operational conditions (login_required, rate_limited, captcha_detected,
    # closed, already_bid) exit with code 0 in both preview and submit modes to
    # prevent spurious CI workflow failure. Before this, "closed"/"already_bid"
    # returned 1, so a correctly-detected closed project (2026-11-22 T3b on
    # 621891) showed as a failed run and spammed a failure email + stuck-watch alert.
    if status in ("preview_ready", "submitted", "rate_limited", "login_required", "captcha_detected", "closed", "already_bid"):
        return 0
    if status == "missing_proposal" and args.mode == "preview":
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
