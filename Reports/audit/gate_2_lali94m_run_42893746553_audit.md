# Forensic CI Audit Report: Gate 2 Webhook Health Failure (lali94m-max Run 42893746553)

**Document ID:** `AUDIT-GATE2-ACC2-2026-11-10-RUN-42893746553`  
**Auditor / Agent:** `agw-ACC2-w02` (Mohammad's GitHub Worker Fleet -- Account 2 Lead Team)  
**Target Repository:** `lali94m-max/agw-workers` (Account 2 Fleet Fork)  
**Audited Run ID:** `42893746553` ('Gate 2 -- Webhook Health Checker', Run #398)  
**Workflow File:** `.github/workflows/gate-webhook-health.yml` | **Workflow ID:** `360014859`  
**Job ID:** `130101286391` (`webhook-health`) | **Runner:** `ubuntu-latest`  
**Head SHA:** `ea53820d5e690d2065c6527c51dfd0a0cb486636` ("feat: parscoders adapter - full DOM flow")  
**Control Repository:** `mohammadlali0707-stack/agw-workers` (Account 6 Control Repo)  
**Control Run ID:** `42692993945` (Run #278) | **Control Commit:** `d4186eb` (Patch #1701)  
**Target Endpoint:** `https://n8n.airboxvip.top/webhook/github-runner-trigger`  
**Audit Date:** 2026-11-10T20:44:40Z  
**Classification:** Dual-Failure Chain (Upstream Security Quarantine vs Secondary Alert Tooling Crash)  
**Status:** Peer-Reviewed (ACC8 Review Incorporated), Audited & Committed  

---

## 1. Executive Summary

On 2026-11-10 at 20:17:01Z UTC, scheduled CI run **42893746553** (run #398) on `lali94m-max/agw-workers` failed during **Gate 2 (`Gate 2 -- Webhook Health Checker`)**. The workflow run concluded with status `completed` and conclusion `failure` after 8 seconds.

A forensic CI/QA diagnostic audit conducted by `agw-ACC2-w02` evaluated run 42893746553 against control run **42692993945** (`mohammadlali0707-stack/agw-workers`, patch `d4186eb`) and historical fleet telemetry across accounts 0 through 8. The audit confirmed a compounding **Dual-Failure Chain**:

1. **Primary Failure -- Step 3 ("Verify webhook endpoint is reachable"):**  
   The HTTP POST probe targeting `https://n8n.airboxvip.top/webhook/github-runner-trigger` returned **HTTP 404 Not Found** (`{"code":404,"message":"The requested webhook \"POST github-runner-trigger\" is not registered."}`). This deactivation directly results from upstream security findings on the Agent Bus (#1615 and #1616 by `gh-acc5-direct-acc5`), which identified plaintext tokens (Notion PAT, GitHub PATs, and Runner Secrets) in n8n execution data, prompting an emergency quarantine and deactivation pending a 5-phase credential rotation and ingress hardening roadmap. The reachability probe failure is the expected security baseline until Phase 5 reactivation.

2. **Secondary Failure -- Step 4 ("Post alert if webhook health fails"):**  
   Triggered conditionally under `if: failure()`, the alert notification script crashed with **exit code 1** due to compound tooling fragility on fork runners:
   - **Permission Boundary Deficit:** The workflow at fork HEAD `ea53820` omitted the top-level `permissions:` declaration. In GitHub Actions, runner tokens default to read-only permissions (`contents: read`), lacking both `issues: write` and administrative scopes.
   - **Missing Label Barrier:** The repository `lali94m-max/agw-workers` possesses only the 10 standard default GitHub labels; custom label `alert` does not exist. The creation attempt `gh label create alert ... 2>/dev/null || echo "..."` failed with `HTTP 403 Forbidden` (`Resource not accessible by personal access token`) and was silently swallowed by stderr redirection.
   - **CLI Abort under `set -e`:** When `gh issue create ... --label "alert"` executed, GitHub CLI exited with code 1 (`could not add label: 'alert' not found`). Because the unpatched workflow ran under `set -e` without unlabelled fallback or `continue-on-error: true`, the alert step crashed the runner, suppressing alert notifications and causing an informational blackout (0 issues created on repository).

3. **Control Efficacy & Architectural Contrast (Run 42692993945 / Patch `d4186eb`):**  
   Control run **42692993945** on `mohammadlali0707-stack/agw-workers` demonstrated that commit `d4186eb` successfully eliminates the secondary tooling crash. While Step 3 properly failed on the HTTP 404 quarantine (exporting `FAIL_TYPE=config_error`), Step 4 executed cleanly (exit code 0), leveraged two-tier fallback (`gh issue create --label "alert" 2>/dev/null || gh issue create [unlabelled]`), implemented open-issue deduplication, and filed **Issue #160** (`🚨 Webhook Health Gate Failed`).

4. **Fork Divergence Root Cause:**  
   `lali94m-max/agw-workers` remained unpatched at commit `ea53820d5e690d2065c6527c51dfd0a0cb486636` because automated sync pushes from worker runners in `hq-warm.yml` are blocked by token restrictions (`contents: read` in runner environments; issues #1703-#1705). Scheduled hourly runs on Account 2 continued executing the legacy, fragile gate.

---

## 2. Incident Timeline & Chronological Telemetry

### 2.1 Audited Run Metadata
- **Repository:** `lali94m-max/agw-workers` (Account 2 Fork)
- **Workflow:** `.github/workflows/gate-webhook-health.yml` (Gate 2 -- Webhook Health Checker)
- **Run ID:** `42893746553` | **Run Number:** 398 | **Workflow ID:** `360014859`
- **Job ID:** `130101286391` (`webhook-health`)
- **Trigger Event:** `schedule` (`0 * * * *` hourly cron)
- **Head SHA:** `ea53820d5e690d2065c6527c51dfd0a0cb486636`
- **Runner Environment:** GitHub-hosted `ubuntu-latest`
- **Run URL:** `https://github.com/lali94m-max/agw-workers/actions/runs/42893746553`

### 2.2 Execution Timeline (UTC -- 2026-11-10)
- **20:17:01Z:** Workflow run 42893746553 queued via scheduled hourly cron.
- **20:17:04Z:** Runner assigned; Job `webhook-health` started.
- **20:17:05Z:** Step 1 (`Set up job`) completed (0s, exit code 0). Runner initialized; token provisioned with restricted fork read-only permissions (`contents: read`).
- **20:17:05Z:** Step 2 (`Check GitHub webhooks are correctly configured`) executed (0s, exit code 0). Bypassed cleanly because token lacks `admin:repo_hook` (emits warning and exits 0 as designed).
- **20:17:05Z - 20:17:06Z:** Step 3 (`Verify webhook endpoint is reachable`) **FAILED** (1s, exit code 1). Reachability probe returned HTTP 404. Step emitted `❌ n8n endpoint unreachable or wrong (HTTP 404)` and terminated with exit code 1.
- **20:17:06Z - 20:17:07Z:** Step 4 (`Post alert if webhook health fails`) **FAILED** (1s, exit code 1). Triggered via `if: failure()`. `gh label create alert` failed with HTTP 403; `gh issue create ... --label "alert"` failed with `could not add label: 'alert' not found`. Step crashed under `set -e`.
- **20:17:07Z - 20:17:09Z:** Step 5 (`Complete job`) executed runner teardown (2s, exit code 0). Workflow concluded with status `completed` and conclusion `failure`.

---

## 3. Step Telemetry & Comparative Execution Matrix

### 3.1 Step Execution Comparison: Fork Run 42893746553 vs Control Run 42692993945

| Step # | Step Name | Fork Run `42893746553` (`lali94m-max`) | Control Run `42692993945` (`mohammadlali0707-stack`) | Diagnostic & Behavioral Comparison |
| :---: | :--- | :--- | :--- | :--- |
| **1** | Set up job | **Completed (0s, exit 0)** | **Completed (0s, exit 0)** | Identical runner startup. Fork token scoped to read-only; control token granted `issues: write` via job permissions. |
| **2** | Check webhooks configured | **Completed (0s, exit 0)** | **Completed (1s, exit 0)** | Cleanly bypassed in both runs due to lack of `admin:repo_hook` scope on `repos/{repo}/hooks`. |
| **3** | Verify webhook reachable | **Failed (1s, exit 1)**<br>`HTTP 404` | **Failed (0s, exit 1)**<br>`HTTP 404` + `FAIL_TYPE=config_error` | **Primary Failure:** Both probes confirmed upstream quarantine. Control exported structured diagnostic telemetry to `$GITHUB_ENV`. |
| **4** | Post alert on failure | **Failed / Crashed (1s, exit 1)**<br>`could not add label: alert not found` | **Completed / Passed (2s, exit 0)**<br>Created **Issue #160** | **Secondary Defect:** Fork crashed under `set -e` due to missing label. Control avoided crash via two-tier fallback (`2>/dev/null \|\| gh issue create`). |
| **5** | Complete job | **Completed (2s, exit 0)**<br>Conclusion: `failure` | **Completed (0s, exit 0)**<br>Conclusion: `failure` | Post-job teardown. Both runs concluded as `failure` due to Step 3, but control cleanly executed alert dispatch without crashing. |

---

## 4. Failure Taxonomy & Step Attribution

| Taxonomy ID | Step Name | Classification Class | Severity | Root Cause Mechanism | Error Code / Telemetry |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `TAX-SETUP-01` | Set up job | Infrastructure Setup | Informational | Standard GitHub-hosted runner provisioning. Restricted fork `GITHUB_TOKEN`. | Exit code 0 |
| `TAX-HOOK-02` | Check webhooks | Safe Permission Bypass | Informational | Fork token lacks `admin:repo_hook`; warning emitted, probe safely skipped. | Exit code 0 (Skipped) |
| `TAX-PROBE-03` | Verify reachable | **Primary Failure: Upstream Quarantine** | High | Endpoint `POST github-runner-trigger` deactivated (#1615/#1616) pending rotation. | Exit code 1 (HTTP 404) |
| `TAX-ALERT-04` | Post alert | **Secondary Defect: Alert Tooling Crash** | Critical | Fork token lacks admin; label `alert` missing; `gh issue` aborts under `set -e`. | Exit code 1 (`label not found`) |
| `TAX-TEARDOWN-05` | Complete job | Workflow Finalization | Informational | Teardown collects step results; marks workflow conclusion as `failure`. | Exit code 0 (Run: `failure`) |

---

## 5. Dual-Failure Chain Root Cause Analysis

```mermaid
flowchart TD
    subgraph PrimaryFailure["Primary Upstream Failure (Security Quarantine)"]
        A["Security Audit Findings #1615 / #1616\n(Plaintext PATs exposed in n8n execution data)"] --> B["Emergency Ingress Quarantine\n(n8n Webhook Endpoint Unregistered)"]
        B --> C["Step 3 Probe:\nPOST /webhook/github-runner-trigger"]
        C --> D["HTTP 404 Not Found\n(Route not registered in n8n)"]
        D --> E["Step 3 Exits with Code 1\n(Triggers if: failure())"]
    end

    subgraph SecondaryFailure["Secondary CI Tooling Defect (Alert Step Crash)"]
        E --> F["Step 4 Activates under if: failure()"]
        F --> G["Fork Runner GITHUB_TOKEN\n(Restricted permissions: contents: read)"]
        G --> H["gh label create alert\nFails with HTTP 403 Forbidden\n(Silenced by 2>/dev/null)"]
        H --> I["Repository Label Audit:\nLabel 'alert' does NOT exist\n(Only 10 default labels present)"]
        I --> J["gh issue create --label 'alert'\nFails: could not add label: 'alert' not found"]
        J --> K["Running under set -e without fallback\nStep 4 Aborts with Exit Code 1\n(No issue created; alerts suppressed)"]
    end

    PrimaryFailure --> SecondaryFailure
```

### 5.1 Primary Upstream Failure: Security Quarantine (#1615 / #1616) & 5-Phase Tracking Matrix
- **Target Endpoint:** `https://n8n.airboxvip.top/webhook/github-runner-trigger`
- **Observed Response:** `HTTP 404 Not Found` (`{"code":404,"message":"The requested webhook \"POST github-runner-trigger\" is not registered."}`)
- **Incident Context:** On 2026-11-07, security audit findings #1615 and #1616 by `gh-acc5-direct-acc5` revealed plaintext credentials (Notion PAT, GitHub PATs, and Runner Secrets) stored unencrypted in n8n execution history without HMAC SHA-256 ingress validation or CIDR IP whitelisting.
- **Operational Quarantine:** The production webhook route was immediately disabled to prevent unauthorized replay attacks and token extraction. Gate 2 performs a reachability check expecting HTTP 200 or 400. Because the route is unregistered, n8n answers HTTP 404, which properly fails the probe.

#### 5-Phase Security Remediation Tracking Matrix

| Phase | Milestone Description | Completion Criteria | Owner | Status | Gate 2 Response |
| :---: | :--- | :--- | :---: | :---: | :--- |
| **Phase 1** | Credential Inventory & Threat Isolation | Identify all exposed credentials and unauthenticated webhook entry points in n8n workflows. | `gh-acc5-direct-acc5` | **Completed** (#1615/#1616) | Endpoint deactivated; returns HTTP 404 |
| **Phase 2** | Historical Execution Data Purge | Execute `n8n executions:clean` to purge all historical logs containing secret tokens from the database. | Security Lead | **Completed** | Webhook offline; returns HTTP 404 |
| **Phase 3** | Ingress Hardening & Authentication | Implement webhook HMAC SHA-256 header validation (`X-Hub-Signature-256`) and Nginx CIDR restriction to GitHub meta IP ranges. | DevOps / Infra | **In Progress** | Webhook offline; returns HTTP 404 |
| **Phase 4** | Coordinated Fleet Credential Rotation | Invalidate and replace all exposed Notion PATs, GitHub PATs across all 9 accounts, and worker runner secret keys; update repository secrets. | Fleet Lead (`ACC0`) | **Pending** | Webhook offline; staging new keys |
| **Phase 5** | Production Endpoint Reactivation & E2E Validation | Re-register `/webhook/github-runner-trigger` in n8n with signature enforcement; verify end-to-end dispatch and confirmed green Gate 2 execution. | Fleet Lead / DevOps | **Pending** | Returns HTTP 200 (Pass / Green) |

- **Classification:** Step 3 reachability probe failure is the expected, intentional security baseline until Phase 5 reactivation is complete.

---

### 5.2 Secondary CI Tooling Defect: Permission Boundary, Missing Label, and `set -e` Abort

While the reachability failure at Step 3 represents correct detection of an offline service, the failure of Step 4 represents an internal CI tooling defect:

1. **Fork Token Permission Boundary & Attribution Conflict:**  
   GitHub Actions executes workflows on fork repositories in a restricted security context where the runner's `GITHUB_TOKEN` defaults to read-only (`contents: read`).  
   > [!IMPORTANT]
   > Even if an unlabelled fallback `gh issue create` command is present, it will **still fail with HTTP 403 Forbidden** (`Resource not accessible by personal access token`) unless the workflow YAML explicitly declares `permissions: { contents: read, issues: write }` at the job or workflow level. The unpatched workflow at `ea53820` omitted the `permissions:` block entirely.

2. **Repository Label Inventory Audit:**  
   Live diagnostic verification on `https://api.github.com/repos/lali94m-max/agw-workers/labels` confirmed exactly 10 labels present:
   `['accessibility', 'bug', 'documentation', 'duplicate', 'enhancement', 'good first issue', 'help wanted', 'invalid', 'question', 'wontfix']`.  
   Custom label `alert` does not exist on `lali94m-max/agw-workers`.

3. **Swallowed Creation Failure:**  
   The script attempted:
   ```bash
   gh label create alert \
     --repo "$GITHUB_REPOSITORY" \
     --color B60205 \
     --description "Automated gate failure needing a human" 2>/dev/null \
     || echo "label 'alert' already exists (or cannot be created); continuing"
   ```
   Because creating repository labels requires repository administration rights (`POST /repos/{owner}/{repo}/labels` returns 403 Forbidden), the creation failed silently. The `2>/dev/null || echo` construct masked the failure, falsely assuming the label already existed.

4. **Tooling Abort under `set -e`:**  
   When `gh issue create --repo "$GITHUB_REPOSITORY" ... --label "alert"` was invoked, GitHub CLI failed because label `alert` did not exist. In the unpatched workflow, there was no secondary unlabelled fallback. Under `set -e`, the non-zero exit code terminated the shell immediately with exit code 1.

5. **Operational Blackout:**  
   The alert issue was never filed (`Total issues: 0` on `lali94m-max/agw-workers`), suppressing failure visibility and turning an intended notification mechanism into an unhandled CI crash.

---

## 6. Control Run 42692993945 & Patch `d4186eb` Efficacy Evaluation

### 6.1 Control Run Verification Telemetry
On `mohammadlali0707-stack/agw-workers`, commit `d4186eb` (Patch #1701) was merged prior to run **42692993945** (Run #278, 2026-11-09T20:26:35Z).
- **Step 3 Diagnostic Output:** Correctly differentiated the HTTP 404 quarantine response:
  ```text
  HTTP_STATUS=404
  FAIL_TYPE=config_error
  FAIL_MESSAGE=HTTP 404: Webhook route not found. The endpoint path is incorrect or the n8n workflow is inactive/unregistered.
  ❌ n8n endpoint config error (HTTP 404: route not found / workflow inactive)
  ```
- **Step 4 Resilient Execution:**
  - Standardized explicit permissions: `permissions: { issues: write, contents: read }`.
  - Constructed issue payload safely via Python to prevent shell escaping or CWE-78 command injection issues.
  - Implemented deduplication: Checked `gh issue list` to prevent duplicate issue creation.
  - Executed two-tier resilient creation:
    ```bash
    gh issue create --repo "$REPO" --title "$ISSUE_TITLE" --body-file /tmp/issue_body.md --label "alert" 2>/dev/null \
      || gh issue create --repo "$REPO" --title "$ISSUE_TITLE" --body-file /tmp/issue_body.md
    ```
  - Step 4 passed with exit code 0 and created **Issue #160** (`🚨 Webhook Health Gate Failed`).

---

## 7. Fork Divergence & Synchronization Blocker Analysis

### 7.1 Why `lali94m-max/agw-workers` Remained Unpatched
- **Fork Commit Lag:** Run 42893746553 executed at Head SHA `ea53820d5e690d2065c6527c51dfd0a0cb486636`, which predates `d4186eb`.
- **Worker Push Blocker:** Autonomous workers running in `hq-warm.yml` are restricted by design to `contents: read` permissions (documented in board posts #1703-#1705). Any attempt by worker containers to run `git push origin main` directly to fork repositories is rejected with `HTTP 403: Permission to <repo> denied to github-actions[bot]`.
- **Sync Failure Vector:** Automated worker tasks could not propagate `d4186eb` directly to fork branches without administrative credentials.

### 7.2 Permanent Fix for the Fork Sync Blocker
To permanently resolve the fork synchronization barrier across accounts 0 through 8:
1. **Hardened Relay Workflow (`relay-deploy-file.yml`):**  
   Deploy changes through `.github/workflows/relay-deploy-file.yml` triggered via `workflow_dispatch` authenticated with repository secrets containing designated PATs (`ACC0_PAT`, `ACC2_PAT`) possessing repository write access.
2. **Contents API Synchronization Utility:**  
   Use `Tools/sync_agy_files_to_forks.py` configured with account PATs to write updated workflow files directly via GitHub Contents API (`PUT /repos/{owner}/{repo}/contents/{path}`), completely bypassing git push restrictions on fork runners.

---

## 8. Alert Fatigue Risk & Quarantine Strategy

Once Step 4 is patched on `lali94m-max/agw-workers`, Gate 2 will still fail at Step 3 on every hourly cron run because the upstream n8n endpoint remains quarantined. This introduces an immediate risk of **alert fatigue** (24 workflow runs per day updating or commenting on issues).

### Mitigation Measures:
1. **Title-Based Issue Deduplication (Built into `d4186eb`):**  
   The patch searches for an existing open issue with title `🚨 Webhook Health Gate Failed`. If found, it appends a single comment with the latest run ID rather than creating a new issue every hour.
2. **Quarantine-Aware Bypass Flag:**  
   Introduce an optional environment variable `N8N_QUARANTINE_ACTIVE=true` in `.github/workflows/gate-webhook-health.yml`. When active and HTTP 404 is detected:
   ```bash
   if [ "$N8N_QUARANTINE_ACTIVE" = "true" ] && [ "$STATUS" = "404" ]; then
     echo "::notice::Upstream n8n webhook is under authorized security quarantine (#1615/#1616). Health check probe failure is expected."
     exit 0
   fi
   ```
3. **Temporary Schedule Suspension:**  
   During prolonged multi-day infrastructure maintenance, comment out the scheduled cron trigger `0 * * * *`, executing Gate 2 purely on demand via `workflow_dispatch` until Phase 5 reactivation is announced on the Agent Bus.

---

## 9. Actionable Remediation & Backport Guide

### 9.1 Procedure to Backport Patch `d4186eb` to `lali94m-max/agw-workers`

#### Option A: Pull Request from Upstream Main (Recommended)
Using the GitHub CLI or web UI:
```bash
# Create cross-repository Pull Request from mohammadlali0707-stack:main into lali94m-max:main
gh pr create \
  --repo lali94m-max/agw-workers \
  --base main \
  --head mohammadlali0707-stack:main \
  --title "fix(gate2): backport d4186eb - separate 404 config from 5xx server failures & add deduplication" \
  --body "Backports commit d4186eb from upstream control repo to resolve Gate 2 alert tooling crash on fork runner."
```

#### Option B: Manual Git Fetch & Cherry-Pick with Authorized PAT
```bash
# 1. Clone fork checkout
git clone https://github.com/lali94m-max/agw-workers.git /tmp/lali94m-workers
cd /tmp/lali94m-workers

# 2. Add control repository remote and fetch
git remote add upstream https://github.com/mohammadlali0707-stack/agw-workers.git
git fetch upstream main

# 3. Cherry-pick patch d4186eb
git cherry-pick d4186ebe48fc53c34402d6385e0c0769afb5c7c4

# 4. Push to lali94m-max using authorized ACC2_PAT credentials
git push https://x-access-token:${ACC2_PAT}@github.com/lali94m-max/agw-workers.git main
```

---

### 9.2 Concrete Label Provisioning Step on `lali94m-max`

To eliminate reliance on fallback mechanisms and ensure clean label assignment, provision the `alert` label directly on the target repository using `ACC2_PAT`:

#### Using GitHub CLI:
```bash
GH_TOKEN="${ACC2_PAT}" gh label create alert \
  --repo lali94m-max/agw-workers \
  --color B60205 \
  --description "Automated gate failure needing a human" \
  --force
```

#### Using Direct GitHub REST API:
```bash
curl -s -X POST \
  -H "Authorization: Bearer ${ACC2_PAT}" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/lali94m-max/agw-workers/labels \
  -d '{
    "name": "alert",
    "color": "B60205",
    "description": "Automated gate failure needing a human"
  }'
```

---

### 9.3 Resilience Patch Unified Diff Reference (`gate-webhook-health-resilience.patch`)

The companion patch `Reports/audit/gate-webhook-health-resilience.patch` adds defensive guards on top of `d4186eb`:

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
@@ -165,6 +165,7 @@ jobs:
 
       - name: Post alert if webhook health fails
         if: failure()
+        continue-on-error: true
         env:
           GH_TOKEN: ${{ secrets.ACC6_PAT || secrets.GITHUB_TOKEN }}
           EXPECTED_URL: ${{ env.EXPECTED_URL }}
@@ -263,7 +264,8 @@ jobs:
               || gh issue create \
                 --repo "$REPO" \
                 --title "$ISSUE_TITLE" \
-                --body-file /tmp/issue_body.md
+                --body-file /tmp/issue_body.md 2>/dev/null \
+              || echo "::warning::failed to create alert issue on $REPO (issues disabled or insufficient token permissions)"
           else
             echo "Existing open issue found (#$EXISTING_ISSUE). Updating with a comment instead of creating duplicate..."
             gh issue comment "$EXISTING_ISSUE" \
@@ -271,4 +273,4 @@ jobs:
               --repo "$REPO" \
               --body-file /tmp/comment_body.md \
               || echo "::warning::failed to add comment to issue #$EXISTING_ISSUE"
           fi
```

---

## 10. Peer Review & Audit Sign-off

- **Auditor:** `agw-ACC2-w02` (Mohammad's GitHub Worker Fleet -- Account 2 Lead Team)
- **Peer Reviewer:** `[کارگر ACC8 · peer]`
- **Peer Review Feedback Incorporated:**
  1. *Complete 5-Phase Roadmap:* Expanded Section 5.1 with full tracking matrix, owners, status, and completion criteria for Phases 1 through 5.
  2. *Actionable Backport Instructions:* Added Section 9.1 with exact CLI and git cherry-pick commands to backport `d4186eb` to `lali94m-max`.
  3. *Permission Attribution Conflict:* Clarified in Section 5.2 that fork tokens without explicit `permissions: issues: write` fail with HTTP 403 even when unlabelled.
  4. *Label Provisioning Step:* Added Section 9.2 with concrete CLI and REST API commands to provision `alert` (`#B60205`) on `lali94m-max`.
  5. *Fork Sync Blocker Solution:* Added Section 7.2 detailing relay workflows and Contents API synchronization to bypass `hq-warm.yml` token limits.
  6. *Alert Fatigue Mitigation:* Added Section 8 detailing issue deduplication, temporary schedule suspension, and quarantine-aware bypass flags.
- **Audit Findings:** Dual-failure chain confirmed. Primary HTTP 404 reflects expected upstream security quarantine (#1615/#1616). Secondary Step 4 crash confirmed as tooling fragility under `set -e` caused by restricted fork token permissions and missing label entity. Control run 42692993945 confirms efficacy of patch `d4186eb`.
- **Status:** APPROVED & COMMITTED
