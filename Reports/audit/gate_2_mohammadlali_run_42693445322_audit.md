# Forensic CI Audit Report: Gate 2 Webhook Health Failure (Mohammadlali)

**Document ID:** `AUDIT-GATE2-ACC0-2026-11-09-RUN-42693445322`  
**Auditor / Agent:** `agw-ACC0-w02` (Mohammad's GitHub Worker Fleet -- Account 0 Lead)  
**Target Repository:** `Mohammadlali/agw-workers` (Account 0 Primary Repo / Fleet Lead)  
**Audited Run ID:** `42693445322` ('Gate 2 -- Webhook Health Checker', Scheduled Hourly Run)  
**Workflow File:** `.github/workflows/gate-webhook-health.yml`  
**Job ID:** `129418954312` (`webhook-health`)  
**Head SHA:** `37fc856fcaa5e9975ade1f93539ddd31a0dae2ff`  
**Run URL:** `https://github.com/Mohammadlali/agw-workers/actions/runs/42693445322`  
**Audit Date:** 2026-11-09  
**Classification:** Upstream Security Quarantine & Secondary CI Alert Tooling Crash  
**Status:** Forensic Audit Completed, Root Causes Isolated, Remediation & Sync Plan Delivered  

---

## 1. Executive Summary

On 2026-11-09 at 20:29:29Z, scheduled CI run **42693445322** on `Mohammadlali/agw-workers` (Account 0) failed during **Gate 2 (`Gate 2 -- Webhook Health Checker`)**.

A two-stage failure chain occurred, mirroring earlier fleet fork failures (ACC8 `42522282293`, ACC5 `42412660214`):

1. **Primary Failure (Step 3 / YAML Step 2):**  
   The HTTP POST probe to `https://n8n.airboxvip.top/webhook/github-runner-trigger` failed with **HTTP 404 Not Found** (`The requested webhook "POST github-runner-trigger" is not registered`). This deactivation resulted directly from upstream security incident #1615/#1616 (plaintext credentials detected in n8n execution history, placing the webhook in quarantine pending 5-phase credential rotation and HMAC hardening).
2. **Secondary Failure (Step 4 / YAML Step 3):**  
   Under `if: failure()`, alert notification crashed with exit code 1:
   - **Permission Deficit:** The job omitted an explicit `permissions:` block. GitHub Actions assigned a default token with `Contents: read`, `Metadata: read`, and `Packages: read` (`issues: write` absent).
   - **Label Barrier:** The repository lacked the `alert` label. Attempting `gh label create alert ...` failed due to lack of repository administrative rights (`POST /repos/{owner}/{repo}/labels` returned HTTP 403), masked by `2>/dev/null || echo ...`. When `gh issue create ... --label "alert"` ran, the GitHub CLI aborted with `could not add label: 'alert' not found`.
   - **Tooling Crash:** Running under `bash -e` without unlabelled fallback or `continue-on-error: true`, the step crashed CI without alerting.
3. **Sync Block Context:**  
   Resilience patch #1701 (`d4186eb`) resolved this failure mode on `mohammadlali0707-stack/agw-workers`, but push to `Mohammadlali/agw-workers` was blocked by `hq-warm.yml` (`contents: read`) token permissions (#1703–#1705), leaving `Mohammadlali/agw-workers` running unpatched commit `37fc856fcaa5e9975ade1f93539ddd31a0dae2ff`.

---

## 2. Incident Timeline & Telemetry

### 2.1 Audited Run Metadata
- **Repository:** `Mohammadlali/agw-workers` (Account 0) | **Workflow:** `.github/workflows/gate-webhook-health.yml`
- **Run ID:** `42693445322` | **Job ID:** `129418954312` | **Trigger:** `schedule` (`0 * * * *`)
- **Head SHA:** `37fc856fcaa5e9975ade1f93539ddd31a0dae2ff`
- **Execution Telemetry (UTC -- 2026-11-09):**
  - **20:29:29Z:** Workflow triggered via scheduled hourly cron.
  - **20:29:33Z:** Job `webhook-health` assigned to runner.
  - **20:29:34.093Z:** Step 1 (`Set up job`) records `GITHUB_TOKEN` permissions: `Contents: read`, `Metadata: read`, `Packages: read` (`issues: write` absent).
  - **20:29:34.399Z:** Step 2 (`Check GitHub webhooks are correctly configured`) runs; `gh api repos/Mohammadlali/agw-workers/hooks` encounters HTTP 404/403 due to missing webhook admin scope. Emits `##TBS##` skip record and exits 0 cleanly.
  - **20:29:34.858Z:** Step 3 (`Verify webhook endpoint is reachable`) executes via `if: always()`. POST to `https://n8n.airboxvip.top/webhook/github-runner-trigger` returns HTTP 404.
  - **20:29:35.503Z:** Step 3 outputs `❌ n8n endpoint unreachable or wrong (HTTP 404)` and exits with code 1.
  - **20:29:35.515Z:** Step 4 (`Post alert if webhook health fails`) triggers under `if: failure()`.
  - **20:29:35.807Z:** `gh label create alert` fails (HTTP 403); outputs `label 'alert' already exists (or cannot be created); continuing`.
  - **20:29:36.288Z:** `gh issue create ... --label "alert"` terminates with `could not add label: 'alert' not found` (exit code 1).
  - **20:29:36.304Z:** Step 5 (`Complete job`) executes cleanup. Job terminates with status `failure`. Duration: 5s.
  - **20:29:39Z:** GitHub Actions concludes run `42693445322` as `completed: failure`.

### 2.2 Step Execution Table

| Runner Step | YAML Step Name | Status | Exit Code | Diagnostic Log Finding |
| :---: | :--- | :---: | :---: | :--- |
| **1** | Set up job | Completed | 0 | `GITHUB_TOKEN` scoped to `Contents: read`, `Metadata: read`, `Packages: read` |
| **2** | Check GitHub webhooks are correctly configured | Completed | 0 | Skipped: `cannot read this repo's webhooks -- gh: Not Found (HTTP 404)` |
| **3** | Verify webhook endpoint is reachable | **Failed** | **1** | `❌ n8n endpoint unreachable or wrong (HTTP 404)` |
| **4** | Post alert if webhook health fails | **Failed** | **1** | `could not add label: 'alert' not found` |
| **5** | Complete job | Completed | 0 | Cleanup processes; job conclusion recorded as `failure` |

---

## 3. Diagnostic Evidence

### 3.1 Step 1 Log: Token Permission Deficit
```text
2026-11-09T20:29:34.0937762Z ##[group]GITHUB_TOKEN Permissions
2026-11-09T20:29:34.0940215Z Contents: read
2026-11-09T20:29:34.0941009Z Metadata: read
2026-11-09T20:29:34.0941602Z Packages: read
2026-11-09T20:29:34.0942287Z ##[endgroup]
```

### 3.2 Step 3 Log: Primary Failure (HTTP 404 Probe)
```text
2026-11-09T20:29:34.8671198Z === Checking n8n endpoint reachability ===
2026-11-09T20:29:35.5035760Z ❌ n8n endpoint unreachable or wrong (HTTP 404)
2026-11-09T20:29:35.5042318Z ##[error]Process completed with exit code 1.
```

### 3.3 Step 4 Log: Secondary Failure (Alert Step Crash)
```text
2026-11-09T20:29:35.8074703Z label 'alert' already exists (or cannot be created); continuing
2026-11-09T20:29:36.2882366Z could not add label: 'alert' not found
2026-11-09T20:29:36.2919641Z ##[error]Process completed with exit code 1.
```

### 3.4 Live Endpoint & Target Repository State
- **Live Endpoint Probe:** `POST https://n8n.airboxvip.top/webhook/github-runner-trigger` returns HTTP 404 (`{"code":404,"message":"The requested webhook \"POST github-runner-trigger\" is not registered."}`).
- **Repository Labels:** `GET /repos/Mohammadlali/agw-workers/labels` lists 15 default labels; `alert` is absent.
- **Repository Issues:** `GET /repos/Mohammadlali/agw-workers/issues` confirms 0 alert issues were created.

---

## 4. Root Cause Breakdown

### 4.1 Primary Failure: Upstream Security Quarantine (#1615/#1616)
Findings #1615 and #1616 by `gh-acc5-direct-acc5` detected plaintext credentials (Notion PAT, GitHub PATs, Runner Secret) in n8n execution data without IP allowlisting or HMAC payload verification. The production webhook was deactivated pending rotation. Gate 2 asserts HTTP 200/400; receiving HTTP 404 trips assertion failure.

### 4.2 Secondary Failure: Alert Step Crash & Permission Barriers
- **Permission Deficit:** The workflow omitted `permissions: issues: write`.
- **Swallowed Error & Strict Label Dependency:** `gh label create alert` failed with HTTP 403 (requires repo admin scope), masked by `2>/dev/null || echo`. When `gh issue create ... --label "alert"` ran, the GitHub CLI aborted because `alert` did not exist.
- **No Fallback & Bash `-e` Crash:** The step lacked fallback to unlabelled issue dispatch and lacked `continue-on-error: true`, causing the alert tooling to crash CI.

---

## 5. Upstream n8n Quarantine 5-Phase Tracking Matrix

| Phase | Milestone Description | Owner | Status | Gate 2 Behavior |
| :---: | :--- | :---: | :---: | :--- |
| **Phase 1** | Credential Inventory & Threat Isolation | `gh-acc5-direct-acc5` | **Completed** (#1615/#1616) | Endpoint quarantined |
| **Phase 2** | Historical Execution Purge (`n8n executions:clean`) | Security Lead | **In Progress** | Endpoint returns HTTP 404 |
| **Phase 3** | Ingress Hardening (HMAC SHA-256 + Nginx CIDR) | DevOps / Infra | **Pending** | Webhook remains offline |
| **Phase 4** | Coordinated Credential Rotation (PATs & Secrets) | Fleet Lead (ACC0) | **Pending** | Re-registration staging |
| **Phase 5** | Production Reactivation & E2E Verification | Fleet Lead | **Pending** | Gate 2 returns HTTP 200 (Green) |

---

## 6. Workflow Remediation Specification

To permanently decouple CI stability from alert dispatch and eliminate alert crashes, `.github/workflows/gate-webhook-health.yml` requires the following concrete remediation:

```diff
--- a/.github/workflows/gate-webhook-health.yml
+++ b/.github/workflows/gate-webhook-health.yml
@@ -9,6 +9,9 @@
 jobs:
   webhook-health:
     runs-on: ubuntu-latest
+    permissions:
+      issues: write
+      contents: read
     env:
       GH_TOKEN: ${{ secrets.ACC6_PAT || secrets.GITHUB_TOKEN }}
@@ -143,6 +146,7 @@
       - name: Post alert if webhook health fails
         if: failure()
+        continue-on-error: true
         run: |
-          gh label create alert ... || echo "..."
-          gh issue create ... --label "alert"
+          export ISSUE_TITLE="🚨 Webhook Health Gate Failed"
+          EXISTING=$(gh issue list --repo "$GITHUB_REPOSITORY" --state open \
+            --json number,title --jq '.[] | select(.title == env.ISSUE_TITLE) | .number' 2>/dev/null | head -n 1 || true)
+          if [ -n "$EXISTING" ]; then
+            echo "Open alert #$EXISTING already exists; skipping duplicate"
+            exit 0
+          fi
+          gh issue create --repo "$GITHUB_REPOSITORY" --title "$ISSUE_TITLE" \
+            --body-file /tmp/issue_body.md --label "alert" 2>/dev/null \
+            || gh issue create --repo "$GITHUB_REPOSITORY" --title "$ISSUE_TITLE" \
+              --body-file /tmp/issue_body.md
```

---

## 7. Sync Block Resolution & Action Items

### 7.1 Resolving the Fork Synchronization Barrier
Board posts #1703–#1705 revealed that local patch `36a7825` could not be pushed to `Mohammadlali/agw-workers` because runner instances spawned by `hq-warm.yml` carry default tokens scoped strictly to `contents: read`.

**Actionable Deployment Procedures:**
1. **Option A (Administrative Dispatch Sync):** Dispatch a synchronization workflow from control repository `mohammadlali0707-stack/agw-workers` targeting `Mohammadlali/agw-workers` using an administrative PAT with `repo` and `workflow` scopes.
2. **Option B (Pull Request Fast-Forward):** Open a pull request from a contributor branch carrying commit `d4186eb` to `Mohammadlali/agw-workers:main` and merge via repository maintainer credentials.

### 7.2 Label Provisioning Plan
- **Administrative Provisioning:** Execute a one-time API provisioning using maintainer credentials to create the `alert` label (`color: B60205`, `description: "Automated gate failure needing a human"`) across `Mohammadlali/agw-workers` and all 8 fleet forks.
- **Resilient Fallback:** Ensure all workflows implement the unlabelled issue creation fallback so CI never crashes if a label is missing or inaccessible.
