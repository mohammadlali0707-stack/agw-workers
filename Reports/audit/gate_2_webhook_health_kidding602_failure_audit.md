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

On 2026-11-07 at 20:46:33Z, scheduled CI run **42412660214** (run #246) on `kidding602/agw-workers` failed during **Gate 2 (`Gate 2 -- Webhook Health Checker`)**.

Referencing `Tools/audit_fork_run_logs.py`, `.github/workflows/gate-webhook-health.yml`, and live endpoint diagnostics, this audit isolated a two-stage failure chain:

1. **Primary Failure -- Step 2 in YAML (Runner Step 3, "Verify webhook endpoint is reachable"):**  
   The HTTP POST probe targeting `https://n8n.airboxvip.top/webhook/github-runner-trigger` returned **HTTP 404 Not Found**. This deactivation directly results from upstream security findings on Agent Bus (#1615/#1616 by `gh-acc5-direct-acc5`), which identified plaintext tokens (Notion PAT, GitHub PAT, Runner Secret) in n8n execution data and quarantined the webhook pending 5-phase credential rotation.

2. **Secondary Failure -- Step 3 in YAML (Runner Step 4, "Post alert if webhook health fails"):**  
   Triggered under `if: failure()`, the script executed `gh label create alert ... 2>/dev/null || echo "..."`, swallowing label creation errors. Because fork tokens lack repository label creation permissions (`POST /repos/{owner}/{repo}/labels` returns 403) and `kidding602/agw-workers` only possesses default GitHub labels, `gh issue create --label "alert"` aborted with exit code 1 (`could not add label: 'alert' not found`), suppressing failure alert issues and crashing under `bash -e`.

3. **Fleet Exposure:**  
   Cross-account audit confirmed 100% failure across fleet forks post-quarantine (ACC3 `ngocgminh5-debug`, ACC8 `moradzahra85-png`, ACC6 `mohammadlali0707-stack`, ACC5 `kidding602`).

---

## 2. Incident Timeline & Telemetry

### 2.1 Audited Run Metadata
- **Repository:** `kidding602/agw-workers` (Account 5 Fork) | **Workflow:** `.github/workflows/gate-webhook-health.yml`
- **Run ID:** `42412660214` (Run #246) | **Job ID:** `128523936742` | **Trigger:** `schedule` (`0 * * * *`)
- **Run URL:** `https://github.com/kidding602/agw-workers/actions/runs/42412660214`
- **Milestones (UTC):**
  - **16:16Z:** Pre-quarantine run `42390450990` on `kidding602` succeeds (HTTP 200).
  - **18:36Z:** Findings #1615/#1616 post credential exposure; n8n webhook deactivated.
  - **20:46Z:** Run `42412660214` starts; Step 2 fails (HTTP 404); Step 3 crashes (missing label); job fails.

### 2.2 Step Execution Telemetry Table

| YAML Step | Runner Step | Step Name | Status | Exit Code | Diagnostic Finding |
| :---: | :---: | :--- | :---: | :---: | :--- |
| — | **1** | Set up job | Completed | 0 | Runner initialized |
| **1** | **2** | Check GitHub webhooks are correctly configured | Completed | 0 | Skipped cleanly (requires admin PAT) |
| **2** | **3** | Verify webhook endpoint is reachable | **Failed** | **1** | `❌ n8n endpoint unreachable or wrong (HTTP 404)` |
| **3** | **4** | Post alert if webhook health fails | **Failed** | **1** | `could not add label: 'alert' not found` |
| — | **5** | Complete job | Completed | 0 | Job concluded with failure |

---

## 3. Diagnostic Evidence

### 3.1 Step 2 (Runner Step 3): HTTP 404 Probe Failure
```text
=== Checking n8n endpoint reachability ===
❌ n8n endpoint unreachable or wrong (HTTP 404)
##[error]Process completed with exit code 1.
```

### 3.2 Step 3 (Runner Step 4): Missing Label Alert Crash
```text
label 'alert' already exists (or cannot be created); continuing
could not add label: 'alert' not found
##[error]Process completed with exit code 1.
```

### 3.3 Live Probe & Label Baseline
- **POST Probe (`https://n8n.airboxvip.top/webhook/github-runner-trigger`):** Returns HTTP 404 (`"The requested webhook \"POST github-runner-trigger\" is not registered."`).
- **Target Repository Labels:** `kidding602/agw-workers` possesses only default labels; `alert` is absent.

---

## 4. Root Cause Breakdown

### 4.1 Upstream n8n Webhook Quarantine (#1615/#1616)
- **Quarantine Cause:** Posts #1615 and #1616 by `gh-acc5-direct-acc5` discovered plaintext tokens (Notion PAT, GitHub PAT, Runner Secret) in n8n execution history, without IP allowlisting or HMAC verification.
- **Deactivation:** The production webhook was deactivated pending 5-phase credential rotation.
- **Gate 2 Behavior:** Gate 2 asserts HTTP 200 or 400. Deactivation returns HTTP 404, tripping the failure threshold.

### 4.2 Secondary Alert Tooling Crash & Permission Deficit
- **Error Swallowing:** `gh label create alert ... 2>/dev/null || echo "..."` falsely assumed non-zero return implied label existence.
- **Permission Boundary:** Fork workflows use restricted tokens lacking `repo` admin rights (`POST /repos/{owner}/{repo}/labels` returns 403).
- **Missing Label:** The `alert` label does not exist by default on GitHub repositories.
- **Client Crash:** `gh issue create` validates labels client-side before creating issues. Missing `alert` caused an immediate exit code 1 under `bash -e`, blocking issue creation.

---

## 5. Upstream n8n 5-Phase Rotation Status & Reactivation Criteria

Remediation following #1615/#1616 progresses across 5 distinct phases:

1. **Phase 1: Read-Only Audit & Credential Inventory:** **COMPLETED** (#1615/#1616).
2. **Phase 2: Historical Execution Pruning:** **IN PROGRESS**. Pruning historical payloads via `n8n executions:clean`.
3. **Phase 3: Upstream Ingress Protection:** **PENDING**. Configuring HMAC (`X-Hub-Signature-256`) and GitHub webhook IP CIDR filtering in Nginx.
4. **Phase 4: Coordinated Credential Rotation:** **PENDING**. Synchronized update of Notion PAT, GitHub PATs, and Runner Trigger secret.
5. **Phase 5: Webhook Reactivation & Smoke Testing:** **PENDING**. Re-activating production webhook and executing end-to-end tests.

### Reactivation Criteria & Gate 2 Status
- **Reactivation Prerequisites:** Phases 2, 3, and 4 must complete before the webhook is enabled.
- **Interim Gate 2 Status:** Gate 2 Step 2 is expected-red (HTTP 404) across all fleet accounts until Phase 5 activation.

---

## 6. Fleet-Wide Telemetry Assessment

Audits across all fleet repositories confirm uniform post-quarantine behavior:

| Account | Repository | Audited Run ID | Step 2 (HTTP) | Step 3 (Alert) | Outcome |
| :---: | :--- | :---: | :---: | :---: | :--- |
| **ACC0** | `Mohammadlali/agw-workers` | `42408689708` | 404 (Fail) | Crash (Label) | Failure |
| **ACC1** | `momonakikugava-pixel/agw-workers` | `42407692569` | 404 (Fail) | Crash (Label) | Failure |
| **ACC2** | `lali94m-max/agw-workers` | `42408561020` | 404 (Fail) | Crash (Label) | Failure |
| **ACC3** | `ngocgminh5-debug/agw-workers` | `42401883413` | 404 (Fail) | Crash (Label) | Failure |
| **ACC4** | `hmmletssee7-design/agw-workers` | `42408260500` | 404 (Fail) | Crash (Label) | Failure |
| **ACC5** | `kidding602/agw-workers` | `42412660214` | 404 (Fail) | Crash (Label) | Target Run (Failure) |
| **ACC6** | `mohammadlali0707-stack/agw-workers` | `42408453388` | 404 (Fail) | Crash (Label) | Failure (#1635) |
| **ACC7** | `mohammad97okk/agw-workers` | `42408110897` | 404 (Fail) | Crash (Label) | Failure |
| **ACC8** | `moradzahra85-png/agw-workers` | `42404941144` | 404 (Fail) | Crash (Label) | Failure (#1630) |

---

## 7. Fleet Resilience Recommendations & Concrete Mitigations

To eliminate alert tooling crashes, prevent issue spam during extended quarantine, and guarantee alert delivery across all fleet forks:

1. **Hourly Issue Flooding Mitigation (Deduplication Guard):**  
   Because Gate 2 runs hourly (`0 * * * *`) during extended quarantine, unconditional issue dispatch generates ~192 duplicate issues daily across 8 fleet forks. Step 3 must check for existing open issues using `// empty` and defensive checks to prevent `"null"` false-positive suppression:
   ```bash
   EXISTING_ISSUE=$(gh issue list \
     --repo "$GITHUB_REPOSITORY" \
     --state open \
     --search "Webhook Health Gate Failed" \
     --json number \
     --jq '.[0].number // empty' 2>/dev/null || true)

   if [ -n "$EXISTING_ISSUE" ] && [ "$EXISTING_ISSUE" != "null" ]; then
     echo "::notice::Open alert issue #${EXISTING_ISSUE} already exists; skipping duplicate dispatch"
     exit 0
   fi
   ```

2. **Two-Tier Resilient Alert Dispatch:**  
   Update Step 3 in `.github/workflows/gate-webhook-health.yml` to attempt issue dispatch with `--label "alert"`, and if label assignment fails (due to missing label or fork permission boundaries), immediately dispatch unlabelled:
   ```bash
   if ! gh issue create --repo "$GITHUB_REPOSITORY" --title "$ISSUE_TITLE" --body "$ISSUE_BODY" --label "alert"; then
     echo "::warning::Failed to create issue with '--label alert'; falling back to unlabelled issue creation"
     gh issue create --repo "$GITHUB_REPOSITORY" --title "$ISSUE_TITLE" --body "$ISSUE_BODY"
   fi
   ```

3. **CWE-78 Command Injection Elimination:**  
   Pass dynamic parameters (`GITHUB_SERVER_URL`, `GITHUB_REPOSITORY`, `GITHUB_RUN_ID`, `ISSUE_TITLE`, `ISSUE_BODY`) via runner environment variables rather than direct shell string interpolation of `${{ github.* }}` expressions into `gh` commands. Strictly quote all variable expansions (`"$ISSUE_TITLE"`, `"$ISSUE_BODY"`, `"$GITHUB_REPOSITORY"`) to prevent shell injection or parameter splitting.

4. **Fleet-Wide Repository Label Pre-Provisioning:**  
   Pre-create the `alert` label (color `#B60205`, description `Automated gate failure needing a human`) across all 8 fleet fork repositories via admin scripts so missing labels never cause a single point of failure.

5. **CI Step Error Handling & Failure Isolation:**  
   Add `continue-on-error: true` (or shell error trapping) to the alert step so notification tooling anomalies never compound workflow failure status or obscure underlying gate diagnostics.

6. **Governance Conformance & Patch Reference:**  
   The core resilient dispatch patch is stored in `Reports/audit/gate-webhook-health-resilience.patch` and documented in `Reports/audit/gate-webhook-health-resilience-proposal.md`, verified clean with `git apply --check` (exit code 0).

---

## 8. Conclusion

The failure of Gate 2 on `kidding602/agw-workers` (run `42412660214`) is fully isolated:
- Step 2 in YAML (Runner Step 3) failed legitimately due to intentional n8n webhook quarantine (#1615/#1616).
- Step 3 in YAML (Runner Step 4) crashed due to unhandled missing labels in `gh issue create`.
Implementing the two-tier resilient fallback, deduplication guard (with `jq // empty` protection), and CWE-78 hardening eliminates the alert SPOF, prevents notification flooding during quarantine, and restores full fleet observability.
