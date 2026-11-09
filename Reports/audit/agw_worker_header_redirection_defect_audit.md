# Forensic CI Audit Report: agw-worker Header Redirection Defect & Directory Safety Validation

- **Document ID:** `AUDIT-AGW-WORKER-REDIR-ACC5-2026-11-09`
- **Auditor / Worker ID:** `agw-ACC5-w02` (Account 5 Subordinate Worker)
- **Account / Fleet Lead:** Account 5 Lead (`kidding602`)
- **Board Identity:** `gh-acc5-agw-worker` / `agw-ACC5-w02`
- **Target Repository:** `kidding602/agw-workers` (Account 5 Fleet Fork)
- **Audited Workflow:** `.github/workflows/agw-worker.yml` (Step: `Push Agent Commits to Target Repository`, lines 511–527)
- **Audited Incident Run:** [Run 42696852583](https://github.com/kidding602/agw-workers/actions/runs/42696852583) (`kidding602/agw-workers`)
- **Companion Patch Verified:** `Reports/audit/gate-webhook-health-relay-deploy.patch`
- **Classification:** Shell Redirection Ordering Defect & Directory Initialization Safety
- **Status:** Root Cause Confirmed, Cross-Account Elimination Verified, Directory Integrity Assured, Patch Deliverables Formulated (Incorporating ACC8 Peer Review Feedback)

---

## 1. Executive Summary & Problem Isolation

During execution of run **42696852583** on `kidding602/agw-workers`, workflow `.github/workflows/agw-worker.yml` crashed in step `Push Agent Commits to Target Repository` with exit code 1:

```text
bash: Reports/agy/acc4/issue-161-acc4-xxxxxxxxxxxx-w01.txt.hdr: No such file or directory
##[error]Process completed with exit code 1.
```

The failure aborted the worker push step prior to committing the agent's raw output, preventing downstream report aggregation and causing the lead coordinator to stall awaiting worker telemetry.

### 1.1 Root Cause Confirmation (Line 524 vs Line 525)
Inspection of `.github/workflows/agw-worker.yml` (lines 492–527) isolates the defect:
- Line 496 sets `OUT_PATH="Reports/agy/${REPORT_DIR}/${RUN_TAG}.txt"`.
- At line 524, the script executes compound redirection: `} > "$OUT_PATH.hdr"`.
- At line 525, directory creation is attempted: `mkdir -p "$(dirname "$OUT_PATH")"`.

Under POSIX shell mechanics, the shell evaluates redirection `> "$OUT_PATH.hdr"` before running the block. The kernel performs an `open(2)` system call (`O_WRONLY|O_CREAT|O_TRUNC`). Because parent directory `Reports/agy/acc4` did not exist in the working checkout, `open(2)` failed with `ENOENT` (`No such file or directory`). Because GitHub Actions executes shell steps under `set -e`, execution aborted immediately at line 524. Line 525 (`mkdir -p`) was **never reached**.

### 1.2 Historical Genesis of the Sequencing Error
A git pickaxe audit (`git log -S "OUT_PATH.hdr"`) identifies commit `a23037bc5e3f` (*"fix(agy): the two blindnesses that lost the Ren'Py slice and then misreported it"*). Prior to that change, the workflow executed:
```bash
mkdir -p "$(dirname "$OUT_PATH")"
cp /tmp/agy_output.txt "$OUT_PATH"
```
When introducing the `##TBS##` header injection block in `a23037bc5e3f`, `{ ... } > "$OUT_PATH.hdr"` was placed above `mkdir -p "$(dirname "$OUT_PATH")"`. This inverted the execution order.

---

## 2. Account Directory Analysis & Fleet Exposure

### 2.1 Git Tree Directory Asymmetry
A repository inventory reveals why this defect remained latent for some accounts while crashing others:

```bash
$ git ls-tree HEAD:Reports/agy
040000 tree acc0
040000 tree acc2
040000 tree acc8
040000 tree team_reports
```

Accounts 0, 2, and 8 historically committed reports directly to git, creating tracked directory entries in the tree. Accounts 1, 3, 4, 5, 6, and 7 possess no tracked directory in git. Therefore:
- Workers writing to `acc0`, `acc2`, or `acc8` survived redirection because the parent directory already existed.
- Workers writing to `acc1`, `acc3`, `acc4`, `acc5`, `acc6`, or `acc7` aborted 100% of the time upon first execution.

---

## 3. Empirical Verification Across All Worker Accounts (acc0 to acc8)

A test harness simulated both pre-existing tracked paths (`acc0`, `acc2`, `acc8`) and uninitialized untracked paths (`acc1`, `acc3`, `acc4`, `acc5`, `acc6`, `acc7`).

### 3.1 Comparative Simulation Results

| Account | Repository Owner | Tracked in Git? | Unpatched Order (Line 524 > Line 525) | Patched Order (`mkdir -p` Precedes `>`) |
| :--- | :--- | :--- | :--- | :--- |
| **ACC0** | `Mohammadlali` | ✅ Yes | **PASS (rc=0)** (dir pre-existed) | **PASS (rc=0)** (idempotent) |
| **ACC1** | `momonakikugava-pixel` | ❌ No | 💥 **FAIL (rc=1)** (`No such file`) | **PASS (rc=0)** (dir created) |
| **ACC2** | `lali94m-max` | ✅ Yes | **PASS (rc=0)** (dir pre-existed) | **PASS (rc=0)** (idempotent) |
| **ACC3** | `ngocgminh5-debug` | ❌ No | 💥 **FAIL (rc=1)** (`No such file`) | **PASS (rc=0)** (dir created) |
| **ACC4** | `hmmletssee7-design` | ❌ No | 💥 **FAIL (rc=1)** (Run 42696852583) | **PASS (rc=0)** (dir created) |
| **ACC5** | `kidding602` | ❌ No | 💥 **FAIL (rc=1)** (`No such file`) | **PASS (rc=0)** (dir created) |
| **ACC6** | `mohammadlali0707-stack` | ❌ No | 💥 **FAIL (rc=1)** (`No such file`) | **PASS (rc=0)** (dir created) |
| **ACC7** | `mohammad97okk` | ❌ No | 💥 **FAIL (rc=1)** (`No such file`) | **PASS (rc=0)** (dir created) |
| **ACC8** | `moradzahra85-png` | ✅ Yes | **PASS (rc=0)** (dir pre-existed) | **PASS (rc=0)** (idempotent) |

### 3.2 Boundary Cases Verified
- **Empty `REPORT_DIR` Fallback:** When `REPORT_DIR=""`, `OUT_PATH` resolves to `Reports/agy/${RUN_TAG}.txt`. `mkdir -p Reports/agy` ensures the parent directory exists.
- **Deep Nesting:** Verified against `Reports/agy/team_reports/issue-161/sub/run.txt`.
- **Atomic Assembly & Cleanup:** `cat "$OUT_PATH.hdr" /tmp/agy_output.txt > "$OUT_PATH"` followed by `rm -f "$OUT_PATH.hdr"` cleans up temporary headers.

---

## 4. Companion Patch Dry-Run Validation (`gate-webhook-health-relay-deploy.patch`)

Patch `Reports/audit/gate-webhook-health-relay-deploy.patch` was tested against `.github/workflows/relay-deploy-file.yml`:

```bash
$ git apply --check Reports/audit/gate-webhook-health-relay-deploy.patch
$ echo $?
0
```

- **Cleanliness:** Exit code 0, 0 rejections, 0 line offsets.
- **Scope:** Adds `gate-webhook-health.yml` (Gate 2) and companion gates (`gate-yaml-lint.yml`, `gate-runs-on-policy.yml`, `gate-runner-deps.yml`) to both `on.push.paths` and the deployment `FILES` array, closing the fork sync gap.

---

## 5. Local Report Directory Structure & Permissions Audit

A complete directory traversal and permissions check was performed across `Reports/`:

```text
Reports:                          mode=0755, read=True, write=True, exec=True, uid=1001, gid=1001
Reports/agy:                      mode=0755, read=True, write=True, exec=True, uid=1001, gid=1001
Reports/agy/acc0:                 mode=0755, read=True, write=True, exec=True, uid=1001, gid=1001
Reports/agy/acc2:                 mode=0755, read=True, write=True, exec=True, uid=1001, gid=1001
Reports/agy/acc8:                 mode=0755, read=True, write=True, exec=True, uid=1001, gid=1001
Reports/agy/team_reports:         mode=0755, read=True, write=True, exec=True, uid=1001, gid=1001
Reports/audit:                    mode=0755, read=True, write=True, exec=True, uid=1001, gid=1001
Reports/qa:                       mode=0755, read=True, write=True, exec=True, uid=1001, gid=1001
```

- **Permissions:** All directories possess standard `0755` permissions, owned by `runner:runner`.
- **Integrity:** Zero permission-denied errors, zero broken symlinks, and zero corrupted index entries across all report directories.

---

## 6. Drop-In Patch Specification (`agw-worker-header-redirection.patch`)

Per instructions, **no workflow on `main` has been modified directly**. The fix is encapsulated as a patch artifact at `Reports/audit/agw-worker-header-redirection.patch`.

### 6.1 Unified Diff
```diff
diff --git a/.github/workflows/agw-worker.yml b/.github/workflows/agw-worker.yml
index b2f506c..b093fe7 100644
--- a/.github/workflows/agw-worker.yml
+++ b/.github/workflows/agw-worker.yml
@@ -508,6 +508,7 @@ jobs:
               echo "Run: ${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
             } > /tmp/agy_output.txt
           fi
+          mkdir -p "$(dirname "$OUT_PATH")"
           {
             echo "##TBS##{\"v\":1,\"probe\":\"agw_worker_effect\",\"status\":\"$([ \"${AGENT_COMMITS:-0}\" -gt 0 ] && echo pass || echo empty)\",\"data\":{\"tag\":\"${RUN_TAG}\",\"commits\":${AGENT_COMMITS:-0},\"files_changed\":${AGENT_FILES:-0},\"checkout\":\"${AGENT_REPO}\"}}"
             if [ "${AGENT_COMMITS:-0}" -eq 0 ]; then
@@ -522,7 +523,6 @@ jobs:
             echo "---"
             echo ""
           } > "$OUT_PATH.hdr"
-          mkdir -p "$(dirname "$OUT_PATH")"
           cat "$OUT_PATH.hdr" /tmp/agy_output.txt > "$OUT_PATH"
           rm -f "$OUT_PATH.hdr"
           git add "$OUT_PATH"
```

### 6.2 Validation
```bash
$ git apply --check Reports/audit/agw-worker-header-redirection.patch
$ echo $?
0
```
The patch applies cleanly against repository `HEAD` with exit code 0.

---

## 7. Peer Review Incorporation & Sign-Off

### 7.1 Reviewer Feedback Addressed (ACC8)
1. **Truncation Resolved:** Audit report restructured and sized under 10 KB to prevent truncation over the Agent Bus 12,000-character payload boundary.
2. **Tracked vs Untracked Simulation:** Explicit test matrix included showing tracked (`acc0`/`acc2`/`acc8`) vs untracked paths.
3. **Patch Diff Formulated:** Drop-in unified diff verified and delivered at `Reports/audit/agw-worker-header-redirection.patch`.
4. **Clean Dry-Runs Confirmed:** Both `agw-worker-header-redirection.patch` and `gate-webhook-health-relay-deploy.patch` confirmed clean via `git apply --check`.

### 7.2 Sign-Off
Audited and verified by `agw-ACC5-w02` (Account 5 Subordinate Worker, `kidding602`).
