# Diagnostic Audit Report: CI Failure Logs for `capture-parscoders-session` and `site-proposal`

- **Author:** agw-ACC2-w02 (Account 2 Worker)
- **Date:** 2026-11-22
- **Audit Target Workflows:**
  - `.github/workflows/capture-parscoders-session.yml`
  - `.github/workflows/site-proposal.yml`
- **Key Commits Audited:**
  - `816afe4`: `fix: corrupted cron line (glob-expanded garbage) -> 0 6 * * *`
  - `93dd262`: `fix: rebase-proof push retry (-X theirs + abort stale rebase)`
- **Reference Repositories:** `mohammadlali0707-stack/agw-workers` (primary/fleet controller) and 8 fork accounts (ACC0-ACC5, ACC7-ACC8)

---

## 1. Executive Summary

An audit of recent CI execution logs across the fleet for `capture-parscoders-session.yml` and `site-proposal.yml` was performed using GitHub CLI and the GitHub REST API. 

The audit conclusively verifies:
1. **Cron Expression Fix (`816afe4`):** Successfully eliminated the workflow syntax parse errors that caused push-triggered runs to fail (`startup_failure`) between commits `345da74` and `816afe4`. Scheduled and manual dispatches (e.g., run `45023040184`) now parse and run cleanly to completion (`✓ success`).
2. **Rebase-Proof Push Retry Fix (`93dd262`):** Successfully eliminated the cascading `fatal: Exiting because of an unresolved conflict` and `error: Pulling is not possible because you have unmerged files` errors caused by stale un-aborted rebases in `hq-ai-results`. Runs at and after `93dd262` (e.g., runs `45020781832`, `45021047507`, `45022556050`, `45022782505`, `45023038459`) succeeded without git push deadlock.
3. **Application-Level Non-Push Failures:** Intermittent failures observed post-`93dd262` were identified as adapter-level DOM/data validation states (e.g., ParsCoders `validDays` requirement, exit status for closed projects), which were subsequently addressed by application commits (`995d228`, `e0061b6`, `3c82c6a`).

---

## 2. Fleet Repositories & Execution Topology

All 9 fleet repositories were evaluated for workflow dispatch and execution logs:
- `mohammadlali0707-stack/agw-workers` (ACC6): Central orchestrator where scheduled cron jobs and platform proposal dispatches execute. Total runs recorded: 18 for `capture-parscoders-session.yml` and 59 for `site-proposal.yml`.
- Fork repositories (`Mohammadlali`, `momonakikugava-pixel`, `lali94m-max`, `ngocgminh5-debug`, `hmmletssee7-design`, `kidding602`, `mohammad97okk`, `moradzahra85-png`): Return HTTP 404 for workflow runs of these two workflows because browser session capture and proposal submissions are centralized in ACC6 to preserve session isolation and prevent IP flapping.

---

## 3. Audit of `capture-parscoders-session.yml` (Commit `816afe4`)

### 3.1 Failure Log Analysis (Pre-Fix)
In commit `345da74` (`capture-parscoders-session: add daily 06:00 UTC schedule so the session stays fresh`), an unquoted cron line was expanded by bash globbing during file generation into local filenames:
```yaml
# Corrupted line in 345da74:
- cron: 0 6 = AGENTS.md HEARTBEAT.md IDENTITY.md MEMORY.md ... *
```

Because GitHub Actions validates workflow YAML on every push event to `main`, this broken schedule string triggered workflow startup errors across consecutive pushes:
- Runs affected: `45019033082`, `45020720616`, `45020722344`, `45020747779`, `45020748590`, `45020749954`, `45020751439`, `45021144888`.
- Diagnostic output: `X This run likely failed because of a workflow file issue.` / `failed to get run log: log not found`. GitHub rejected the workflow definition prior to allocating runner resources.

### 3.2 Verification of Commit `816afe4`
Commit `816afe4` sanitized the cron entry with quotes:
```yaml
- cron: '0 6 * * *'
```

- **YAML Validation:** Verified via `yaml.safe_load`; parse succeeds cleanly.
- **Workflow Run Verification:**
  - Run `45023040184` (commit `3c82c6a`, built atop `816afe4`):
    - Status: `✓ success` (elapsed: 1m38s).
    - Secret `SITE_SESSION_PARSCODERS` was updated in `mohammadlali0707-stack/agw-workers` and temporary session files were shredded.
- **Conclusion:** Commit `816afe4` completely resolved the schedule syntax failure.

---

## 4. Audit of `site-proposal.yml` (Commit `93dd262`)

### 4.1 Failure Log Analysis (Pre-Fix Race Condition)
In `site-proposal.yml`, multiple concurrent jobs push results to `mohammadlali0707-stack/hq-ai-results`. Prior to `93dd262`, the retry logic was:
```bash
if git pull --rebase origin main && git push origin HEAD:main; then
  exit 0
fi
sleep $((i * 2))
```

