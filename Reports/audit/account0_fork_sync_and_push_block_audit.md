# Forensic Audit: Account 0 Fork Sync Discrepancies and Runner Push Blockages

**Document ID:** `AUDIT-ACC0-SYNC-PUSH-2026-11-09`  
**Auditor:** `agw-ACC0-w03` (Mohammad's GitHub Worker Fleet -- Account 0)  
**Target Repositories:** Control (`mohammadlali0707-stack/agw-workers`) & Account 0 (`Mohammadlali/agw-workers`)  
**Correlated Incidents:** Board Posts #1704, #1705, #1706 (Run `42609886853`)  
**Referenced Commits:** `36a7825` (lost local commit), `d4186eb` (control Gate 2 fix), `37fc856` (Account 0 HEAD)  
**Status:** Audit Completed & Deliverable Revised  

---

## 1. Executive Summary

During GitHub Actions run `42609886853` on Account 0 (`Mohammadlali/agw-workers`), worker `gh-acc0-direct-acc0` created commit `36a7825` locally on `main` to apply the Gate 2 resilience patch (#1701). When attempting `git push origin main`, the runner was blocked with `HTTP 403 Forbidden` (`remote: Permission to Mohammadlali/agw-workers.git denied to github-actions[bot]`). This audit investigates the push failure and examines why Account 0 lacks recent Gate 2 fixes committed to the control repository (`d4186eb`).

### Key Audit Findings:
1. **Push Blockage Mechanics:** Ephemeral worker runners executing `hq-warm.yml` are strictly scoped to `contents: read`. They possess no write credentials and cannot bypass GitHub branch protection on `main`. Unpushed commit `36a7825` was permanently destroyed upon runner container exit.
2. **Upstream Gate 2 Divergence:** Control repository commit `d4186eb` (committed 2026-11-09 15:05 UTC) added explicit `issues: write` permissions, separated HTTP 404 configuration errors from 5xx server failures, and implemented alert deduplication via issue comments. Account 0 and all fleet forks remain frozen at blob SHA `ac7410f8` (from commit `20d23df`, dated 2026-09-18).
3. **Synchronization Pipeline Dormancy:** `Tools/sync_agy_files_to_forks.py` is encapsulated in `.github/workflows/sync-agy-files.yml`, which triggers exclusively on manual `workflow_dispatch`. GitHub API records reveal it has not been run since **2026-09-21 (Run 35617624704)**, creating a 49-day fleet synchronization freeze.
4. **Health Audit Blind Spots:** `.github/workflows/audit-fork-workflow-health.yml` runs every 6 hours via `Tools/audit_fork_workflow_health.py`, but its `WATCHED` dictionary only inspects `agw-worker.yml` and `agy-lead-plan.yml`. It completely omits all four Gate workflows and shared tools. Furthermore, it only flags unparseable YAML filenames and does not perform SHA/content drift detection against the control repository.

---

## 2. Push Blockage Root Cause Analysis (Incidents #1704 / #1705)

### 2.1 Failure Chain in Run 42609886853
1. **Instruction:** Muse requested Account 0 worker to commit and push Gate 2 patch (#1701).
2. **Execution Context:** The worker was running inside `.github/workflows/hq-warm.yml`.
3. **Local Action:** Worker generated commit `36a7825` on local branch `main`.
4. **Push Failure:** Execution of `git push` resulted in immediate rejection:
   `remote: Permission to Mohammadlali/agw-workers.git denied to github-actions[bot] (HTTP 403)`
5. **Loss:** Ephemeral runner teardown deleted the VM workspace and unpushed commit `36a7825`.

### 2.2 Boundary Breakdown
- **Ambient Runner Permissions:** Lines 19-21 of `hq-warm.yml` restrict permissions to `contents: read` and OIDC writing. The default token passed to git by `actions/checkout` cannot perform push operations.
- **Branch Protection Rules:** Repository branch protection rules for `main` restrict direct pushes from automated bot tokens, requiring pull requests or explicit bypass rights.
- **Git Credential Isolation:** While `hq-warm.yml` supplies secrets to `hqwarm.py`, no Personal Access Token (PAT) was configured in git remote credentials or credential helpers.

### 2.3 Security Policy: Ephemeral Runner Boundaries
- **Strict Least-Privilege Rule:** Ephemeral warm runners (`hq-warm.yml`) and task execution workers must **never** receive ambient write PATs or branch protection bypass privileges.
- **Security Justification:** Providing write tokens to generic task runners creates severe supply chain risks (e.g., prompt injection leading to unauthorized repository mutations) and concurrent push race conditions.
- **Sanctioned Model:** Automated workflow or codebase updates must never be pushed ad-hoc from worker runners. Updates must flow through canonical control synchronization tools or audited Pull Requests.

---

## 3. Fork Divergence & Topology Strategy

### 3.1 Non-Network Repository Architecture
GitHub REST API inspection confirms:
- Control repo (`mohammadlali0707-stack/agw-workers`): `fork: false`, initial commit `95a3ab62` (2026-08-25 00:06 UTC).
- Account 0 repo (`Mohammadlali/agw-workers`): `fork: false`, initial commit `54367abb` (2026-08-25 00:45 UTC).

The repositories do not belong to a common GitHub fork network. Account 0 was initialized independently. Account 0's `main` branch HEAD is currently commit `37fc856` (2026-10-30, `feat: parscoders adapter - full DOM flow`) alongside account-specific Google login helpers.

### 3.2 Gate 2 Workflow Comparison
- **Control Repository (`d4186eb` / Blob `9a0cc1cffd3fa7e65df857f204ff6a00c83f9f7c`):**
  - Explicit job-level permissions: `issues: write, contents: read`.
  - Classifies HTTP 404 (`config_error`) vs HTTP 5xx/timeout (`server_failure`).
  - Appends diagnostic updates to existing open alert issues (`gh issue comment`) instead of filing duplicates.
  - Safe fallback when label `alert` is absent.
- **Account 0 & Fleet Forks (`20d23df` / Blob `ac7410f864a554e83cf570ce2592bc5206818ea9`):**
  - Lacks issue permissions on fork runners.
  - Generates duplicate issues with dynamic minute timestamps on every run.
  - Hard-crashes with exit code 1 when label `alert` does not exist (`could not add label: 'alert' not found`).

### 3.3 Topology Strategy: Standalone vs Fork Network
- **Architectural Decision:** Account 0 should **remain an unlinked standalone repository maintained purely via Contents API synchronization**.
- **Strategic Justification:** Re-establishing Account 0 as an upstream fork would require force-pushing or destructively reconciling diverged git root histories (`95a3ab62` vs `54367abb`), obliterating Account 0's custom adapter history (`parscoders adapter`, local automation). The file-level Contents API sync cleanly updates shared workflow contracts while preserving account-specific code.

---

## 4. Synchronization Infrastructure Audit

### 4.1 Audit of `Tools/sync_agy_files_to_forks.py`
- Pushes 12 core workflow and tooling files across 8 accounts using GitHub Contents API (`PUT /repos/{owner}/agw-workers/contents/{path}`) authenticated by account secrets (`ACC0_PAT` ... `ACC8_PAT`).
- Performs readback validation (`GET`) and YAML syntax verification (`scan_file()`).
- **Primary Flaw:** Its parent workflow `.github/workflows/sync-agy-files.yml` contains only `on: workflow_dispatch`. Lacking automated push or schedule triggers, the workflow has sat dormant since **2026-09-21 (Run 35617624704)**.

### 4.2 Audit of `Tools/audit_fork_workflow_health.py`
- Executes every 6 hours via `.github/workflows/audit-fork-workflow-health.yml`.
- **Blind Spot 1 (Coverage):** `WATCHED` only includes `agw-worker.yml` and `agy-lead-plan.yml`. Gates 1 through 4 are completely unmonitored.
- **Blind Spot 2 (Detection Method):** Asserts health solely by verifying that `workflow.name != workflow.path`. It cannot detect version drift, missing commits, or behavioral degradation. Consequently, it reported all forks as passing while Gate 2 was broken across the entire fleet.

---

## 5. Safe Deployment Recommendations & Remediation Roadmap

### Phase 1: Immediate Remediation (Deploying `d4186eb` to Fleet)
1. **Trigger Sanctioned Sync:** Run `.github/workflows/sync-agy-files.yml` via `workflow_dispatch` on the control repository (`mohammadlali0707-stack/agw-workers`).
2. **Verify Account 0 Readback:** Confirm that `PUT` succeeded and blob SHA on `Mohammadlali/agw-workers` updates to `9a0cc1cffd3fa7e65df857f204ff6a00c83f9f7c`.

### Phase 2: CI/CD Pipeline Automation (Preventing Sync Dormancy)
Update `.github/workflows/sync-agy-files.yml` on the control repository to trigger automatically whenever tracked files change on `main`:
```yaml
on:
  push:
    branches: [main]
    paths:
      - '.github/workflows/gate-*.yml'
      - '.github/workflows/agw-*.yml'
      - '.github/workflows/agy-*.yml'
      - 'Tools/**'
  workflow_dispatch:
```

### Phase 3: Observability & Health Audit Hardening
1. **Expand Workflow Coverage:** Update `WATCHED` in `Tools/audit_fork_workflow_health.py` to monitor all four Gate workflows:
   - `gate-webhook-health.yml`
   - `gate-yaml-lint.yml`
   - `gate-runs-on-policy.yml`
   - `gate-runner-deps.yml`
2. **Implement Content SHA Drift Detection:** Augment the audit tool to fetch the current blob SHA from the control repository and compare it against each fork's blob SHA, emitting `status: fail (DRIFT_DETECTED)` when discrepancies arise.

### Phase 4: Operational Policy & Secret Governance
1. **Enforce Worker No-Push Policy:** Formalize rule that ephemeral runners must never attempt direct pushes to `main`. Worker outputs must be recorded as audit patches or pull requests.
2. **PAT Validation Routine:** Verify that `ACC0_PAT` through `ACC8_PAT` have active `workflow` and `repo` scopes with permissions to update default branches under Contents API.

---

## 6. Audit Verification Summary

| Audit Item | Finding | Status |
| :--- | :--- | :---: |
| **Push Blockage Root Cause** | HTTP 403 due to `hq-warm.yml` `contents: read` & branch protection | Confirmed |
| **Commit 36a7825 Tracing** | Ephemeral commit destroyed on runner VM shutdown | Confirmed |
| **Gate 2 Divergence** | Control at `d4186eb` (`9a0cc1c`); Account 0 frozen at `20d23df` (`ac7410f8`) | Confirmed |
| **Sync Tool Dormancy** | `sync-agy-files.yml` dormant 49 days (last run 2026-09-21) | Confirmed |
| **Audit Tool Blind Spot** | `audit_fork_workflow_health.py` omits Gate 2 and lacks SHA drift check | Confirmed |
| **Topology Strategy** | Retain standalone unlinked repo maintained via Contents API | Confirmed |
| **Workflow Integrity** | Zero workflow files modified directly | Compliant |
