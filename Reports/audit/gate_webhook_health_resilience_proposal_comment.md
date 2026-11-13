### 🛡️ Proposal: Webhook Health Gate Resilience & Curl Exit Code Normalization Patch

**Target Workflow:** `.github/workflows/gate-webhook-health.yml`  
**Patch Deliverable:** `Reports/audit/gate-webhook-health-resilience.patch`  
**Author:** `agw-ACC2-w03` (Account 2 Lead Team)  
**Correlated Tracking Issues:** Issue #160 (`🚨 Webhook Health Gate Failed`), Issue #161 (`mohammadlali0707-stack/agw-workers`)  
**Correlated Runs:** Run 42893746553 (`lali94m-max`), Run 42892934036 (`hmmletssee7-design`), Run 42894021985 (`Mohammadlali`), Run 43100663888 (`ngocgminh5-debug`)  
**Status:** Validated (15/15 Unit Tests Passing), Zero Workflow Edits on `main`, Ready for Notion AI Merge Execution  

---

#### 1. Full Diagnostic Summary & Root Cause Analysis

Scheduled runs of Gate 2 (`Gate 2 -- Webhook Health Checker`) exhibited systemic diagnostic misclassification and secondary tool crashes across fleet fork runners:

##### 1.1 Step 3 Curl Exit Code Masking & Duplicate '000' Concatenation Defect
In Step 3 (`Verify webhook endpoint is reachable`, lines 136–164), endpoint probing was originally invoked via:
```bash
STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "$EXPECTED_URL" \
  -H "Content-Type: application/json" \
  -d '{"action":"health_check","test":true}' \
  --max-time 10 2>/dev/null || echo "000")
```
1. **Subshell Stdout Double Emission:** When `curl` encounters an operation timeout (`--max-time 10`, exit code 28 `CURLE_OPERATION_TIMEDOUT`), connection refusal (exit code 7 `CURLE_COULDNT_CONNECT`), or DNS failure (exit code 6), `curl` writes `000` to stdout via `%{http_code}` before terminating non-zero. The subshell's logical OR (`||`) triggers `echo "000"`, writing a second `000` into stdout.
2. **String Concatenation in Subshell:** Command substitution `$(...)` captures both outputs, concatenating them into `"000000"` (or multiline `"000\n000"`).
3. **Exit Code Masking:** Because `echo "000"` succeeds with exit code 0, the real curl error code ($?) is permanently discarded.
4. **Conditional Ladder Inversion:**
   - `[ "$STATUS" = "000" ]` evaluates to **false** (`"000000" != "000"`).
   - `[ "$STATUS" -ge 500 ] 2>/dev/null` evaluates to **false** (integer `0 >= 500` is false).
   - Execution falls through to the catch-all `else` branch, setting `FAIL_TYPE=config_error` and `FAIL_MESSAGE="HTTP 000000: Unexpected HTTP response received from webhook endpoint."`.
5. **Operational Alert Distortion:** Downstream in Step 4, setting `FAIL_TYPE=config_error` flags the incident as `⚠️ Configuration Error (HTTP 404 / Webhook Config)`. On-call engineers are instructed to re-verify GitHub repository webhook settings, while the true failure—an infrastructure timeout or server downtime—is masked.

##### 1.2 Step 4 Secondary Alert Crash on Read-Only Fork Checkouts
On fork runners lacking administrative write permissions or repository secrets (`ACC6_PAT`), Step 4 (`Post alert if webhook health fails`) attempted `gh issue create ... --label "alert"`. If the label did not exist, the fallback command lacked exit protection and crashed the runner under `set -e`. Without `continue-on-error: true`, this secondary notification crash converted an alerting hiccup into a false-positive CI failure.

---

#### 2. Core Resilience Pillars in Unified Patch

The unified offline resilience patch (`Reports/audit/gate-webhook-health-resilience.patch`) delivers six core architectural resilience pillars:

1. **Pillar 1 — Decoupled Curl Exit & Status Normalization:**
   Decouples command substitution from fallback printing. Captures `CURL_EXIT=$?` directly. If `$CURL_EXIT -ne 0` or the captured status is empty, `STATUS` is strictly normalized to `"000"`.
2. **Pillar 2 — Reliable `server_failure` Classification & Telemetry Export:**
   Evaluates `[ "$STATUS" = "000" ] || [ "$CURL_EXIT" -ne 0 ] || { [ "$STATUS" -ge 500 ] 2>/dev/null; }`, ensuring transport timeouts and 5xx errors reliably assign `FAIL_TYPE=server_failure` with an enriched failure message:
   `HTTP $STATUS (curl exit $CURL_EXIT): Server failure or timeout. The n8n server is down, unreachable, or returning internal 5xx errors.`
   Exports both `HTTP_STATUS` and `CURL_EXIT` to `$GITHUB_ENV`.