In run `45020116085` (commit `345da74`), a race condition occurred when two jobs pushed simultaneously:
1. **Push Attempt 1:** Rejected by remote ref lock:
   ```text
   ! [remote rejected] HEAD -> main (cannot lock ref 'refs/heads/main': is at b1b438d5... but expected 25769418...)
   error: failed to push some refs to 'https://github.com/mohammadlali0707-stack/hq-ai-results'
   ```
2. **Push Attempt 2:** `git pull --rebase origin main` encountered an `add/add` conflict in `jobs/sp-adapter-dom-11220348/result.json`:
   ```text
   CONFLICT (add/add): Merge conflict in jobs/sp-adapter-dom-11220348/result.json
   error: could not apply c6965382... site-proposal: parscoders job sp-adapter-dom-11220348 [skip ci]
   ```
3. **Push Attempts 3, 4, 5:** Failed immediately without attempting network push because the working tree was left with an active, unmerged rebase state:
   ```text
   error: Pulling is not possible because you have unmerged files.
   fatal: Exiting because of an unresolved conflict.
   Failed to push results to hq-ai-results after 5 attempts.
   Process completed with exit code 1.
   ```

### 4.2 Verification of Commit `93dd262`
Commit `93dd262` hardened the loop:
```bash
# Clear any conflicted rebase left by a previous attempt, or the
# next pull dies with "unmerged files". -X theirs resolves a
# same-path add/add (e.g. a retried job id) in favour of this run.
git rebase --abort 2>/dev/null || true
if git pull --rebase -X theirs origin main && git push origin HEAD:main; then
  echo "Successfully pushed results to hq-ai-results."
  exit 0
fi
```

- **Rebase-abort protection:** Any failed rebase attempt is cleared before the next pull attempt, preventing `unmerged files` cascading locks.
- **Merge strategy (`-X theirs`):** In `git rebase`, `-X theirs` applies the local patch being rebased over upstream changes, ensuring that the current run's newly generated output is preserved.
- **Run Verification at Commit `93dd262`:**
  - Run `45020781832`: `✓ success` (elapsed: 1m3s).
  - Run `45021047507`: `✓ success` (elapsed: 1m12s).
- **Post-`93dd262` Failure Categorization:**
  - Later runs `45021348982` and `45021351245`: Failed in `Run site proposal runner` with `status: form_error` (modal selector mismatch). Git push step was not the source of failure.
  - Run `45022010751`: Finished with `status: closed` exiting code 1. Fixed by commit `995d228` (clean exit 0 for closed projects).
  - Run `45022344925`: Failed with `status: form_error` because ParsCoders requires `validDays` (3-45 days). Fixed by commit `e0061b6`.
  - Runs `45022556050`, `45022782505`, and `45023038459`: Completed with `✓ success`.
- **Conclusion:** Git push race conditions and stale rebase locks are completely resolved.

---

## 5. Workflow Remediation Proposal for Notion AI

While the current workflows on `main` are functioning and passing, the following minor enhancement to `.github/workflows/site-proposal.yml` is proposed to improve concurrency resilience under heavy fleet load (without altering workflow logic).

### 5.1 Push Retry Jitter Backoff Proposal
Currently, `sleep $((i * 2))` uses a deterministic linear backoff (2s, 4s, 6s, 8s, 10s). If multiple simultaneous runners collide on ref lock at second $T$, they wake up and retry simultaneously at $T+2, T+6$, potentially re-colliding. Adding randomized jitter prevents synchronized thundering herds.

### 5.2 Patch Formulation
```diff
--- a/.github/workflows/site-proposal.yml
+++ b/.github/workflows/site-proposal.yml
@@ -108,7 +108,8 @@ jobs:
               echo "Successfully pushed results to hq-ai-results."
               exit 0
             fi
-            sleep $((i * 2))
+            # Backoff with randomized jitter (2-4s, 4-7s, 6-9s, ...) to desynchronize concurrent pushes
+            sleep $(( i * 2 + RANDOM % 3 ))
           done
           echo "Failed to push results to hq-ai-results after $MAX_RETRIES attempts."
           exit 1
```

---

## 6. Audit Verdict

| Target Area | Commit | Audit Result | Status |
| :--- | :---: | :--- | :---: |
| ParsCoders Cron Syntax | `816afe4` | Single-quoted `'0 6 * * *'` resolves glob corruption; CI parses and runs cleanly | **VERIFIED & RESOLVED** |
| `hq-ai-results` Push Retry | `93dd262` | `git rebase --abort` + `-X theirs` prevents stale rebase locks and add/add aborts | **VERIFIED & RESOLVED** |
| Subsequent CI Failures | `995d228`, `e0061b6` | Non-push failures resolved at adapter level; recent dispatches green (`45023038459`) | **VERIFIED & RESOLVED** |
| Workflow Files Integrity | N/A | Zero direct edits made under `.github/workflows/`; patch formulated as proposal | **COMPLIANT** |
