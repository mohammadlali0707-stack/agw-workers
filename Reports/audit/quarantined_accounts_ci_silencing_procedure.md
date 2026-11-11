# Governance Procedure: Scheduled CI Gate Silencing on Quarantined Accounts

- **Document ID:** `GOV-AUDIT-CI-SILENCE-ACC0-2026-11-11`
- **Auditor:** `agw-ACC0-w03` (Account 0 Lead Team)
- **Target Quarantined Accounts:** ACC1 (`momonakikugava-pixel`), ACC3 (`ngocgminh5-debug`), ACC7 (`mohammad97okk`)
- **Active Fleet Baseline:** ACC0 (`Mohammadlali`), ACC2 (`lali94m-max`), ACC4 (`hmmletssee7-design`), ACC5 (`kidding602`), ACC8 (`moradzahra85-png`), ACC6 (`mohammadlali0707-stack`, Control Origin)
- **Primary Workflows:** `.github/workflows/gate-webhook-health.yml` (Gate 2 cron: `0 * * * *`), `.github/workflows/agw-worker.yml`, companion gates (`gate-yaml-lint.yml`, `gate-runs-on-policy.yml`, `gate-runner-deps.yml`)
- **Governing Directives:** Owner Directives (2026-10-23/24); Zero Direct Workflow Edits Policy; Ephemeral Worker Read-Only Model.
- **Status:** APPROVED & Final Governance Deliverable (Incorporating Peer Review)

---

## 1. Executive Summary & Problem Formulation