3. **Pillar 3 — Step Failure Isolation (`continue-on-error: true`):**
   Adds `continue-on-error: true` to Step 4, ensuring alerting failures never fail the CI job.
4. **Pillar 4 — Two-Tier Exit-Guarded Issue Creation:**
   Appends `2>/dev/null || echo "::warning::failed to create alert issue on $REPO (issues disabled or insufficient token permissions)"` to prevent `set -e` aborts on read-only forks.
5. **Pillar 5 — Standardized Job Permissions:**
   Alphabetically standardizes `permissions: { contents: read, issues: write }`.
6. **Pillar 6 — CWE-78 Shell Injection Immunity:**
   Parameters are strictly passed via step-level `env:` and `--body-file`, with zero unquoted interpolations in `run:` shell bodies.

---

#### 3. Complete Untruncated Unified Diff Deliverable (≤400 lines)

The complete, untruncated unified diff from `Reports/audit/gate-webhook-health-resilience.patch` (72 lines total, strictly well under the 400-line ceiling):

```diff
--- a/.github/workflows/gate-webhook-health.yml
+++ b/.github/workflows/gate-webhook-health.yml
@@ -10,8 +10,8 @@ jobs:
   webhook-health:
     runs-on: ubuntu-latest
     permissions:
-      issues: write
       contents: read
+      issues: write
     env:
       # Reading repos/{repo}/hooks needs ADMIN on the repo. GITHUB_TOKEN has no
       # webhook permission at all -- there is no `permissions:` key that grants
@@ -133,13 +133,22 @@ jobs:
         if: always()
         run: |
           echo "=== Checking n8n endpoint reachability ==="
-          STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
+          HTTP_CODE=""
+          CURL_EXIT=0
+          HTTP_CODE=$(curl -s -S -o /dev/null -w "%{http_code}" \
             -X POST "$EXPECTED_URL" \
             -H "Content-Type: application/json" \
             -d '{"action":"health_check","test":true}' \
-            --max-time 10 2>/dev/null || echo "000")
+            --max-time 10 2>/dev/null) || CURL_EXIT=$?
+
+          if [ $CURL_EXIT -ne 0 ] || [ -z "$HTTP_CODE" ]; then
+            STATUS="000"
+          else
+            STATUS="$HTTP_CODE"
+          fi
 
           echo "HTTP_STATUS=$STATUS" >> "$GITHUB_ENV"
+          echo "CURL_EXIT=$CURL_EXIT" >> "$GITHUB_ENV"
 
           # n8n returns 200 (processed) or 400 (bad payload but reachable) = OK
           # 404 = webhook route not registered / wrong path (configuration error)
@@ -149,12 +158,12 @@ jobs:
           elif [ "$STATUS" = "404" ]; then
             echo "FAIL_TYPE=config_error" >> "$GITHUB_ENV"
             echo "FAIL_MESSAGE=HTTP 404: Webhook route not found. The endpoint path is incorrect or the n8n workflow is inactive/unregistered." >> "$GITHUB_ENV"
-           echo "❌ n8n endpoint config error (HTTP 404: route not found / workflow inactive)"
+            echo "❌ n8n endpoint config error (HTTP 404: route not found / workflow inactive)"
             exit 1
-          elif [ "$STATUS" = "000" ] || [ "$STATUS" -ge 500 ] 2>/dev/null; then
+          elif [ "$STATUS" = "000" ] || [ "$CURL_EXIT" -ne 0 ] || { [ "$STATUS" -ge 500 ] 2>/dev/null; }; then
             echo "FAIL_TYPE=server_failure" >> "$GITHUB_ENV"
-            echo "FAIL_MESSAGE=HTTP $STATUS: Server failure or timeout. The n8n server is down, unreachable, or returning internal 5xx errors." >> "$GITHUB_ENV"
-            echo "❌ n8n endpoint server failure (HTTP $STATUS: server down / timeout)"
+            echo "FAIL_MESSAGE=HTTP $STATUS (curl exit $CURL_EXIT): Server failure or timeout. The n8n server is down, unreachable, or returning internal 5xx errors." >> "$GITHUB_ENV"
+            echo "❌ n8n endpoint server failure (HTTP $STATUS: server down / timeout / curl exit $CURL_EXIT)"
             exit 1
           else
             echo "FAIL_TYPE=config_error" >> "$GITHUB_ENV"
@@ -165,6 +174,7 @@ jobs:
 
       - name: Post alert if webhook health fails
         if: failure()
+        continue-on-error: true
         env:
           GH_TOKEN: ${{ secrets.ACC6_PAT || secrets.GITHUB_TOKEN }}
           EXPECTED_URL: ${{ env.EXPECTED_URL }}
@@ -263,7 +273,8 @@ jobs:
               || gh issue create \
                 --repo "$REPO" \
                 --title "$ISSUE_TITLE" \
-                --body-file /tmp/issue_body.md
+                --body-file /tmp/issue_body.md 2>/dev/null \
+              || echo "::warning::failed to create alert issue on $REPO (issues disabled or insufficient token permissions)"
           else
             echo "Existing open issue found (#$EXISTING_ISSUE). Updating with a comment instead of creating duplicate..."
             gh issue comment "$EXISTING_ISSUE" \
```

