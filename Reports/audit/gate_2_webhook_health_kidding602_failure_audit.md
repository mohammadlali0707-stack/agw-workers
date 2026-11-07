# Forensic CI Audit Report: Gate 2 Webhook Health Failure (kidding602)

**Document ID:** `AUDIT-GATE2-ACC5-2026-11-07`  
**Auditor / Agent:** `agw-ACC2-w02` (Mohammad's GitHub Worker Fleet -- Account 2)  
**Target Repository:** `kidding602/agw-workers` (Account 5 Fleet Fork)  
**Audited Run ID:** `42412660214` ('Gate 2 -- Webhook Health Checker', Run #246)  
**Workflow File:** `.github/workflows/gate-webhook-health.yml`  
**Job ID:** `128523936742` (`webhook-health`)  
**Audit Date:** 2026-11-07  
**Classification:** Upstream Security Quarantine & Secondary CI Alert Tooling Crash  
**Status:** Verified Forensic Audit Completed, Root Causes Isolated, Resilience Strategy Delivered  

---

## 1. Executive Summary

On 2026-11-07 at 20:46:33Z, scheduled continuous integration run **42412660214** (run attempt 1) on `kidding602/agw-workers` failed during **Gate 2 (`Gate 2 -- Webhook Health Checker`)**.

Referencing `Tools/audit_fork_run_logs.py`, `.github/workflows/gate-webhook-health.yml`, and direct endpoint diagnostics, this audit isolated a two-stage failure chain:

1. **Primary Failure -- Step 2 in YAML (Runner Step 3, "Verify webhook endpoint is reachable"):**  
   The HTTP POST probe targeting `https://n8n.airboxvip.top/webhook/github-runner-trigger` returned **HTTP 404 Not Found** (`{"code":404,"message":"The requested webhook \"POST github-runner-trigger\" is not registered."}`). This deactivation directly results from upstream security audit findings broadcast on the Agent Bus (#1615/#1616 by `gh-acc5-direct-acc5`), which identified plaintext tokens (Notion PAT, GitHub PAT, Runner Secret) in n8n `execution_data` and placed the webhook in security quarantine pending a 5-phase credential rotation.

2. **Secondary Failure -- Step 3 in YAML (Runner Step 4, "Post alert if webhook health fails"):**  
   When Step 2 failed, Step 3 triggered under `if: failure()`. The script executed `gh label create alert ... 2>/dev/null || echo "..."`, swallowing label creation errors. Because fork tokens lack repository label creation permissions (`POST /repos/{owner}/{repo}/labels` returns 403) and `kidding602/agw-workers` only possesses default GitHub labels, the subsequent command `gh issue create --label "alert"` aborted with exit code 1 (`could not add label: 'alert' not found`). This prevented the failure alert issue from being created and crashed the step under `bash -e`.

3. **Fleet Exposure:**  
   Cross-account audit confirmed a 100% failure rate across fleet forks post-quarantine (including ACC3 `ngocgminh5-debug` run `42401883413`, ACC8 `moradzahra85-png` run `42404941144`, ACC6 `mohammadlali0707-stack` run `42408453388`, and ACC5 `kidding602` run `42412660214`).

---

## 2. Incident Timeline & Telemetry

### 2.1 Audited Run Metadata
- **Repository:** `kidding602/agw-workers` (Account 5 Fork)
- **Workflow:** `Gate 2 -- Webhook Health Checker` (`.github/workflows/gate-webhook-health.yml`)
- **Run ID:** `42412660214` | **Run Number:** `246` | **Run Attempt:** `1`
- **Job ID:** `128523936742` (`webhook-health`)
- **Trigger Event:** `schedule` (`0 * * * *`)
- **Workflow URL:** `https://github.com/kidding602/agw-workers/actions/runs/42412660214`
- **Job URL:** `https://github.com/kidding602/agw-workers/actions/runs/42412660214/job/128523936742`
- **Timeline (UTC):**
  - **16:16:20Z:** Run 42390450990 (Run #245) on `kidding602` succeeds (pre-quarantine baseline).
  - **18:36Z:** Security audit posts #1615/#1616 report n8n credential exposure and initiates webhook quarantine.
  - **18:33Z - 20:05Z:** Gate 2 runs fail identically on ACC3 (`ngocgminh5-debug`), ACC8 (`moradzahra85-png`), and ACC6 (`mohammadlali0707-stack`).
  - **20:46:33Z:** Scheduled Gate 2 run 42412660214 triggered on `kidding602/agw-workers`.
  - **20:46:36Z:** Job `128523936742` initializes runner.
  - **20:46:37Z:** Step 2 (YAML Step 1) skips hook verification gracefully (0s, exit code 0).
  - **20:46:38Z:** Step 3 (YAML Step 2) returns HTTP 404 from n8n endpoint and exits 1.
  - **20:46:39Z:** Step 4 (YAML Step 3) crashes on `could not add label: 'alert' not found` and exits 1.
  - **20:46:41Z:** Job completes with conclusion `failure` (total duration: 5s).

### 2.2 Step Execution Telemetry Table

| YAML Step | Runner Step | Step Name | Status | Duration | Exit Code | Diagnostic Finding |
| :---: | :---: | :--- | :---: | :---: | :---: | :--- |
| — | **1** | Set up job | Completed | 1s | 0 | Runner environment initialized |
| **1** | **2** | Check GitHub webhooks are correctly configured | Completed | 0s | 0 (skip) | Skipped (requires admin PAT; exits 0 cleanly) |
| **2** | **3** | Verify webhook endpoint is reachable | **Failed** | 1s | **1** | `❌ n8n endpoint unreachable or wrong (HTTP 404)` |
| **3** | **4** | Post alert if webhook health fails | **Failed** | 1s | **1** | `could not add label: 'alert' not found` |
| — | **5** | Complete job | Completed | 0s | 0 | Final job status: failure |

*Note: In `.github/workflows/gate-webhook-health.yml`, "Verify webhook endpoint is reachable" is Step 2 and "Post alert if webhook health fails" is Step 3. In the runner execution log, they correspond to Steps 3 and 4.*

---

## 3. Verified Diagnostic Evidence

### 3.1 Step 2 in YAML (Runner Step 3): Endpoint Reachability Failure
```text
=== Checking n8n endpoint reachability ===
❌ n8n endpoint unreachable or wrong (HTTP 404)
##[error]Process completed with exit code 1.
```

### 3.2 Step 3 in YAML (Runner Step 4): Alert Tooling Premature Crash
```text
gh label create alert --repo "$GITHUB_REPOSITORY" --color B60205 ... 2>/dev/null \
  || echo "label 'alert' already exists (or cannot be created); continuing"
gh issue create --repo "$GITHUB_REPOSITORY" --title "..." --label "alert"
label 'alert' already exists (or cannot be created); continuing
could not add label: 'alert' not found
##[error]Process completed with exit code 1.
```

### 3.3 Live Endpoint Diagnostic Probe
POST probe against `https://n8n.airboxvip.top/webhook/github-runner-trigger` confirms registered webhook absence:
```text
HTTP/1.1 404 Not Found
Server: nginx/1.24.0 (Ubuntu)
Content-Type: application/json; charset=utf-8
{"code":404,"message":"The requested webhook \"POST github-runner-trigger\" is not registered.","hint":"The workflow must be active for a production URL to run successfully. You can activate the workflow using the toggle in the top-right of the editor. Note that unlike test URL calls, production URL calls aren't shown on the canvas (only in the executions list)"}
```

### 3.4 Label Verification on Target Repository
Querying repository labels for `kidding602/agw-workers` confirms absence of `alert`:
```json
["accessibility", "bug", "documentation", "duplicate", "enhancement", "good first issue", "help wanted", "invalid", "question", "wontfix"]
```

---

## 4. Root Cause Breakdown

### 4.1 Upstream n8n Webhook Quarantine (#1615/#1616)
- **Quarantine Trigger:** Posts #1615 and #1616 by `gh-acc5-direct-acc5` revealed plaintext credentials (Notion PAT, GitHub PAT, Runner Secret) exposed in n8n execution tables, combined with zero upstream ingress protections (no IP allowlisting, no HMAC verification).
- **Quarantine Action:** The `github-runner-trigger` webhook workflow was deactivated in n8n to halt unauthenticated invocations while credential rotation and security hardening are executed.
- **Contract Mismatch:** Gate 2 asserts HTTP 200 or 400. Because the endpoint is deactivated, n8n responds with HTTP 404, correctly failing the assertion.

### 4.2 Secondary Tooling Crash & Permission Boundary
- **Error Swallowing Fallacy:** `gh label create alert ... 2>/dev/null || echo "..."` assumes non-zero exit codes imply label existence.
- **Permission Deficit:** Fork execution contexts use tokens without administrative privileges, making `POST /repos/{owner}/{repo}/labels` fail silently.
- **Missing Label:** The `alert` label does not exist by default on GitHub repositories.
- **Client-Side Failure:** GitHub CLI validates label existence before issue creation. When `alert` is missing, `gh issue create` aborts immediately with `could not add label: 'alert' not found`, terminating the step without creating the issue.

---

## 5. Upstream n8n 5-Phase Rotation Status & Reactivation Criteria

Following post #1615/#1616, the credential remediation is structured across 5 phases:

1. **Phase 1: Read-Only Audit & Credential Inventory:** **COMPLETED** (#1615/#1616). Exposed tokens mapped and quarantined.
2. **Phase 2: Historical Execution Pruning:** **IN PROGRESS**. Pruning historical run payloads using native CLI `n8n executions:clean` to prevent database locks.
3. **Phase 3: Upstream Ingress Protection:** **PENDING**. Provisioning HMAC verification (`X-Hub-Signature-256`) and Nginx GitHub webhook IP CIDR restrictions.
4. **Phase 4: Coordinated Credential Rotation:** **PENDING**. Synchronized rotation of Notion PAT, GitHub PATs, and Runner Trigger secrets.
5. **Phase 5: Webhook Reactivation & Smoke Testing:** **PENDING**. Re-enabling the production trigger in n8n and executing end-to-end verification.

### Reactivation Criteria & Gate 2 ETA
- **Prerequisites for Activation:** Phases 2, 3, and 4 must complete before the webhook is re-enabled. HMAC verification and IP CIDR filtering must be operational.
- **Gate 2 Operational Status:** Step 2 is expected-red (HTTP 404) across all fleet accounts until Phase 5 activation is concluded.

---

## 6. Fleet-Wide Audit & Telemetry Assessment

Inspection across all fleet repositories confirms identical post-quarantine behavior:

| Account | Repository | Audited Run ID | Timestamp (UTC) | Step 2 (HTTP) | Step 3 (Alert) | Outcome |
| :---: | :--- | :---: | :---: | :---: | :---: | :--- |
| **ACC0** | `Mohammadlali/agw-workers` | `42408689708` | 19:57Z | **404 (Fail)** | **Crash (Label)** | Failure |
| **ACC1** | `momonakikugava-pixel/agw-workers` | `42407692569` | 19:44Z | **404 (Fail)** | **Crash (Label)** | Failure |
| **ACC2** | `lali94m-max/agw-workers` | `42408561020` | 19:55Z | **404 (Fail)** | **Crash (Label)** | Failure |
| **ACC3** | `ngocgminh5-debug/agw-workers` | `42401883413` | 18:33Z | **404 (Fail)** | **Crash (Label)** | Failure |
| **ACC4** | `hmmletssee7-design/agw-workers` | `42408260500` | 19:51Z | **404 (Fail)** | **Crash (Label)** | Failure |
| **ACC5** | `kidding602/agw-workers` | `42412660214` | 20:46Z | **404 (Fail)** | **Crash (Label)** | **Target Run (Failure)** |
| **ACC6** | `mohammadlali0707-stack/agw-workers` | `42408453388` | 19:54Z | **404 (Fail)** | **Crash (Label)** | Failure (#1635) |
| **ACC7** | `mohammad97okk/agw-workers` | `42408110897` | 19:50Z | **404 (Fail)** | **Crash (Label)** | Failure |
| **ACC8** | `moradzahra85-png/agw-workers` | `42404941144` | 19:10Z | **404 (Fail)** | **Crash (Label)** | Failure (#1630) |

**Finding:** Post-quarantine Gate 2 runs fail identically across 100% of the active fleet due to the two-stage failure chain.

---

## 7. Fleet Resilience Recommendations & Concrete Mitigations

To eliminate alert tooling crashes, prevent issue spam during extended quarantine, and guarantee alert delivery across all fleet forks:

1. **Hourly Issue Flooding Mitigation (Deduplication Guard):**  
   Because Gate 2 runs hourly (`0 * * * *`) and the webhook remains quarantined (expected-red until Phase 5), unconditional issue creation will spawn 24 issues per day per fork (up to ~192 issues daily across the fleet). Before dispatching an alert, Step 3 must query for existing open gate failure issues:
   ```bash
   EXISTING_ISSUE=$(gh issue list \
     --repo "$GITHUB_REPOSITORY" \
     --state open \
     --search "Webhook Health Gate Failed" \
     --json number \
     --jq '.[0].number' 2>/dev/null || true)

   if [ -n "$EXISTING_ISSUE" ]; then
     echo "::notice::Open alert issue #${EXISTING_ISSUE} already exists; skipping duplicate dispatch to prevent issue flooding during quarantine"
     exit 0
   fi
   ```

2. **Two-Tier Resilient Alert Dispatch:**  
   Update Step 3 in `.github/workflows/gate-webhook-health.yml` to implement an automated unlabelled fallback: attempt issue creation with `--label "alert"`, and if label assignment fails (due to missing label or fork permission boundaries), immediately dispatch the issue without `--label "alert"`:
   ```bash
   if ! gh issue create --repo "$GITHUB_REPOSITORY" --title "$ISSUE_TITLE" --body "$ISSUE_BODY" --label "alert"; then
     echo "::warning::Failed to create issue with '--label alert'; falling back to unlabelled issue creation"
     gh issue create --repo "$GITHUB_REPOSITORY" --title "$ISSUE_TITLE" --body "$ISSUE_BODY"
   fi
   ```

3. **CWE-78 Command Injection Elimination:**  
   Replace inline GitHub Actions template expressions in the script body (`${{ github.server_url }}`, `${{ github.repository }}`, `${{ github.run_id }}`) with runner-provided environment variables (`${GITHUB_SERVER_URL}`, `${GITHUB_REPOSITORY}`, `${GITHUB_RUN_ID}`). Strictly quote all variable expansions (`"$ISSUE_TITLE"`, `"$ISSUE_BODY"`, `"$GITHUB_REPOSITORY"`) to avoid parameter splitting or code injection.

4. **Fleet-Wide Repository Label Pre-Provisioning:**  
   Pre-create the `alert` label (color `#B60205`, description `Automated gate failure needing a human`) across all 8 fleet fork repositories via account setup scripts or admin configuration so label creation permissions during CI runtime are never a single point of failure.

5. **CI Step Error Handling & Failure Isolation:**  
   Add `continue-on-error: true` (or robust shell error trapping with `trap`) to the alert step so notification tooling anomalies never compound workflow failure status or obscure underlying gate diagnostics.

6. **Governance Conformance & Patch Reference:**  
   The core resilient dispatch patch is stored in `Reports/audit/gate-webhook-health-resilience.patch` and documented in `Reports/audit/gate-webhook-health-resilience-proposal.md`, passing `git apply --check` cleanly with exit code 0.

---

## 8. Conclusion

The failure of Gate 2 on `kidding602/agw-workers` (run `42412660214`) is fully isolated:
- Step 2 in YAML (Runner Step 3) failed legitimately due to intentional n8n webhook quarantine (#1615/#1616).
- Step 3 in YAML (Runner Step 4) crashed due to unhandled missing labels in `gh issue create`.
Implementing the two-tier resilient fallback, deduplication guard, and CWE-78 hardening eliminates the alert SPOF, prevents notification flooding during quarantine, and restores full fleet observability.

