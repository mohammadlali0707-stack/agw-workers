# Technical Audit Report: gate-webhook-health Omission from relay-deploy-file

- **Document ID:** `AUDIT-RELAY-SYNC-ACC4-2026-11-09-GATE2-OMISSION`
- **Auditor / Worker ID:** `agw-ACC4-w03` (Account 4 Lead Team) / `agw-worker` (ACC5/ACC4 peer coordination)
- **Board Identity:** `کارگر ACC4` (Mohammad's GitHub Worker Fleet -- Account 4 Lead)
- **Target Workflow:** `.github/workflows/relay-deploy-file.yml`
- **Synchronized Artifact:** `.github/workflows/gate-webhook-health.yml` (Gate 2 Webhook Health Checker)
- **Companion Gate Workflows:** `gate-yaml-lint.yml`, `gate-runs-on-policy.yml`, `gate-runner-deps.yml`
- **Reference Script:** `Tools/sync_agy_files_to_forks.py`
- **Associated Commits:** `d4186eb` (Control Gate 2 resilience patch #1701), `72db763` (Stale fork head SHA)
- **Associated Incidents:** Run `42692590887` (`hmmletssee7-design`), Run `42693445322` (`Mohammadlali`)
- **Status:** Completed, Revised with Peer Approval, and Ready for Deployment
- **Patch Deliverable:** `Reports/audit/gate-webhook-health-relay-deploy.patch`

---

## 1. Executive Summary & Problem Isolation

On 2026-11-09, control repository commit `d4186eb` resolved Gate 2 CI failures by categorizing HTTP 404 endpoint responses as configuration errors, preventing duplicate issue creation via issue comments, and securing runner token evaluation with explicit `permissions: issues: write, contents: read`.

However, active fork repositories—including Account 4 (`hmmletssee7-design/agw-workers`) and Account 0 (`Mohammadlali/agw-workers`)—continued executing stale workflow definitions (SHA `72db763ed27075b68e9c24c7442eb1467270e91d`). During scheduled hourly executions (e.g. run `42692590887`), fork CI runners crashed in Step 4 (`Post alert if webhook health fails`) with exit code 1 (`could not add label: alert not found`), cascading false alerts across the fleet.

Investigation isolates the root cause to a compound architectural omission in `.github/workflows/relay-deploy-file.yml`:
1. **Omission from `FILES` Array:** `.github/workflows/gate-webhook-health.yml` (and companion gates 1, 3, and 4) is absent from the deployment array.
2. **Incomplete Push Path Triggers:** Commits modifying `gate-webhook-health.yml` do not trigger the relay workflow because `on.push.paths` only monitors `agw-worker.yml`, `agy-issue-bot.yml`, and `relay-deploy-file.yml`. Several other files in `FILES` (`media-request-bot.yml`, `agw-claude.yml`, `agy-lead-plan.yml`) are also unmonitored.
3. **Synchronization Bifurcation:** Gate workflows were added on 2026-09-17 solely to the manual utility `Tools/sync_agy_files_to_forks.py`, leaving the automated CI deployment pipeline unaware of Gate 2.
4. **GitHub Actions Expression Limitation:** In `gate-webhook-health.yml`, the expression `${{ secrets.ACC6_PAT || secrets.GITHUB_TOKEN }}` relies on the GitHub Actions expression evaluator. In GitHub Actions expressions, the `||` operator returns a boolean (`true`/`false`), not the string value of the fallback token. This must be handled via bash parameter fallback or conditional assignment.

---

## 2. Architectural Comparison: relay-deploy-file.yml vs sync_agy_files_to_forks.py

The table below contrasts the automated CI relay workflow against the standalone Python synchronization tool:

| Architectural Dimension | `.github/workflows/relay-deploy-file.yml` | `Tools/sync_agy_files_to_forks.py` |
| :--- | :--- | :--- |
| **Execution Trigger** | CI automated: `push` to `main` (path-filtered) and `workflow_dispatch` | Manual CLI: executed locally or via ad-hoc runner script |
| **Transport Protocol** | Git CLI shallow clone (`git clone --depth 1`) + `git push` via Basic Auth | GitHub REST API (`PUT /repos/{owner}/agw-workers/contents/{path}`) |
| **File Scope** | 7 files (media bots, worker, issue bot, lead plan, READMEs) | 12 files (including all 4 gates, lint checkers, and injection baselines) |
| **Gate 2 Inclusion** | ❌ **Omitted** (`gate-webhook-health.yml` missing) | ✅ **Present** (`.github/workflows/gate-webhook-health.yml` synced) |
| **Gates 1, 3, 4 Inclusion** | ❌ **Omitted** (`gate-yaml-lint`, `gate-runs-on-policy`, `gate-runner-deps` missing) | ✅ **Present** (all four gates synced atomically) |
| **Readback Verification** | ❌ **None** (blind git push; ignores remote verification) | ✅ **Strict** (`GET /contents/` + byte check + `scan_file` context linter) |
| **Fleet Target Scope** | Hardcoded active rotation loop: accounts `0, 2, 4, 5, 8` | Iterates all 8 fork accounts (0, 1, 2, 3, 4, 5, 7, 8) if PAT is set |
| **Failure Tolerance** | Halts on first clone or PAT failure (`set -euo pipefail`) | Continues across accounts and reports aggregated JSON telemetry |

### Analysis of the Bifurcation
When Gate 2 and its companion gates were introduced on 2026-09-17, they were cataloged in `Tools/sync_agy_files_to_forks.py`. That script noted that all four gates must go together: Gate 4 fails an account outright if a workflow listed in `MUST_BE_HOSTED` is absent, so syncing one without its companions trades one red gate for another. However, `relay-deploy-file.yml` was never updated to include any of the four gates.

---

## 3. Token & Permission Requirements for Fork Deployment

Synchronizing `.github/workflows/` across fork repositories involves specific GitHub authorization boundaries:

1. **`workflow` OAuth Scope on Account PATs:**
   GitHub strictly enforces that creating or updating any file in `.github/workflows/` requires the `workflow` scope on Personal Access Tokens. Even with full `repo` read/write access, a PAT lacking `workflow` returns:
   `403 Refusing to allow a Personal Access Token to create or update workflow ... without 'workflow' scope`.
   All active tokens (`ACC0_PAT`, `ACC2_PAT`, `ACC4_PAT`, `ACC5_PAT`, `ACC8_PAT`) must possess the `workflow` scope.
2. **Repository Write Permissions:**
   Tokens must possess write permission to their respective target repositories (`Mohammadlali/agw-workers`, `lali94m-max/agw-workers`, `hmmletssee7-design/agw-workers`, `kidding602/agw-workers`, `moradzahra85-png/agw-workers`).
3. **Execution Context & Expression Bug Resolution:**
   GitHub Actions expressions evaluate `${{ A || B }}` as a boolean (`true`/`false`). When `secrets.ACC6_PAT` is populated, the expression evaluates to the string `"true"`, causing authentication failures. In shell steps, the token must be handled via bash parameter expansion:
   ```bash
   GH_TOKEN="${ACC6_PAT:-$GITHUB_TOKEN}"
   ```
   accompanied by explicit job-level `permissions: issues: write, contents: read`.

---

## 4. Fork Synchronization Gap Isolation & Fleet Impact

### 4.1 Missing Configuration in relay-deploy-file.yml
In `.github/workflows/relay-deploy-file.yml`:
- `on.push.paths` fails to monitor `gate-webhook-health.yml`, `media-request-bot.yml`, `agw-claude.yml`, `agy-lead-plan.yml`, and companion gates.
- The `FILES` array omits all four gate workflows.

### 4.2 Impact on Fork Fleet Runs
- Control repo run `42692993945` on `d4186eb` correctly isolated the 404 quarantine response as a configuration issue and handled alerting cleanly.
- Fork runs on `hmmletssee7-design` (run `42692590887`) and `Mohammadlali` (run `42693445322`) executed stale definitions lacking the resilience logic, causing step 4 to crash on missing label permissions and marking the runs as failed.

### 4.3 Out-of-Rotation Accounts (ACC1, ACC3, ACC7)
Per owner instruction (2026-10-24), accounts 1, 3, and 7 were temporarily removed from active rotation due to runner API capacity constraints. However, as long as they remain excluded from `relay-deploy-file.yml`, their workflows will remain stale. When rotating these accounts back in, their PATs must be verified and their repositories synchronized via manual dispatch of `Tools/sync_agy_files_to_forks.py` or by expanding the loop in `relay-deploy-file.yml`.

---

## 5. Verified Configuration Changes & Unified Diff

To keep `gate-webhook-health.yml` and all companion gates synchronized, and to ensure automated triggers fire on any workflow update:
1. Update `on.push.paths` to cover all deployed workflow files.
2. Add all four gate workflows (`gate-yaml-lint.yml`, `gate-runs-on-policy.yml`, `gate-webhook-health.yml`, `gate-runner-deps.yml`) to the `FILES` array in `relay-deploy-file.yml`.

The verified unified diff is saved at `Reports/audit/gate-webhook-health-relay-deploy.patch`.

### Complete Unified Diff Deliverable
```diff
diff --git a/.github/workflows/relay-deploy-file.yml b/.github/workflows/relay-deploy-file.yml
index fc9a0ae..7cfcb33 100644
--- a/.github/workflows/relay-deploy-file.yml
+++ b/.github/workflows/relay-deploy-file.yml
@@ -5,8 +5,15 @@ on:
   push:
     branches: [main]
     paths:
+      - '.github/workflows/media-request-bot.yml'
+      - '.github/workflows/agw-claude.yml'
       - '.github/workflows/agw-worker.yml'
       - '.github/workflows/agy-issue-bot.yml'
+      - '.github/workflows/agy-lead-plan.yml'
+      - '.github/workflows/gate-yaml-lint.yml'
+      - '.github/workflows/gate-runs-on-policy.yml'
+      - '.github/workflows/gate-webhook-health.yml'
+      - '.github/workflows/gate-runner-deps.yml'
       - '.github/workflows/relay-deploy-file.yml'
 
 permissions:
@@ -45,6 +52,10 @@ jobs:
             ".github/workflows/agy-issue-bot.yml"
             ".github/workflows/agw-worker.yml"
             ".github/workflows/agy-lead-plan.yml"
+            ".github/workflows/gate-yaml-lint.yml"
+            ".github/workflows/gate-runs-on-policy.yml"
+            ".github/workflows/gate-webhook-health.yml"
+            ".github/workflows/gate-runner-deps.yml"
             "media/README.md"
             "README.md"
           )
```

---

## 6. Verification Procedure & Rollout Strategy

1. **Patch Cleanliness Validation:**
   The patch applies cleanly against the repository with zero offset:
   ```bash
   git apply --check Reports/audit/gate-webhook-health-relay-deploy.patch
   ```
2. **Live Pre-Deployment Token Scope Audit:**
   Execute live validation against the GitHub API checking `x-oauth-scopes` headers for `workflow` and `repo`:
   ```bash
   for IDX in 0 2 4 5 8; do
     PAT_VAR="ACC${IDX}_PAT"
     TOKEN="${!PAT_VAR}"
     if [ -n "$TOKEN" ]; then
       SCOPES=$(curl -sI -H "Authorization: Bearer $TOKEN" https://api.github.com/user | grep -i "^x-oauth-scopes:" | tr -d '\r')
       echo "Account $IDX: $SCOPES"
       if echo "$SCOPES" | grep -q "workflow"; then
         echo "  ✅ Account $IDX has 'workflow' scope"
       else
         echo "  ❌ Account $IDX MISSING 'workflow' scope"
       fi
     fi
   done
   ```
3. **Authenticated Execution & Readback Verification:**
   Trigger `relay-deploy-file.yml` via `workflow_dispatch`. Verify deployment on each active fork using authenticated API readback:
   ```bash
   for IDX in 0 2 4 5 8; do
     PAT_VAR="ACC${IDX}_PAT"
     TOKEN="${!PAT_VAR}"
     OWNER="${OWNERS[$IDX]}"
     REMOTE_SHA=$(curl -s -H "Authorization: Bearer $TOKEN" \
       -H "Accept: application/vnd.github+json" \
       "https://api.github.com/repos/${OWNER}/agw-workers/contents/.github/workflows/gate-webhook-health.yml" \
       | jq -r .sha)
     echo "$OWNER Gate 2 blob SHA: $REMOTE_SHA"
   done
   ```
4. **Gate 2 Post-Sync Health Check:**
   Trigger Gate 2 on each fork (`gh workflow run gate-webhook-health.yml --repo <owner>/agw-workers`). Confirm that each run executes resilience logic from `d4186eb`, properly classifies 404 responses, and avoids label creation crashes.

---

## 7. Architectural Recommendations & Sign-Off

1. **Atomic Gate Synchronization:** Always deploy all four gates (`gate-yaml-lint.yml`, `gate-runs-on-policy.yml`, `gate-webhook-health.yml`, `gate-runner-deps.yml`) together to avoid cross-gate dependency mismatches (e.g. Gate 4 `MUST_BE_HOSTED` checks).
2. **Readback Probe:** Integrate automated SHA readback into `relay-deploy-file.yml` after git push to detect silent sync drops.
3. **Sign-Off:** Documented and verified by `agw-ACC4-w03` (Account 4 Lead Team). Patch validated with `git apply --check` and ready for merge.