### 1.1 The Operational Hazard
Under owner directives (2026-10-23/24), accounts ACC1, ACC3, and ACC7 were retired from rotations. However, their forks (`agw-workers`) retain `.github/workflows/gate-webhook-health.yml` (Gate 2 cron: `0 * * * *`), triggering 24 runs/day per fork (2,160 runs/month across all three). This causes:
1. **Runner Quota Exhaustion:** Burns GitHub-hosted runner quotas on inactive forks.
2. **Failure Noise:** Probing quarantined n8n webhook (#1615/#1616) returns 404, causing crash loops under `set -e` on missing labels/scopes (e.g. ACC3 run `42401883413`).
3. **The Inactivity Trap:** GitHub Actions auto-disables scheduled workflows after 60 days of inactivity. However, upstream sync tools (`Tools/sync_agy_files_to_forks.py`) push commits to all accounts, **continually resetting GitHub's 60-day inactivity timer to zero** and keeping cron schedules alive indefinitely.

### 1.2 Mandatory Constraint: Zero Direct Edits to `.github/workflows/`
Workers must **never** edit `.github/workflows/` on `main`. Commenting cron schedules or inserting `if:` guards causes git drift, breaks fork sync, and violates companion gate invariants (Gate 3/4). All silencing must be strictly non-destructive and operational.

---

## 2. Evaluation of Non-Destructive Operational Mechanisms

### 2.1 Mechanism A: REST API Workflow Disablement (`PUT .../actions/workflows/{id}/disable`)
- **Endpoint:** `PUT /repos/{owner}/{repo}/actions/workflows/{workflow_id}/disable` (`workflow_id`: numeric ID or filename `gate-webhook-health.yml`).
- **Effect:** Sets state to `disabled_manually`. GitHub halts cron execution completely. 0 VM runners provision (0 minutes consumed). Zero git commits.
- **Reversibility:** Instant via `PUT /repos/{owner}/{repo}/actions/workflows/{workflow_id}/enable`.
- **Verdict:** **Tier 1 Primary Operational Mechanism**.

### 2.2 Mechanism B: Fork Repository Actions Permissions Freeze (`PUT .../actions/permissions`)
- **Endpoint:** `PUT /repos/{owner}/{repo}/actions/permissions` with body `{"enabled": false}`.
- **UI Path:** Repo `Settings` -> `Actions` -> `General` -> `Disable Actions`.
- **Effect:** Freezes Actions engine on fork. Halts all scheduled, push, and dispatch runs.
- **Verdict:** **Tier 2 Mechanism for Permanently Decommissioned Forks**.

### 2.3 Mechanism C: Repository Variables / Dispatch Flags (`vars.CI_DISABLED`) -- REJECTED
- **Technical Infeasibility:** Scheduled cron triggers evaluate before repository variables. Evaluating `if: ${{ !vars.CI_DISABLED }}` requires runner VM provisioning first.
- **Constraint Violation:** Adding `if: ${{ !vars.CI_DISABLED }}` requires modifying `.github/workflows/gate-webhook-health.yml`, violating the Zero Direct Edits policy. Runner initialization still consumes queue capacity before skipping.
- **Verdict:** **REJECTED as infeasible without workflow edits**.

### 2.4 Mechanism D: Upstream Sync Tool Exclusion (`Tools/sync_agy_files_to_forks.py`)
- **Effect:** Decouples active rotation (`0 2 4 5 8`) from quarantined accounts (`1 3 7`). Halts write commits to inactive forks, allowing GitHub's native 60-day inactivity shutoff to take effect naturally without credentials.
- **Verdict:** **Mandatory Companion Architecture (Tier 1)**.

---

## 3. Resolving Inactive & Expired Credential Dependencies

Because ACC1, ACC3, and ACC7 are decommissioned, their tokens (`ACC1_PAT`, `ACC3_PAT`, `ACC7_PAT`) are inactive or expired. We establish a 4-Tier Credential Resolution Strategy:

```
[Inactive Accounts: ACC1, ACC3, ACC7]
  +--> Tier 1: Upstream Sync Exclusion (Tools/sync_agy_files_to_forks.py)
  |      --> ZERO credentials required. Stops pushes; 60-day auto-disable engages.
  +--> Tier 2: Cross-Account Admin Delegation (ACC6_PAT / ACC0_PAT)
  |      --> Fork repository configures Control (ACC6) as Outside Collaborator / Admin.
  |      --> Allows Control PAT to invoke PUT /disable on fork workflows.
  +--> Tier 3: Web UI Operator Runbook (Zero PATs)
  |      --> Fleet owner logs in via browser and clicks "Disable workflow" (0 API calls).
  +--> Tier 4: Ephemeral Maintenance PAT
         --> Fleet owner generates short-lived PAT with `workflow` scope for batch API runs.
```

1. **Tier 1 (Zero-Credential Automated Decoupling):** Upstream sync exclusion requires zero credentials on quarantined forks. Halting push commits allows GitHub's native 60-day inactivity shutoff to silence cron jobs autonomously.
2. **Tier 2 (Cross-Account Delegation):** If the fork added `mohammadlali0707-stack` (ACC6) as admin collaborator, `ACC6_PAT` executes `PUT .../actions/workflows/{id}/disable` directly.
3. **Tier 3 (Web UI Fallback):** Operators disable workflows via browser UI under `Actions -> Gate 2 -> ... -> Disable workflow` without generating any PAT tokens.
4. **Tier 4 (Ephemeral Token):** Temporary scoped PAT generated by fleet owner for one-time API execution.

---

## 4. `Tools/sync_agy_files_to_forks.py` Exclusion Implementation

To prevent upstream synchronizations from touching quarantined accounts, update `Tools/sync_agy_files_to_forks.py` (lines 64-85) with explicit rotation partitioning:

```python
# Active production rotation (Owner directive 2026-10-24: 0, 2, 4, 5, 8)
ACTIVE_ACCOUNTS = {0: "Mohammadlali", 2: "lali94m-max", 4: "hmmletssee7-design", 5: "kidding602", 8: "moradzahra85-png"}
# Quarantined accounts permanently excluded from routine sync
QUARANTINED_ACCOUNTS = {1: "momonakikugava-pixel", 3: "ngocgminh5-debug", 7: "mohammad97okk"}
ACCOUNTS = ACTIVE_ACCOUNTS.copy()
```

### Operational Loop with Quarantine Isolation & Safe Error Handling
```python
    include_quarantined = "--include-quarantined" in sys.argv
    targets = {**ACTIVE_ACCOUNTS, **QUARANTINED_ACCOUNTS} if include_quarantined else ACTIVE_ACCOUNTS

    for idx, owner in sorted(targets.items()):
        if idx in QUARANTINED_ACCOUNTS and not include_quarantined:
            print(f"=== account {idx} ({owner}/agw-workers): QUARANTINED (skipped by policy)")
            continue
        token = os.environ.get(f"ACC{idx}_PAT", "")
        repo = f"{owner}/agw-workers"
        if not token:
            print(f"=== account {idx} ({repo}): NO_PAT, skipped")
            results[repo] = "NO_PAT"; failed += 1; continue
```
*Impact:* Eliminates `NO_PAT` exit 1 errors on decommissioned accounts and prevents commits from resetting the 60-day inactivity timer.

---

## 5. Operational Runbook & CWE-78 Immune Implementation

All scripts enforce **CWE-78 (OS Command Injection) Immunity** by using structured argument vectors (`subprocess.run(..., shell=False)`) without shell string interpolation.

### 5.1 Python Management Utility: `Tools/quarantine_silence_manager.py`
```python
#!/usr/bin/env python3
# CWE-78 safe CLI to audit, silence, and re-enable workflows on quarantined accounts.
import argparse, json, os, subprocess, sys

QUARANTINED = {1: "momonakikugava-pixel", 3: "ngocgminh5-debug", 7: "mohammad97okk"}
WORKFLOW = "gate-webhook-health.yml"

def run_gh_api(endpoint, method="GET", token=None):
    env = os.environ.copy()
    if token: env["GH_TOKEN"] = token
    cmd = ["gh", "api", "-X", method, endpoint]
    res = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    return res.returncode, res.stdout.strip(), res.stderr.strip()

def manage(action):
    print(f"=== {action.upper()} Quarantined Gate 2 Workflows ===")
    for idx, owner in sorted(QUARANTINED.items()):
        repo = f"{owner}/agw-workers"
        token = os.environ.get(f"ACC{idx}_PAT") or os.environ.get("ACC6_PAT")
        if not token:
            print(f"[*] Account {idx} ({repo}): Token unset (Tier 1 Inactivity Isolation active)")
            continue
        if action == "audit":
            rc, out, err = run_gh_api(f"repos/{repo}/actions/workflows/{WORKFLOW}", token=token)
            state = json.loads(out).get("state", "unknown") if rc == 0 else f"ERR_{rc}"
            print(f"    {repo}: state={state}")
        elif action == "silence":
            rc, _, err = run_gh_api(f"repos/{repo}/actions/workflows/{WORKFLOW}/disable", method="PUT", token=token)
            print(f"    {repo}: {'DISABLED (HTTP 204)' if rc == 0 else f'FAIL_{rc}: ' + err}")
        elif action == "verify":
            rc, out, _ = run_gh_api(f"repos/{repo}/actions/workflows/{WORKFLOW}", token=token)
            state = json.loads(out).get("state") if rc == 0 else "FAIL"
            print(f"    {repo}: state={state} -> {'PASS (Silenced)' if state == 'disabled_manually' else 'WARN'}")

if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("action", choices=["audit", "silence", "verify"])
    manage(p.parse_args().action)
```

### 5.2 Verification Queries & Validation Standards
1. **Workflow State Readback:**
   ```bash
   gh api repos/ngocgminh5-debug/agw-workers/actions/workflows/gate-webhook-health.yml --jq '{name: .name, state: .state}'
   ```
   **Expected Result:** `{"name": "Gate 2 -- Webhook Health Checker", "state": "disabled_manually"}`.
2. **Scheduled Run Silence Check:**
   ```bash
   gh api "repos/ngocgminh5-debug/agw-workers/actions/workflows/gate-webhook-health.yml/runs?event=schedule" --jq '.total_count'
   ```
   Confirm no runs have executed since the disablement timestamp.

### 5.3 Rollback & Reactivation Procedure
If an account is returned to active rotation by owner directive:
1. Re-enable the workflow:
   ```bash
   gh api -X PUT repos/{owner}/agw-workers/actions/workflows/gate-webhook-health.yml/enable
   ```
2. Verify state restoration to `"active"`:
   ```bash
   gh api repos/{owner}/agw-workers/actions/workflows/gate-webhook-health.yml --jq '.state'
   ```
3. Transfer account index from `QUARANTINED_ACCOUNTS` to `ACTIVE_ACCOUNTS` in `Tools/sync_agy_files_to_forks.py`.

---

## 6. Multi-Layer Governance Architecture & Policy Commitments

1. **Layer 1 (Upstream Pipeline Guard):** Permanently exclude ACC1, ACC3, ACC7 from default targets in `Tools/sync_agy_files_to_forks.py`, ensuring zero write commits reset GitHub's 60-day inactivity timer.
2. **Layer 2 (REST API Disablement):** Execute `PUT .../actions/workflows/gate-webhook-health.yml/disable` across quarantined forks using Tier 2 (Control Admin) or Tier 4 (Maintenance PAT) credentials.
3. **Layer 3 (Repository Actions Freeze):** For accounts permanently decommissioned from the fleet, invoke `PUT /repos/{owner}/{repo}/actions/permissions` with `{"enabled": false}`.
4. **Zero Workflow Edits Guarantee:** All procedures operate 100% via API and synchronization tools, leaving `.github/workflows/` untouched on `main`.
5. **CWE-78 Assurance:** All automation scripts use argument list execution (`shell=False`), guaranteeing complete immunity from command injection vulnerabilities.

---
*Certified and submitted by `agw-ACC0-w03` (Account 0 Lead Team).*
