# ParsCoders Profile Probe Navigation Resilience & Unit Verification Audit

- **Worker ID:** `agw-ACC2-w03`
- **Lead / Account:** Account 2 (`agw-ACC2`)
- **Task:** Script resilience enhancements and unit verification for `scripts/site_proposal/parscoders_profile_probe.py`
- **Target Repository:** `mohammadlali0707-stack/agw-workers`
- **Date:** 2026-11-28
- **Status:** Verified & Resolved; 13/13 offline unit tests passing; line counts strictly <= 400; `.github/workflows/` untouched.

---

## 1. Executive Summary

During production and automated probe execution, [`scripts/site_proposal/parscoders_profile_probe.py`](file:///home/runner/work/_temp/agent-repo-46121081145-1-run-agy/scripts/site_proposal/parscoders_profile_probe.py) experienced unhandled crashes and premature failures caused by Playwright network timeouts (`net::ERR_TIMED_OUT`, `net::ERR_CONNECTION_TIMED_OUT`, and `TimeoutError`).

### Key Root Causes Identified:
1. **Unhandled Homepage Navigation Failure:** The initial homepage navigation (`page.goto(SITE + "/", wait_until="domcontentloaded", timeout=60000)`) had no `try...except` wrapper or retry mechanism. Any transient timeout or packet drop immediately crashed the Python process with an unhandled exception, violating the script contract to exit cleanly on fatal errors.
2. **Absence of Profile Page Retries:** Candidate profile page navigations and secondary probe routines (`probe_portfolio`, `probe_sample_details`, `probe_actions_menu`) lacked retry attempts and exponential backoff. A single transient glitch caused candidate targets to be abandoned immediately.
3. **Repository Line Limit Compliance:** Prior to refactoring, `parscoders_profile_probe.py` stood at 516 lines, exceeding the strict repository governance limit of <= 400 lines per script.

### Delivered Solutions:
1. **Resilient Navigation Helper (`goto_with_retry`):** Implemented a configurable navigation wrapper around `page.goto()` supporting exponential backoff (`delay = initial_backoff * (backoff_factor ** attempt)`), configurable retry counts, environment variable overrides, and transient error recovery.
2. **Graceful Homepage & Profile Page Handling:** Initial homepage failure now retries up to `max_retries`, and if unreachable, writes a structured `homepage_error` into `result.json` and exits with code 1 without crashing. Profile page transient errors are retried before recording page errors and continuing to subsequent candidates.
3. **Comprehensive Offline Unit Test Suite:** Authored [`tests/test_parscoders_profile_probe.py`](file:///home/runner/work/_temp/agent-repo-46121081145-1-run-agy/tests/test_parscoders_profile_probe.py) covering mock timeout recovery, retry exhaustion, parameter overrides, and probe execution without network or Playwright binary dependencies.
4. **Strict Line Limit & Docstring Integrity:** Refactored `parscoders_profile_probe.py` to 394 lines (<= 400) and `test_parscoders_profile_probe.py` to 341 lines (<= 400) while preserving 100% of existing docstrings and inline comments.

---

## 2. Navigation Architecture & Implementation Details

### 2.1 Configuration & Backoff Logic

The [`goto_with_retry()`](file:///home/runner/work/_temp/agent-repo-46121081145-1-run-agy/scripts/site_proposal/parscoders_profile_probe.py#L68-L101) function provides robust retry logic with configurable backoff:

```python
def goto_with_retry(page, url, wait_until="domcontentloaded", timeout=None,
                    max_retries=None, initial_backoff=None, backoff_factor=None,
                    sleep_fn=time.sleep):
    """Resilient navigation wrapper around page.goto() with configurable retries
    and exponential backoff for transient timeouts and connection errors."""
    def _cfg(k, d, conv):
        try: return conv(os.environ.get(k, d))
        except (ValueError, TypeError): return d

    timeout = timeout if timeout is not None else _cfg("PROBE_NAV_TIMEOUT_MS", 60000, int)
    max_retries = max_retries if max_retries is not None else _cfg("PROBE_NAV_RETRIES", 3, int)
    initial_backoff = initial_backoff if initial_backoff is not None else _cfg("PROBE_NAV_INITIAL_BACKOFF", 2.0, float)
    backoff_factor = backoff_factor if backoff_factor is not None else _cfg("PROBE_NAV_BACKOFF", 2.0, float)

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return page.goto(url, wait_until=wait_until, timeout=timeout)
        except Exception as e:
            last_error, err_str = e, str(e)
            if attempt < max_retries:
                delay = initial_backoff * (backoff_factor ** attempt)
                print(f"goto attempt {attempt + 1}/{max_retries + 1} failed for {url} ({type(e).__name__}: {err_str[:120]}), retrying in {delay:.1f}s...")
                sleep_fn(delay)
            else:
                print(f"goto failed for {url} after {max_retries + 1} attempts ({type(e).__name__}: {err_str[:120]})")
                raise last_error
```

### 2.2 Environment Variable Controls
| Environment Variable | Description | Default |
| :--- | :--- | :---: |
| `PROBE_NAV_RETRIES` | Maximum retry attempts after initial failure | `3` |
| `PROBE_NAV_INITIAL_BACKOFF` | Base backoff duration in seconds | `2.0` |
| `PROBE_NAV_BACKOFF` | Multiplier applied per retry attempt ($d = b \cdot m^i$) | `2.0` |
| `PROBE_NAV_TIMEOUT_MS` | Page navigation timeout in milliseconds | `60000` |

---

## 3. Line Count & Code Integrity Audit

The repository rule mandates that all modified repository scripts remain strictly `<= 400` lines and preserve all docstrings/comments.

| File Path | Previous Lines | Refactored Lines | Limit | Status |
| :--- | :---: | :---: | :---: | :---: |
| [`scripts/site_proposal/parscoders_profile_probe.py`](file:///home/runner/work/_temp/agent-repo-46121081145-1-run-agy/scripts/site_proposal/parscoders_profile_probe.py) | 516 | **394** | <= 400 | **PASS** |
| [`tests/test_parscoders_profile_probe.py`](file:///home/runner/work/_temp/agent-repo-46121081145-1-run-agy/tests/test_parscoders_profile_probe.py) | N/A | **341** | <= 400 | **PASS** |

### Verified Preserved Comments & Docstrings:
- Module docstring (lines 2-22) explaining session handling, privacy constraints, and exit contracts.
- CANDIDATES comment: `# Candidate profile/account pages, in the order to try them.`
- PROFILE_HREF_RE comment: `# Header/nav link keywords that point at the profile/account page.`
- `sanitize_cookies` docstring.
- `probe_portfolio` docstring.
- `probe_sample_details` docstring.
- `probe_actions_menu` docstring.
- All numbered operational comments `# 1. Homepage...`, `# 2. Discover...`, `# 3. Visit...`, `# 4. Portfolio...`, `# 5. Sample details...`, and `# 6. Actions menu...`.

---

## 4. Test Suite Execution & Verification

Test execution was verified using Python's standard `unittest` framework:

```bash
$ python3 -m unittest -v tests/test_parscoders_profile_probe.py
```

### Execution Output:
```text
test_sanitize_cookies_valid_and_defaults (tests.test_parscoders_profile_probe.TestCookieSanitization.test_sanitize_cookies_valid_and_defaults) ... ok
test_goto_env_var_configuration (tests.test_parscoders_profile_probe.TestGotoWithRetry.test_goto_env_var_configuration) ... ok
test_goto_exhausts_retries_and_raises (tests.test_parscoders_profile_probe.TestGotoWithRetry.test_goto_exhausts_retries_and_raises) ... ok
test_goto_invalid_env_var_fallback (tests.test_parscoders_profile_probe.TestGotoWithRetry.test_goto_invalid_env_var_fallback) ... ok
test_goto_recovers_after_transient_timeout (tests.test_parscoders_profile_probe.TestGotoWithRetry.test_goto_recovers_after_transient_timeout) ... ok
test_goto_success_first_attempt (tests.test_parscoders_profile_probe.TestGotoWithRetry.test_goto_success_first_attempt) ... ok
test_main_homepage_fatal_timeout_exits_cleanly (tests.test_parscoders_profile_probe.TestMainResilience.test_main_homepage_fatal_timeout_exits_cleanly) ... ok
test_main_homepage_transient_timeout_recovery (tests.test_parscoders_profile_probe.TestMainResilience.test_main_homepage_transient_timeout_recovery) ... ok
test_main_profile_page_timeout_continues (tests.test_parscoders_profile_probe.TestMainResilience.test_main_profile_page_timeout_continues) ... ok
test_probe_actions_menu (tests.test_parscoders_profile_probe.TestProbePageHelpers.test_probe_actions_menu) ... ok
test_probe_portfolio (tests.test_parscoders_profile_probe.TestProbePageHelpers.test_probe_portfolio) ... ok
test_probe_sample_details (tests.test_parscoders_profile_probe.TestProbePageHelpers.test_probe_sample_details) ... ok
test_scroll_page (tests.test_parscoders_profile_probe.TestProbePageHelpers.test_scroll_page) ... ok

----------------------------------------------------------------------
Ran 13 tests in 0.037s

OK
```

All 13 test cases passed with zero errors, zero warnings, and zero external network calls.

---

## 5. Peer Review & Governance Compliance

1. **Peer Review Submission:** Submitted both `scripts/site_proposal/parscoders_profile_probe.py` and `tests/test_parscoders_profile_probe.py` via `python3 /tmp/hqteam.py review`. The Agent Bus returned `HTTPError: 404 -- continue without the team board`, indicating autonomous worker fallback.
2. **Workflow Security:** No files under `.github/workflows/` were modified or touched.
3. **Cross-Account Isolation:** Strictly isolated under Account 2 scope (`agw-ACC2-w03`); zero interactions or modifications involving ACC1, ACC3, or ACC7.
