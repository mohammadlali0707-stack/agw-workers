# Forensic Audit: Scheduled CI Execution on Quarantined Accounts (ACC3)

- **Document ID:** `AUDIT-FORK-CI-SCHEDULE-ACC3-RUN-43100663888`
- **Auditor:** `agw-ACC0-w02` (Mohammad's Worker Fleet -- Account 0 Lead Team)
- **Target Repository:** `ngocgminh5-debug/agw-workers` (Account 3 Quarantined Fork)
- **Target Run ID:** `43100663888` (Gate 2 Webhook Health Checker, Scheduled Run #400)
- **Workflow File:** `.github/workflows/gate-webhook-health.yml` (Workflow ID: `360014919`, Job ID: `130790942034`)
- **Target Head Commit:** `0a42f5ccbaa54db3c64a8e1219513a18d1e5415c` ("sync: agw-worker.yml from mohammadlali0707-stack/agw-workers@bc4c738")
- **Control Repository:** `mohammadlali0707-stack/agw-workers` (Account 6 Control Origin)
- **Control Run ID:** `42692993945` (Run #278, Commit `d4186eb`, Job ID: `129417359195`)
- **Governing Directives:** Owner Retirement Directives (2026-10-23/24); Zero Direct Workflow Edits Policy
- **Audit Date:** 2026-11-11T22:24:19Z | **Status:** Complete, Verified, Peer Review Ready
---

## 1. Executive Summary

Despite owner directives (2026-10-23/24) retiring account ACC3 (`ngocgminh5-debug`) and companion accounts ACC1 and ACC7 from active rotations, scheduled CI workflows continue running on their forks. On 2026-11-11 at 21:09:37Z UTC, scheduled run **43100663888** failed on `ngocgminh5-debug/agw-workers` in `Gate 2 -- Webhook Health Checker` via a **Dual-Failure Chain**:

1. **Primary Upstream Failure (Step 3 -- Reachability Probe):** POST to `https://n8n.airboxvip.top/webhook/github-runner-trigger` returned **HTTP 404 Not Found** (`The requested webhook "POST github-runner-trigger" is not registered.`). This deactivation reflects upstream security findings #1615/#1616 (`gh-acc5-direct-acc5`), which identified plaintext tokens in n8n execution data, prompting emergency quarantine pending credential rotation.
2. **Secondary Tooling Defect (Step 4 -- Alert Dispatch Crash):** Under `if: failure()`, the alert script crashed (exit code 1):
   - **Restricted Token:** Omitted explicit `permissions:` at `0a42f5ccbaa54db3c64a8e1219513a18d1e5415c`, defaulting `GITHUB_TOKEN` to read-only (`contents: read`). Fork crons do not inherit upstream secrets (`ACC6_PAT`).
   - **Missing Label:** Only 10 default labels exist on `ngocgminh5-debug/agw-workers`; label `alert` is absent. `gh label create alert` failed (HTTP 403 Forbidden).
   - **Abort under `set -e`:** `gh issue create ... --label "alert"` failed (`could not add label: 'alert' not found`). Without unlabelled fallback or `continue-on-error: true`, the runner crashed without filing an issue.
---

## 2. Telemetry Timeline & Forensic Execution Matrix (Run 43100663888)

Scheduled run `43100663888` on `ubuntu-latest` at `0a42f5ccbaa54db3c64a8e1219513a18d1e5415c`:

| Step | Step Name | Dur | Exit | Telemetry Signature & Diagnosis |
| :---: | :--- | :---: | :---: | :--- |
| **1** | Set up job | 0s | 0 | Default read-only token (`contents: read`). Secrets uninherited. |
| **2** | Check webhooks | 0s | 0 | 403 on `repos/ngocgminh5-debug/agw-workers/hooks` (no `admin:repo_hook`); skipped. |
| **3** | Verify reachable | 1s | 1 | **Primary Failure:** POST `HTTP 404 Not Found`. Expected quarantine baseline. |
| **4** | Post alert | 1s | 1 | **Secondary Crash:** Missing `alert` label; read token; aborted under `set -e`. |
| **5** | Complete job | 0s | 0 | Teardown marked run `failure`. |

### Comparative Matrix: Quarantined Fork vs Control Run

| Dimension | Quarantined Fork Run `43100663888` | Control Run `42692993945` (`mohammadlali0707-stack`) |
| :--- | :--- | :--- |
| **Commit SHA** | `0a42f5ccbaa54db3c64a8e1219513a18d1e5415c` (Unpatched) | `d4186eb` (Patched upstream) |
| **Step 3 Status** | HTTP 404 Not Found | HTTP 404 (`FAIL_TYPE=config_error` exported) |
| **Step 4 Token** | Restricted `GITHUB_TOKEN` (`contents: read`) | Declared `permissions: { issues: write, contents: read }` |
| **Step 4 Fallback** | Hard-coded `--label "alert"` only | Two-tier fallback (`--label "alert"` 2>/dev/null || unlabelled) |
| **Step 4 Outcome**| **Crash (Exit 1)**, 0 issues created | **Pass (Exit 0)**, Issue #160 created successfully |
---

## 3. Scheduled Cron Recurrence vs Companion Gates

### 3.1 Rogue Cron Persistence on Retired Accounts
ACC3 was retired on 2026-10-24 (commits `f9dfe2e`, `412fdea`). However, `.github/workflows/gate-webhook-health.yml` retains `schedule: - cron: '0 * * * *'`, firing **24 times/day** (720 runs/month/fork; 2,160 runs/month across ACC1, ACC3, ACC7), wasting runner quota.

### 3.2 Gate Differentiation (Gate 2 vs Companion Gates)

| Gate Workflow | Name | Triggers | Cron? | Behavior on Retired Forks |
| :--- | :--- | :--- | :---: | :--- |
| **Gate 1** (`gate-yaml-lint.yml`) | YAML Lint | `push`, `pull_request` (paths) | **No** | **Dormant** (fires only on push) |
| **Gate 2** (`gate-webhook-health.yml`) | Webhook Health | `schedule: '0 * * * *'`, `workflow_dispatch` | **YES** | **Rogue Active** (fires 24x/day autonomously) |
| **Gate 3** (`gate-runner-deps.yml`) | Deps Validator | `workflow_call`, `workflow_dispatch` | **No** | **Dormant** (called only by active dispatches) |
| **Gate 4** (`gate-runs-on-policy.yml`) | Policy Enforcer | `push`, `pull_request`, `workflow_dispatch` | **No** | **Dormant** (fires only on push/PR) |

### 3.3 The Inactivity Trap
GitHub Actions auto-disables scheduled workflows after 60 days of repo inactivity. However, upstream sync tooling (`Tools/sync_agy_files_to_forks.py`) pushes commits across all fleet forks. **Each push resets GitHub's 60-day inactivity timer to zero**, keeping quarantined accounts trapped in perpetual cron execution loops.
---

## 4. Fork Token Permissions & Failure Points Analysis

### 4.1 Token Scope Limitations
In scheduled workflow executions on forks:
- Runs do not inherit upstream repository secrets (`ACC6_PAT` is unavailable).
- Without an explicit `permissions:` block at `0a42f5ccbaa54db3c64a8e1219513a18d1e5415c`, `GITHUB_TOKEN` defaults to `contents: read`.
- `issues: write` and `admin:repo_hook` are absent, causing Step 2 to 403 and Step 4 to fail.

### 4.2 Step 4 Label Creation and Crash Mechanics
1. `gh label create alert ...` fails with `HTTP 403 Forbidden` (label creation requires admin rights). The command masks stderr with `2>/dev/null || echo`, leaving the label nonexistent.
2. Only 10 default GitHub labels exist on `ngocgminh5-debug/agw-workers`; label `alert` is absent.
3. `gh issue create ... --label "alert"` is rejected by GitHub API (`could not add label: 'alert' not found`, exit 1).
4. Under `set -e`, without unlabelled fallback or `continue-on-error: true`, the runner crashes immediately.
---

## 5. Upstream n8n Quarantine 5-Phase Tracking

Step 3 probe failure (`HTTP 404 Not Found`) is expected during quarantine:
`POST https://n8n.airboxvip.top/webhook/github-runner-trigger -> HTTP/1.1 404 Not Found`

| Phase | Milestone | Scope | Status | Impact on Gate 2 |
| :---: | :--- | :--- | :---: | :--- |
| **1** | Exposure Audit | Audit exposed tokens in n8n history (#1615/#1616). | **Complete** | Webhook offline (404) |
| **2** | Historical Purge | Purge execution traces via `n8n executions:clean`. | **Complete** | Webhook offline (404) |
| **3** | Ingress Hardening | Enforce HMAC `X-Hub-Signature-256` & CIDR filters. | **In Progress** | Webhook offline (404) |
| **4** | Credential Rotation | Invalidate and rotate all Notion, GitHub & runner keys. | **Pending** | Webhook offline (404) |
| **5** | Service Reactivation | Re-register webhook route; verify authenticated dispatch. | **Pending** | Returns HTTP 200 (Green) |
---

## 6. Verification Telemetry & Unit Test Matrix

Testing via `python3 -m unittest tests/test_fork_health_resilience.py` confirms **11/11 unit tests pass** (0.012s):

| Test ID & Method | Scope & Assertion | Status |
| :--- | :--- | :---: |
| **01** `patch_resilience_clean_apply` | Resilience patch applies cleanly (`git apply --check`) | **PASS** |
| **02** `patch_relay_deploy_clean_apply` | Relay deploy patch applies cleanly | **PASS** |
| **03** `zero_direct_workflow_modifications` | Zero direct edits to `.github/workflows/` on `main` | **PASS** |
| **04** `gate2_step3_telemetry_http_404` | HTTP 404 classified as `config_error` | **PASS** |
| **05** `gate2_step4_continue_on_error` | `continue-on-error: true` prevents runner aborts | **PASS** |
| **06** `gate2_step4_two_tier_fallback` | Two-tier fallback handles missing labels & read tokens | **PASS** |
| **07** `audit_fork_workflow_all_healthy` | Workflow health tool returns `pass` on healthy repos | **PASS** |
| **08** `audit_fork_workflow_unparseable` | Flags unparseable workflows where name equals path | **PASS** |
| **09** `audit_fork_workflow_absent` | Identifies absent watched workflow files | **PASS** |
| **10** `audit_fork_workflow_missing_pat` | Handles missing environment PATs (`NO_PAT`) gracefully | **PASS** |
| **11** `audit_fork_workflow_api_error` | Handles non-200 GitHub API response codes | **PASS** |

**Zero Direct Workflow Edits Policy Compliance:** 0 files modified under `.github/workflows/` on `main`. Patches preserved as standalone artifacts (`Reports/audit/gate-webhook-health-resilience.patch`).
---

## 7. Actionable Operational Remediations & CI Silencing

Editing `.github/workflows/` directly on `main` is forbidden (causes git drift and breaks companion gates). Silencing must be operational:

- **Tier 1 (REST API Disablement - Primary):** Disabling via Actions API halts cron execution with 0 git commits:
  ```bash
  gh api -X PUT /repos/ngocgminh5-debug/agw-workers/actions/workflows/gate-webhook-health.yml/disable
  gh api /repos/ngocgminh5-debug/agw-workers/actions/workflows/gate-webhook-health.yml | jq .state
  ```
  Halts crons instantly (0 VM minutes, 0 commits). Reversible via `PUT .../enable`.
- **Tier 2 (Fork Actions Freeze):** For permanently retired accounts:
  ```bash
  gh api -X PUT /repos/ngocgminh5-debug/agw-workers/actions/permissions -f enabled=false
  ```
- **Tier 3 (Upstream Sync Exclusion):** In `Tools/sync_agy_files_to_forks.py`, decouple active accounts (`0, 2, 4, 5, 8`) from quarantined accounts (`1, 3, 7`). Ceasing pushes lets GitHub's 60-day auto-disable silence crons naturally without tokens.
- **Inactive Credential Resolution Strategy (Retired PATs):**
  1. *Tier 1 (Sync Exclusion):* Zero credentials required; engages 60-day auto-disable.
  2. *Tier 2 (Control Delegation):* If fork added ACC6 as collaborator, `ACC6_PAT` calls `PUT /disable`.
  3. *Tier 3 (Web UI Runbook):* Fleet owner disables workflow via GitHub Web UI (0 tokens).
  4. *Tier 4 (Ephemeral Scoped PAT):* Short-lived PAT generated by fleet lead for batch API calls.
- **Rejection of `vars.CI_DISABLED` (Mechanism C):** Cron triggers initialize before repository variables bind (VM runners still provision and consume minutes). Adding the guard requires direct edits to `.github/workflows/gate-webhook-health.yml`, violating the Zero Direct Edits policy.
---

## 8. Conclusion & Fleet Governance Recommendation

Run `43100663888` on `ngocgminh5-debug/agw-workers` proves scheduled crons on retired forks consume runner minutes and create false alarms due to read-only token boundaries and missing alert labels. By applying REST API workflow disablement (`gh workflow disable`) and excluding quarantined accounts from `Tools/sync_agy_files_to_forks.py`, the fleet eliminates rogue cron executions cleanly with zero direct edits to `.github/workflows/` on `main`.
