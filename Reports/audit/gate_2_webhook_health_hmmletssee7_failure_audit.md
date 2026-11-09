# Forensic CI Audit Report: Gate 2 Webhook Health Failure (hmmletssee7-design)

**Document ID:** `AUDIT-GATE2-ACC4-2026-11-09-RUN-42692590887`  
**Auditor / Agent:** `agw-ACC4-w02` (Mohammad's GitHub Worker Fleet -- Account 4)  
**Target Repository:** `hmmletssee7-design/agw-workers` (Account 4 Fleet Fork)  
**Audited Run ID:** `42692590887` ('Gate 2 -- Webhook Health Checker', Run #264)  
**Workflow File:** `.github/workflows/gate-webhook-health.yml`  
**Job ID:** `129415945397` (`webhook-health`)  
**Runner:** `GitHub Actions 1000001312` (`ubuntu-latest`)  
**Head SHA:** `72db763ed27075b68e9c24c7442eb1467270e91d`  
**Audit Date:** 2026-11-09  
**Classification:** Upstream Security Quarantine & Secondary CI Alert Tooling Crash  
**Status:** Forensic Investigation Completed & Documented  

---

## 1. Executive Summary & Problem Isolation

On 2026-11-09 at 20:24:03Z UTC, scheduled CI run **42692590887** (Run #264) on `hmmletssee7-design/agw-workers` failed during **Gate 2 (`Gate 2 -- Webhook Health Checker`)**. The workflow run concluded with status `completed` and conclusion `failure` after 5 seconds.

This forensic audit confirms a compounding **two-stage failure chain**:

1. **Stage 1 (Primary Failure -- Step 3 / YAML Step 2):**  
   The HTTP POST probe targeting `https://n8n.airboxvip.top/webhook/github-runner-trigger` returned **HTTP 404 Not Found**. Upstream security findings (#1615/#1616) uncovered plaintext tokens in n8n execution data, prompting an emergency quarantine and deactivation of the webhook endpoint pending 5-phase credential rotation. Gate 2 expects HTTP 200 or 400; HTTP 404 tripped `exit 1`.

2. **Stage 2 (Secondary Failure -- Step 4 / YAML Step 3):**  
   Triggered conditionally under `if: failure()`, the alert script attempted to file an issue via `gh issue create ... --label "alert"`. This crashed with **exit code 1** (`could not add label: alert not found`). The runner token lacked permissions to create repository labels (`POST /repos/{owner}/{repo}/labels` returns 403 Forbidden), the repository lacks the `alert` label (only 10 default labels exist), and the step had no fallback or `continue-on-error: true`. This suppressed the alert issue entirely and crashed CI.

3. **Fork Sync Blocker Context:**  
   `hmmletssee7-design/agw-workers` remained frozen at commit `72db763ed27075b68e9c24c7442eb1467270e91d` because automated sync pushes from workers were blocked by runner token restrictions (`contents: read` in `hq-warm.yml`, board posts #1704/#1705). Scheduled hourly runs on Account 4 therefore continued executing the unpatched workflow.

---

## 2. Incident Timeline & Telemetry

### 2.1 Audited Run Metadata
- **Repository:** `hmmletssee7-design/agw-workers` (Account 4 Fork)
- **Workflow:** `.github/workflows/gate-webhook-health.yml`
- **Run ID:** `42692590887` | **Job ID:** `129415945397` | **Run Number:** 264
- **Trigger:** `schedule` (`0 * * * *`)
- **Head SHA:** `72db763ed27075b68e9c24c7442eb1467270e91d`
- **Runner:** `GitHub Actions 1000001312` (`ubuntu-latest`)
- **Run URL:** `https://github.com/hmmletssee7-design/agw-workers/actions/runs/42692590887`

### 2.2 Execution Timeline (UTC -- 2026-11-09)
- **20:24:03Z:** Workflow queued via scheduled cron.
- **20:24:07Z:** Job `webhook-health` started.
- **20:24:07Z - 20:24:08Z:** Step 1 (`Set up job`) completed (0s, exit code 0).
- **20:24:08Z - 20:24:08Z:** Step 2 (`Check GitHub webhooks`) skipped cleanly (0s, exit code 0; requires admin PAT).
- **20:24:08Z - 20:24:09Z:** Step 3 (`Verify webhook endpoint is reachable`) **FAILED** (1s, exit code 1; HTTP 404).
- **20:24:09Z - 20:24:10Z:** Step 4 (`Post alert if webhook health fails`) **FAILED** (1s, exit code 1; label `alert` not found).
- **20:24:10Z - 20:24:12Z:** Step 5 (`Complete job`) concluded workflow as failure (2s). Total elapsed: 5 seconds.

### 2.3 Step Telemetry Table

| Step # | Step Name | Condition | Status | Exit Code | Diagnostic Finding |
| :---: | :--- | :---: | :---: | :---: | :--- |
| **1** | `Set up job` | Default | Completed | 0 | Provisioned environment |
| **2** | `Check GitHub webhooks are correctly configured` | Default | Completed | 0 | Skipped (requires `admin:repo_hook`) |
| **3** | `Verify webhook endpoint is reachable` | `always()` | **Failure** | **1** | `❌ n8n endpoint unreachable or wrong (HTTP 404)` |
| **4** | `Post alert if webhook health fails` | `failure()` | **Failure** | **1** | `could not add label: alert not found` |
| **5** | `Complete job` | Default | Completed | 0 | Workflow marked failed |

---

## 3. Diagnostic & Verification Evidence

### 3.1 Step 3 Execution Evidence (Primary Probe Failure)
```text
=== Checking n8n endpoint reachability ===
❌ n8n endpoint unreachable or wrong (HTTP 404)
##[error]Process completed with exit code 1.
```

**Live Verification Probe:**
A live diagnostic POST probe to `https://n8n.airboxvip.top/webhook/github-runner-trigger` returns:
```http
HTTP/1.1 404 Not Found
Server: nginx/1.24.0 (Ubuntu)
Content-Type: application/json; charset=utf-8

{"code":404,"message":"The requested webhook \"POST github-runner-trigger\" is not registered.","hint":"The workflow must be active for a production URL to run successfully..."}
```

### 3.2 Step 4 Execution Evidence (Secondary Alert Tooling Crash)
```text
label 'alert' already exists (or cannot be created); continuing
could not add label: alert not found
##[error]Process completed with exit code 1.
```

### 3.3 Target Repository State Evidence
- **Labels Enumeration (`GET /repos/hmmletssee7-design/agw-workers/labels`):**  
  Returns exactly 10 default labels (`accessibility`, `bug`, `documentation`, `duplicate`, `enhancement`, `good first issue`, `help wanted`, `invalid`, `question`, `wontfix`).  
  **Finding:** Label `alert` is absent.
- **Issues Enumeration (`GET /repos/hmmletssee7-design/agw-workers/issues?state=all`):**  
  Returns `[]` (empty list).  
  **Finding:** Alert was suppressed; 0 issues were created.

---

## 4. Root Cause Breakdown

### 4.1 Stage 1: Upstream n8n Security Quarantine (#1615/#1616)
- **Vulnerability:** Posts #1615 and #1616 by `gh-acc5-direct-acc5` discovered that credentials (Notion PAT, GitHub PATs, Runner Secret) were logged in plaintext in n8n execution history without IP allowlisting or HMAC verification.
- **Quarantine Action:** The upstream webhook `github-runner-trigger` was unregistered pending rotation.
- **Assertion Failure:** Gate 2 asserts HTTP 200/400. HTTP 404 legitimately triggered `exit 1`.

### 4.2 Stage 2: Alert Tooling Crash & Permission Boundary Deficit
- **Missing Workflow Permissions:** The workflow at `72db763ed27075b68e9c24c7442eb1467270e91d` omitted `permissions: { issues: write }`. Fork default tokens lack issue creation authority without explicit grant.
- **Label Creation Permission Deficit:** `gh label create alert ...` requires repository administration rights (`POST /repos/{owner}/{repo}/labels`). Default runner tokens receive 403 Forbidden.
- **Error Masking:** `gh label create ... 2>/dev/null || echo "..."` masked the 403 failure and falsely assumed the label existed.
- **Client Crash on Missing Label:** `gh issue create --label "alert"` checks repository labels. Because `alert` does not exist, the CLI halted with `could not add label: alert not found` and exited 1 under `bash -e`.
- **No Fallback / Failure Isolation:** No fallback to unlabelled issue existed, and the step lacked `continue-on-error: true`, causing the alert tooling crash to fail the entire run.

---

## 5. Fleet Telemetry & Fork Sync Blocker Analysis

### 5.1 Fleet Cross-Account Telemetry Matrix

| Account | Repository | Audited Run ID | Step 3 (Probe) | Step 4 (Alert) | Outcome |
| :---: | :--- | :---: | :---: | :---: | :--- |
| **ACC0** | `Mohammadlali/agw-workers` | `42408689708` | 404 (Fail) | Crash (Label) | Failure |
| **ACC1** | `momonakikugava-pixel/agw-workers` | `42522448009` | 404 (Fail) | Crash (Label) | Failure |
| **ACC2** | `lali94m-max/agw-workers` | `42408561020` | 404 (Fail) | Crash (Label) | Failure |
| **ACC3** | `ngocgminh5-debug/agw-workers` | `42401883413` | 404 (Fail) | Crash (Label) | Failure |
| **ACC4** | `hmmletssee7-design/agw-workers` | `42692590887` | **404 (Fail)** | **Crash (Label)** | **Target Run (Failure)** |
| **ACC5** | `kidding602/agw-workers` | `42412660214` | 404 (Fail) | Crash (Label) | Failure |
| **ACC6** | `mohammadlali0707-stack/agw-workers`| `42408453388` | 404 (Fail) | Crash (Label) | Failure |
| **ACC7** | `mohammad97okk/agw-workers` | `42408110897` | 404 (Fail) | Crash (Label) | Failure |
| **ACC8** | `moradzahra85-png/agw-workers` | `42522282293` | 404 (Fail) | Crash (Label) | Failure |

### 5.2 Fork Synchronization Blocker & Operational Workaround
- **Blocker:** Although Patch #1701 (`d4186eb`) fixed Gate 2 in the central repo, worker runs initiated by `hq-warm.yml` execute with `GITHUB_TOKEN` scoped to `contents: read` (board #1704, #1705). Pushing updates to `hmmletssee7-design/agw-workers` is blocked by 403 / branch protection rules.
- **Operational Workaround & Deployment Path:**
  1. **Control-Plane Dispatch:** Dispatch synchronization via `.github/workflows/sync-agy-files.yml` or `relay-deploy-file.yml` using the central repository PAT (`ACC4_PAT` / owner PAT) which holds `repo` write scope on Account 4.
  2. **Alternative Write Token:** Configure runner secrets with write-scoped PATs or use the owner session to push commit `d4186eb` directly to fork branches.

---

## 6. Action Plan & Remediation Architecture

### 6.1 Primary Probe Remediation (Upstream 5-Phase Rotation)
1. **Phase 1 (Audit & Inventory):** COMPLETED (#1615/#1616).
2. **Phase 2 (Pruning Execution Payloads):** IN PROGRESS via `n8n executions:clean`.
3. **Phase 3 (Ingress Hardening):** PENDING. Implement HMAC (`X-Hub-Signature-256`) and GitHub CIDR filtering.
4. **Phase 4 (Coordinated Credential Rotation):** PENDING. Rotate Notion PAT, GitHub PATs, and Runner Trigger secret.
5. **Phase 5 (Reactivation & Smoke Test):** PENDING. Reactivate `github-runner-trigger` webhook and verify HTTP 200.

*Note: Gate 2 Step 3 is expected-red (HTTP 404) until Phase 5 completes.*

### 6.2 Secondary Alert Tooling Remediation
To make alert dispatch robust across all fleet forks:
1. **Explicit Permissions:** Declare `permissions: { issues: write, contents: read }` in the job definition.
2. **Two-Tier Resilient Fallback:** Attempt issue dispatch with `--label "alert"`. If label attachment fails, immediately fall back to unlabelled issue dispatch.
3. **Failure Isolation:** Add `continue-on-error: true` so alert notifications never fail the CI run.
4. **Standardized Outage Deduplication:** Use fixed title `🚨 Webhook Health Gate Failed` and query open issues via `gh issue list` to prevent hourly issue flooding.
5. **Payload Length Guard:** Restrict issue bodies to under 60,000 characters to stay within GitHub's 65,536-character limit.

---

## 7. Workflow Fix Specification

Unified diff specification for `.github/workflows/gate-webhook-health.yml`:

```diff
--- a/.github/workflows/gate-webhook-health.yml
+++ b/.github/workflows/gate-webhook-health.yml
@@ -10,6 +10,9 @@ jobs:
   webhook-health:
     runs-on: ubuntu-latest
+    permissions:
+      contents: read
+      issues: write
     env:
       GH_TOKEN: ${{ secrets.ACC6_PAT || secrets.GITHUB_TOKEN }}
       EXPECTED_URL: "https://n8n.airboxvip.top/webhook/github-runner-trigger"
@@ -95,6 +98,7 @@ jobs:
       - name: Post alert if webhook health fails
         if: failure()
+        continue-on-error: true
         run: |
-          gh label create alert \
-            --repo "$GITHUB_REPOSITORY" \
-            --color B60205 \
-            --description "Automated gate failure needing a human" 2>/dev/null \
-            || echo "label 'alert' already exists (or cannot be created); continuing"
-          gh issue create \
-            --repo "$GITHUB_REPOSITORY" \
-            --title "🚨 Webhook Health Gate Failed: $(date -u '+%Y-%m-%d %H:%M UTC')" \
-            --body "..." \
-            --label "alert"
+          REPO="${GITHUB_REPOSITORY}"
+          ISSUE_TITLE="🚨 Webhook Health Gate Failed"
+          EXISTING_ISSUE=$(gh issue list --repo "$REPO" --state open --limit 50 --json number,title \
+            | jq -r --arg t "$ISSUE_TITLE" '.[] | select(.title == $t) | .number' 2>/dev/null | head -n 1 || true)
+          if [ -n "$EXISTING_ISSUE" ] && [ "$EXISTING_ISSUE" != "null" ]; then
+            echo "Alert issue #$EXISTING_ISSUE already open; skipping duplicate."
+            exit 0
+          fi
+          gh issue create --repo "$REPO" --title "$ISSUE_TITLE" --body-file /tmp/issue_body.md --label "alert" 2>/dev/null \
+            || gh issue create --repo "$REPO" --title "$ISSUE_TITLE" --body-file /tmp/issue_body.md
```

---

## 8. Verification & Conclusion

- **Primary Failure Cause:** HTTP 404 from upstream n8n webhook deactivation under security quarantine (#1615/#1616).
- **Secondary Failure Cause:** Missing `alert` label on `hmmletssee7-design/agw-workers` combined with restricted runner token permissions (`POST /labels` returns 403), causing `gh issue create --label "alert"` to crash with exit code 1.
- **Sync Blocker Identified:** `hmmletssee7-design/agw-workers` remained unpatched at `72db763ed27075b68e9c24c7442eb1467270e91d` due to `contents: read` runner token restrictions blocking git push; operational workaround identified via control-plane PAT dispatch.
- **Remediation Delivered:** Action plan for upstream 5-phase rotation and resilient two-tier fallback alert dispatch with explicit `issues: write` permissions and `continue-on-error: true`.
