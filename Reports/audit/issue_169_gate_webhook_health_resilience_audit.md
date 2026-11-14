# Issue #169 Diagnostic Audit & Webhook Health Resilience Patch Validation

- **Worker ID:** `agw-ACC2-w03`
- **Account:** Account 2 (`agw-ACC2`)
- **Date:** 2026-11-14
- **Target Workflow:** `.github/workflows/gate-webhook-health.yml`
- **Target Tracking Issue:** #169 (`mohammadlali0707-stack/agw-workers`)
- **Correlated Runs:**
  - `mohammadlali0707-stack/agw-workers`: runs 43482285448, 43352031291, 43313293491 (`gate-webhook-health.yml`)
  - `lali94m-max/agw-workers`: run 43316523910 (`gate-webhook-health.yml`)
- **Patch Deliverable:** `Reports/audit/gate-webhook-health-resilience.patch`
- **Status:** Validated (21/21 Unit Tests Passing), Zero Direct Workflow Edits on `main`

---

## 1. Root Cause Diagnosis for Issue #169 Incidents

Scheduled Gate 2 (`gate-webhook-health.yml`) runs across both the control repository (`mohammadlali0707-stack`) and fork repositories (`lali94m-max`) exhibited recurring failures caused by two core architectural defects:

### 1.1 Step 3: Curl Exit Code Masking and Duplicate '000' Status Concatenation Defect
In Step 3 (`Verify webhook endpoint is reachable`), endpoint reachability was probed using:
```bash
STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "$EXPECTED_URL" \
  -H "Content-Type: application/json" \
  -d '{"action":"health_check","test":true}' \
  --max-time 10 2>/dev/null || echo "000")
```
1. **Double Output Generation:** When curl encounters an operation timeout (`--max-time 10`, exit code 28 `CURLE_OPERATION_TIMEDOUT`), connection refused (exit code 7), or DNS resolution failure (exit code 6), curl outputs `000` via `%{http_code}` before terminating with a non-zero exit code.
2. **Subshell String Concatenation:** The trailing `|| echo "000"` runs upon non-zero exit, printing a second `000`. The subshell command substitution `$(...)` captures both stdout lines, concatenating them into `"000000"` (or multiline `"000\n000"`).
3. **Exit Code Discard:** The trailing `|| echo "000"` succeeds with exit code 0, discarding curl's actual exit status (`$?`).
4. **Classification Ladder Fallthrough:**
   - `[ "$STATUS" = "000" ]` evaluates to false (`"000000" != "000"`).
   - `[ "$STATUS" -ge 500 ] 2>/dev/null` evaluates to false (`000000` evaluates to 0 in bash integer comparison, and `0 >= 500` is false).
   - The script falls through to the catch-all `else` block, assigning `FAIL_TYPE=config_error` and `FAIL_MESSAGE="HTTP 000000: Unexpected HTTP response received from webhook endpoint."`.
   - **Impact:** Network timeouts and server outages are misclassified as configuration errors, steering maintainers toward webhook settings rather than infrastructure recovery.

### 1.2 Step 4: Secondary Alert Creation Crash on Forks
In Step 4 (`Post alert if webhook health fails`):
1. **Missing Failure Isolation:** The step lacked `continue-on-error: true`.
2. **Missing Fallback Exit Protection:** On fork checkouts where tokens have read-only access or the `alert` label does not exist, `gh label create alert` and `gh issue create ... --label "alert"` fail. The unlabelled fallback command lacked exit protection and warning redirection. Under `set -e`, this terminated the runner with exit code 1, converting a secondary notification hiccup into a hard CI crash.

---

## 2. Resilience Architecture & Patch Implementation

The offline resilience patch (`Reports/audit/gate-webhook-health-resilience.patch`) implements four foundational hardening measures:

1. **Decoupled Curl Execution & Status Normalization:**
   ```bash
   HTTP_CODE=""
   CURL_EXIT=0
   HTTP_CODE=$(curl -s -S -o /dev/null -w "%{http_code}" \
     -X POST "$EXPECTED_URL" \
     -H "Content-Type: application/json" \
     -d '{"action":"health_check","test":true}' \
     --max-time 10 2>/dev/null) || CURL_EXIT=$?

   if [ $CURL_EXIT -ne 0 ] || [ -z "$HTTP_CODE" ]; then
     STATUS="000"
   else
     STATUS="$HTTP_CODE"
   fi
   ```
2. **Reliable `server_failure` Routing:**
   Evaluates `elif [ "$STATUS" = "000" ] || [ "$CURL_EXIT" -ne 0 ] || { [ "$STATUS" -ge 500 ] 2>/dev/null; }; then`, ensuring transport timeouts (exit 28), connection errors (exit 7), DNS failures (exit 6), and 5xx responses consistently assign `FAIL_TYPE=server_failure` with an enriched failure message:
   `HTTP $STATUS (curl exit $CURL_EXIT): Server failure or timeout. The n8n server is down, unreachable, or returning internal 5xx errors.`
3. **Step Failure Isolation (`continue-on-error: true`):**
   Adds `continue-on-error: true` to Step 4 so alert creation issues never abort the CI job.
4. **Guarded Two-Tier Issue Creation Fallback:**
   Chains `|| echo "::warning::failed to create alert issue on $REPO (issues disabled or insufficient token permissions)"` with `2>/dev/null` redirection on the unlabelled fallback.

---

## 3. Verification & Validation Evidence

1. **Clean Patch Verification:**
   `git apply --check Reports/audit/gate-webhook-health-resilience.patch` exits with returncode 0 (zero errors, zero fuzz).
2. **Main Branch Isolation:**
   `git status --porcelain .github/workflows/` confirms 0 direct modifications on `main`.
3. **Unit Test Suite (21/21 Passing):**
   `python3 -m unittest -v tests/test_fork_health_resilience.py` ran 21 tests with 100% pass rate:
   - Validated timeout status normalization (test 12).
   - Validated shell status evaluation matrix (test 13).
   - Validated Python parity simulation (test 14).
   - Validated patch content assertions (test 15).
   - Validated timeout & connection refused telemetry fixtures (tests 16, 17).
   - Validated network error matrix across exits 6, 7, 28, 35, 52, 56 (test 18).
   - Validated exhaustive HTTP status matrix across 2xx, 3xx, 4xx, 5xx, and 000 (test 19).
   - Validated YAML patch application and `continue-on-error: true` structure (test 20).
   - Validated defect `000000` concatenation prevention (test 21).
4. **CWE-78 Security & YAML Lint Checks:**
   - `python3 Tools/check_run_injection.py .github/workflows/gate-webhook-health.yml`: pass (0 occurrences).
   - `python3 Tools/probe_gate_yaml_lint_check2.py`: pass (0 failures).
