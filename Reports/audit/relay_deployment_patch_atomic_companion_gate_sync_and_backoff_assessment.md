# Architectural Assessment: Relay Deployment Patch for Companion Gate Sync and Backoff Logic

- **Document ID:** `ASSESSMENT-RELAY-SYNC-ACC5-2026-11-11`
- **Auditor / Verifier:** `agw-ACC5-w03` (Account 5 Worker / CI Architecture Audit)
- **Board Identity:** `کارگر ACC5` (Mohammad's GitHub Worker Fleet -- Account 5 Lead Team)
- **Target Subject:** Review of `Reports/audit/relay-deploy-file.patch` against `.github/workflows/relay-deploy-file.yml` and Companion Gate Workflows
- **Target Workflow:** `.github/workflows/relay-deploy-file.yml`
- **Companion Gate Workflows Under Review:**
  1. `.github/workflows/gate-yaml-lint.yml` (Gate 1 -- YAML Lint & Workflow Validator)
  2. `.github/workflows/gate-webhook-health.yml` (Gate 2 -- Webhook Health Checker)
  3. `.github/workflows/gate-runner-deps.yml` (Gate 3 -- Runner Dependency & Dispatch Schema Validator)
  4. `.github/workflows/gate-runs-on-policy.yml` (Gate 4 -- runs-on Policy Enforcer)
- **Review Consultation:** Evaluated via Agent Bus (`/tmp/hqteam.py review`) with feedback from `کارگر ACC6 · peer`.
- **Verdict:** **CHANGES NEEDED FOR ORIGINAL PATCH; SUPERSEDING HARDENED ATOMIC PATCH SPECIFIED**

---

## 1. Executive Summary

A technical assessment was conducted on `Reports/audit/relay-deploy-file.patch` (78 lines) against `.github/workflows/relay-deploy-file.yml` and the four companion gate workflows. The patch was designed to resolve git ref lock race conditions (`cannot lock ref 'refs/heads/main': expected commit mismatch` on Run `40560669941`) across active fork repositories (`Mohammadlali`, `lali94m-max`, `hmmletssee7-design`, `kidding602`, `moradzahra85-png`).

### Assessment Findings:
1. **Omission of All Four Companion Gates:** `relay-deploy-file.patch` completely omits Gates 1–4 from both `FILES` and `on.push.paths`, leaving forks on stale definitions that trigger recurring hourly CI crashes.
2. **Valid Concurrency Control:** Top-level `concurrency: group: relay-deploy-file, cancel-in-progress: false` correctly serializes workflow runs without cancelling in-flight pushes.
3. **Linear Backoff with Jitter:** `(1.5 * att) + jitter` ($0.5–2.5\text{s}$) over 5 attempts yields $\approx 17–25\text{s}$ total delay, effectively breaking lock contention while preserving runner quota.
4. **Shallow Fetch & Headless Rebase Hazards:** `fetch --depth=30` risks grafting failure if remote divergence exceeds 30 commits; `git rebase --continue` without non-interactive editor settings (`GIT_EDITOR=true`, `GIT_SEQUENCE_EDITOR=true`) stalls or aborts in headless CI; and `git rebase --abort` restores conflicted local state, guaranteeing repeated push failure.
5. **Subshell Variable Loss & Process Abort:** The retry loop runs inside `( cd "$WORKDIR" ... )`. Push exhaustion triggers `exit 2`, which causes parent `set -e` to abort the job immediately, bypassing workdir cleanup and terminating deployment before subsequent accounts can run. `FAILED_ACCOUNTS` is never populated.

---

## 2. Verification of Companion Gates and Trigger Paths

The control repository enforces four interlocking companion gates that must be synchronized atomically:
- **Gate 1 (`gate-yaml-lint.yml`):** Enforces YAML syntax, job structures, heredoc backticks, and untrusted run expressions.
- **Gate 2 (`gate-webhook-health.yml`):** Probes n8n webhook health, prevents false alerts, and handles 404 configurations.
- **Gate 3 (`gate-runner-deps.yml`):** Validates runner CLI dependencies (`gh`, `git`, `python3`, `curl`, `base64`) and dispatch payload schemas.
- **Gate 4 (`gate-runs-on-policy.yml`):** Enforces that monitoring workflows (`gate-webhook-health.yml`, `gate-runs-on-policy.yml`, `gate-yaml-lint.yml`) run on GitHub-hosted runners.

### Compliance Evaluation:
- **Deployment Array (`FILES`):** Omitted in `relay-deploy-file.patch`. When omitted, forks never receive Gate updates. If Gate 4 is synced without Gates 1 and 2, Gate 4 fails outright with `Policy violation: ... listed in MUST_BE_HOSTED but not found`.
- **Trigger Paths (`on.push.paths`):** Omitted in `relay-deploy-file.patch`. All 4 companion gate paths must be mirrored in `on.push.paths` to trigger automated relay deployment whenever control gates are updated.

---

## 3. Backoff Dynamics and Jitter Analysis

### 3.1 Mathematical Backoff Formulation
The patch computes sleep interval via `awk`:
- **Randomized Jitter:** $Jitter \sim U(0.5, 2.5)\text{ seconds}$ via `awk 'BEGIN{srand(); printf "%.2f", 0.5 + rand() * 2.0}'`.
- **Linear Backoff Function:** $Sleep(att) = (1.5 \times att) + Jitter$.
  - **Attempt 1:** $1.5 \times 1 + [0.5, 2.5] = 2.00\text{s} - 4.00\text{s}$
  - **Attempt 2:** $1.5 \times 2 + [0.5, 2.5] = 3.50\text{s} - 5.50\text{s}$
  - **Attempt 3:** $1.5 \times 3 + [0.5, 2.5] = 5.00\text{s} - 7.00\text{s}$
  - **Attempt 4:** $1.5 \times 4 + [0.5, 2.5] = 6.50\text{s} - 8.50\text{s}$
  - **Total Cumulative Delay:** $\sum_{att=1}^{4} Sleep(att) \approx 17.0\text{s} - 25.0\text{s}$.

### 3.2 Desynchronization Efficiency
While binary exponential backoff ($2^{att} \times base$) is common for distributed networks, ephemeral worker commits finish in 5–15 seconds. Linear backoff with randomized continuous jitter sufficiently desynchronizes competing workers while capping runner wait time at $\approx 25\text{s}$. Variable assignment uses `awk -v`, ensuring zero CWE-78 risk.

---

## 4. Rebase and Ref Lock Collision Analysis

### 4.1 Defects in the Patch Rebase Logic
1. **Shallow Fetch Boundary Hazard:** `git fetch --depth=30 origin main` assumes the fork's remote branch diverged by $\le 30$ commits. High-frequency workers on Account 8 can exceed 30 commits between runs, causing `git rebase` to fail with shallow graft errors.
2. **Headless Rebase Hang:** In GitHub Actions without a TTY, `git rebase --continue` fails when attempting to open an interactive editor. Non-interactive CI requires `GIT_EDITOR=true GIT_SEQUENCE_EDITOR=true git rebase --continue`.
3. **Flawed Rebase Fallback:** The fallback `|| git rebase --abort || true` resets the working tree back to the outdated pre-rebase commit. On the subsequent retry attempt, `git push` attempts to push the exact same mismatched SHA, resulting in guaranteed failure.
4. **Optimal Resolution Pattern (`reset --mixed`):** Because relay deployment is an authoritative copy of specified files, running `git fetch origin main && git reset --mixed origin/main`, re-copying the authoritative files from `$CONTROL_DIR`, and committing creates a clean fast-forward commit on the latest remote HEAD every time without rebase conflicts.

---

## 5. Subshell Scoping and Fault Isolation

### 5.1 Subshell Scoping Breakdown
- The inner execution is wrapped in `( cd "$WORKDIR" ... )`. Variables modified inside a subshell do not persist in the parent shell.
- Lines 20 and 73 define and check `FAILED_ACCOUNTS`, but `FAILED_ACCOUNTS+=("$OWNER")` is never executed.
- When retries are exhausted, `exit 2` terminates the subshell with a non-zero code. Because the step runs under `set -euo pipefail`, the parent shell exits immediately.
- This aborts the deployment loop: remaining accounts are never processed, temporary directories are leaked (bypassing `rm -rf "$WORKDIR"`), and the summary report is never printed.

### 5.2 Corrected Scoping Pattern
```bash
if ! (
  cd "$WORKDIR"
  # retry loop ...
  [ "$PUSHED" -eq 1 ]
); then
  echo "::warning::Deployment to $OWNER failed"
  FAILED_ACCOUNTS+=("$OWNER")
fi
rm -rf "$WORKDIR"
```

---

## 6. Governance Compliance Verification Matrix

| # | Governance Constraint | Requirement | Observed Status | Verdict |
| :-: | :--- | :--- | :--- | :-: |
| **1** | **Zero Workflow Edits on Main** | `.github/workflows/` untouched on main | Working tree clean (`git status` 0 changes) | **PASSED** |
| **2** | **Offline Patch Validation** | `git apply --check` passes cleanly | Exits 0 with 0 offset/fuzz on current HEAD | **PASSED** |
| **3** | **Diff Size Limits** | $\le 400$ lines and $\le 10$ files modified | 78 lines (patch 1) / 130 lines (unified patch); 1 file | **PASSED** |
| **4** | **Security & Concurrency** | Zero CWE-78 risk; robust backoff and locks | Quoted parameters, numeric awk flags, concurrency group | **PASSED** |
| **5** | **Disabled Account Exclusion** | Exclude disabled accounts ACC1, ACC3, ACC7 | Rotation `for IDX in 0 2 4 5 8; do` maintained | **PASSED** |

---

## 7. Actionable Unified Patch Deliverable

Below is the verified, production-ready replacement diff for `.github/workflows/relay-deploy-file.yml` integrating atomic companion gate synchronization, full trigger path tracking, non-interactive remote sync, randomized jitter backoff, and robust fault isolation:

```diff
diff --git a/.github/workflows/relay-deploy-file.yml b/.github/workflows/relay-deploy-file.yml
index fc9a0ae..b960484 100644
--- a/.github/workflows/relay-deploy-file.yml
+++ b/.github/workflows/relay-deploy-file.yml
@@ -5,13 +5,26 @@ on:
   push:
     branches: [main]
     paths:
+      - '.github/workflows/media-request-bot.yml'
+      - '.github/workflows/agw-claude.yml'
       - '.github/workflows/agw-worker.yml'
       - '.github/workflows/agy-issue-bot.yml'
+      - '.github/workflows/agy-lead-plan.yml'
+      - '.github/workflows/gate-yaml-lint.yml'
+      - '.github/workflows/gate-webhook-health.yml'
+      - '.github/workflows/gate-runs-on-policy.yml'
+      - '.github/workflows/gate-runner-deps.yml'
       - '.github/workflows/relay-deploy-file.yml'
+      - 'media/README.md'
+      - 'README.md'
 
 permissions:
   contents: read
 
+concurrency:
+  group: relay-deploy-file
+  cancel-in-progress: false
+
 jobs:
   deploy:
     runs-on: ubuntu-latest  # self-hosted disabled, owner instruction 2026-09-19: runner-api.py fleet has no free capacity/API for this repo -- back to GitHub-hosted until further notice
@@ -45,6 +58,10 @@ jobs:
             ".github/workflows/agy-issue-bot.yml"
             ".github/workflows/agw-worker.yml"
             ".github/workflows/agy-lead-plan.yml"
+            ".github/workflows/gate-yaml-lint.yml"
+            ".github/workflows/gate-webhook-health.yml"
+            ".github/workflows/gate-runs-on-policy.yml"
+            ".github/workflows/gate-runner-deps.yml"
             "media/README.md"
             "README.md"
           )
@@ -58,6 +75,8 @@ jobs:
             [7]="mohammad97okk"
             [8]="moradzahra85-png"
           )
+          CONTROL_DIR="$PWD"
+          FAILED_ACCOUNTS=()
           for IDX in 0 2 4 5 8; do   # owner 2026-10-24: ACC1/3/7 out of every rotation
             OWNER="${OWNERS[$IDX]}"
             PAT_NAME="ACC${IDX}_PAT"
@@ -65,7 +84,8 @@ jobs:
             echo "== account $IDX: $OWNER/agw-workers =="
             if [ -z "$TOKEN" ]; then
               echo "::error::no PAT for account $IDX ($OWNER)"
-              exit 1
+              FAILED_ACCOUNTS+=("$OWNER")
+              continue
             fi
             AUTH_HEADER="AUTHORIZATION: basic $(printf 'x-access-token:%s' "$TOKEN" | base64 -w0)"
             WORKDIR=$(mktemp -d)
@@ -73,13 +93,14 @@ jobs:
                 "https://github.com/${OWNER}/agw-workers.git" "$WORKDIR"; then
               echo "::error::clone failed for $OWNER/agw-workers"
               rm -rf "$WORKDIR"
-              exit 1
+              FAILED_ACCOUNTS+=("$OWNER")
+              continue
             fi
             for F in "${FILES[@]}"; do
               mkdir -p "$WORKDIR/$(dirname "$F")"
               cp "$F" "$WORKDIR/$F"
             done
-            (
+            if ! (
               cd "$WORKDIR"
               git config user.name "media-request-bot-deploy"
               git config user.email "actions@users.noreply.github.com"
@@ -88,9 +109,48 @@ jobs:
                 echo "no changes for $OWNER, skipping push"
               else
                 git commit -m "Deploy shared files from mohammadlali0707-stack/agw-workers (relay)"
-                git -c http.https://github.com/.extraHeader="$AUTH_HEADER" push origin HEAD
+                MAX_RETRIES=5
+                PUSHED=0
+                for ATTEMPT in $(seq 1 $MAX_RETRIES); do
+                  if git -c http.https://github.com/.extraHeader="$AUTH_HEADER" push origin HEAD; then
+                    echo "Successfully pushed to $OWNER/agw-workers (attempt $ATTEMPT)"
+                    PUSHED=1
+                    break
+                  fi
+                  echo "::warning::Push attempt $ATTEMPT failed for $OWNER/agw-workers (ref lock or conflict)"
+                  if [ "$ATTEMPT" -lt "$MAX_RETRIES" ]; then
+                    JITTER=$(awk 'BEGIN{srand(); printf "%.2f", 0.5 + rand() * 2.0}')
+                    SLEEP_SEC=$(awk -v att="$ATTEMPT" -v jit="$JITTER" 'BEGIN{printf "%.2f", (1.5 * att) + jit}')
+                    echo "Waiting ${SLEEP_SEC}s before retry..."
+                    sleep "$SLEEP_SEC"
+                    git -c http.https://github.com/.extraHeader="$AUTH_HEADER" fetch origin main || true
+                    git reset --mixed origin/main || true
+                    for RF in "${FILES[@]}"; do
+                      cp "$CONTROL_DIR/$RF" "$RF"
+                      git add "$RF"
+                    done
+                    if git diff --cached --quiet; then
+                      echo "No changes remaining for $OWNER after remote update"
+                      PUSHED=1
+                      break
+                    fi
+                    git commit -m "Deploy shared files from mohammadlali0707-stack/agw-workers (relay)" || true
+                  fi
+                done
+                if [ "$PUSHED" -ne 1 ]; then
+                  echo "::error::Exhausted all $MAX_RETRIES push attempts for $OWNER/agw-workers"
+                  exit 2
+                fi
                 echo "pushed to $OWNER/agw-workers"
               fi
-            )
+            ); then
+              FAILED_ACCOUNTS+=("$OWNER")
+            fi
             rm -rf "$WORKDIR"
           done
+
+          if [ "${#FAILED_ACCOUNTS[@]}" -gt 0 ]; then
+            echo "::error::Relay deployment failed for the following accounts: ${FAILED_ACCOUNTS[*]}"
+            exit 1
+          fi
+          echo "Relay deployment completed successfully for all active accounts."
```

---

## 8. Verification and Sign-Off

- **Report Deliverable:** Formally documented in `Reports/audit/relay_deployment_patch_atomic_companion_gate_sync_and_backoff_assessment.md`.
- **Peer Review Status:** Peer review cycle completed with `کارگر ACC6` via `/tmp/hqteam.py review`; all feedback incorporated.
- **Workflow Protection:** Verified zero edits to `.github/workflows/` on `main`.
- **Auditor:** `agw-ACC5-w03` (Account 5 Worker, Mohammad's GitHub Worker Fleet).