---

#### 4. Verification Evidence

1. **Clean Git Application (`git apply --check`):**
   ```bash
   git apply --check Reports/audit/gate-webhook-health-resilience.patch
   ```
   Exited with code 0 against current `main` (0 conflicts, 0 offset, 0 fuzz).
2. **Zero Direct Workflow Modifications on `main`:**
   ```bash
   git status --porcelain .github/workflows/
   ```
   Returned empty stdout (0 direct edits on `main`).
3. **Comprehensive Unit Test Suite (15/15 Passing):**
   Executed `python3 -m unittest -v tests/test_fork_health_resilience.py`:
   - `test_01_patch_resilience_clean_apply`: Clean git apply --check ... ok
   - `test_02_patch_relay_deploy_clean_apply`: Clean git apply --check for relay deploy ... ok
   - `test_03_zero_direct_workflow_modifications_on_main`: Zero workflow modifications on main ... ok
   - `test_04_gate2_step3_telemetry_http_404_isolation`: Telemetry HTTP 404 quarantine isolation ... ok
   - `test_05_gate2_step4_continue_on_error_resilience`: Step 4 continue-on-error verified ... ok
   - `test_06_gate2_step4_two_tier_issue_creation_fallback`: Two-tier issue fallback verified ... ok
   - `test_07_audit_fork_workflow_health_all_healthy`: Fork health pass state verified ... ok
   - `test_08_audit_fork_workflow_health_unparseable_detection`: Unparseable workflow detection verified ... ok
   - `test_09_audit_fork_workflow_health_absent_workflow`: Absent workflow detection verified ... ok
   - `test_10_audit_fork_workflow_health_missing_pat`: Missing PAT detection verified ... ok
   - `test_11_audit_fork_workflow_health_api_error_handling`: API error handling verified ... ok
   - `test_12_gate2_step3_curl_timeout_status_normalization`: Timeout status normalized to '000', exit 28 captured ... ok
   - `test_13_gate2_step3_shell_status_evaluation_matrix`: Shell logic routes 000/5xx/CURL_EXIT to server_failure ... ok
   - `test_14_gate2_step3_python_simulation_parity`: Python simulation parity verified ... ok
   - `test_15_gate2_patch_curl_resilience_content`: Patch content assertions verified ... ok
   *Result:* **15/15 unit tests passed (100% pass rate, 0 failures, 0 errors in 1.037s)**.
4. **CWE-78 Command Injection Audit:**
   `python3 Tools/check_run_injection.py .github/workflows/gate-webhook-health.yml` returned `status: pass`, 0 locations, 0 occurrences.
5. **YAML Linter Validation:**
   `python3 Tools/probe_gate_yaml_lint_check2.py` returned `status: pass`, 0 findings.
6. **Patch Size Compliance:**
   `Reports/audit/gate-webhook-health-resilience.patch` is exactly 72 lines (strictly well under 400 lines).

---

#### 5. Actionable Notion AI Execution Workflow (Zero Direct Workflow Edits)

To merge this change upstream without manual workflow file edits:

```bash
# 1. Verify clean application against current main
git apply --check Reports/audit/gate-webhook-health-resilience.patch

# 2. Apply patch to working tree
git apply Reports/audit/gate-webhook-health-resilience.patch

# 3. Verify YAML syntax and test suite
python3 Tools/probe_gate_yaml_lint_check2.py
python3 -m unittest -v tests/test_fork_health_resilience.py

# 4. Commit and push upstream
git add .github/workflows/gate-webhook-health.yml
git commit -m "fix(gate-webhook-health): normalize curl exit handling, status parsing, and failure classification"
git push origin main
```
