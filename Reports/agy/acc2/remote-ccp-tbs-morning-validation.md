# Patch Validation & Diagnostic Audit Report: Gate 1 Injection in `remote-ccp-tbs-morning.yml`

- **Worker ID:** `agw-ACC2-w03`
- **Account:** Account 2 (`agw-ACC2`)
- **Task:** Verify Gate 1 injection patch and post proposal to Notion AI / Mohammad
- **Date:** 2026-10-28
- **Target Repositories:** `mohammadlali0707-stack/agw-workers`, `Mohammadlali/agw-workers`, `lali94m-max/agw-workers`, `hmmletssee7-design/agw-workers`, `kidding602/agw-workers`, `moradzahra85-png/agw-workers`
- **Target Workflow:** `.github/workflows/remote-ccp-tbs-morning.yml`
- **Target Issue:** Issue #123 (`[CI] failures from e-mail — 2026-10-28`) on `mohammadlali0707-stack/agw-workers`
- **Delivery Destination:** Structured issue comment on GitHub Issue #123 for Notion AI review and owner (Mohammad) sign-off
- **Status:** Verified in Isolated Test Environment; Gate 1 Quality Gates Passed (Exit code 0, status `pass`, 0 new/grown untrusted expressions); 0 Direct Modifications to `.github/workflows/` in Repository; Isolation Test Artifacts Cleaned Up

---

## 1. Executive Summary

Continuous integration runs of Gate 1 (`.github/workflows/gate-yaml-lint.yml`) failed across all fleet accounts (`Mohammadlali`, `mohammadlali0707-stack`, `hmmletssee7-design`, `lali94m-max`, `kidding602`, `moradzahra85-png`). The failure occurs in step `No new untrusted expression in a run body` executing `python3 Tools/check_run_injection.py .github/workflows`.

The root cause was isolated to line 166 of `.github/workflows/remote-ccp-tbs-morning.yml`, where expression `${{ github.event.inputs.force }}` is interpolated directly inside the inline bash `run:` body of step `Check for an existing report (idempotency guard)`. Because this expression is not grandfathered in `Tools/injection_baseline.json`, `Tools/check_run_injection.py` flags it as a new script injection vulnerability and fails Gate 1 with exit code 1.

As `agw-ACC2-w03`, the unified diff patch formulated to resolve this issue was verified in an isolated test environment:
1. **Isolated Verification:** In an isolated test copy of `.github/workflows/`, the patch was applied and verified using `Tools/check_run_injection.py`. Gate 1 passed with exit code `0`, probe status `"pass"`, 0 new, and 0 grown untrusted expressions.
2. **Patch Cleanliness:** Verified via `git apply --check Reports/agy/acc2/remote-ccp-tbs-morning.patch` (exit code `0`).
3. **Context Availability:** Validated via `python3 Tools/check_job_env_contexts.py` (0 violations across all 85 workflows).
4. **Governance Compliance:** Zero direct edits to `.github/workflows/` in the repository checkout on branch `main`.
5. **Isolation Artifact Cleanup:** All isolation directories (`isolated_test/`) have been removed; working tree is verified clean.
6. **Notion AI & Owner Review:** Structured technical diagnostic and complete unified diff patch proposal prepared for submission as an issue comment on Issue #123.

---

## 2. Root Cause Analysis

### Vulnerability Mechanism
In GitHub Actions, workflow expressions `${{ ... }}` appearing inside a step's `run:` block are expanded by GitHub's runner engine as text substitutions *before* the script is handed off to the system shell (`bash`). If an untrusted input such as `${{ github.event.inputs.force }}` contains quotes, shell metacharacters, or command separators (e.g. `;`, `|`, `&&`), those characters alter the execution semantics of the script, resulting in command injection (CWE-78).

### Error Location in `.github/workflows/remote-ccp-tbs-morning.yml`
In the `Check for an existing report (idempotency guard)` step (lines 156–171):

```yaml
156:       - name: Check for an existing report (idempotency guard)
157:         id: guard
158:         env:
159:           GH_TOKEN: ${{ secrets.ACC6_PAT }}
160:         run: |
161:           exec >>/tmp/private_step.log 2>&1   # public repo: no private output in logs
162:           export GITHUB_REPOSITORY=mohammadlali0707-stack/Claud-Cloud-Project
163:           DATE="$(date -u +%F)"
164:           REPORT="Reports/morning/${DATE}.md"
165:           TITLE="گزارش صبحگاهی — ${DATE}"
166:           FORCE="${{ github.event.inputs.force }}"
167:           if [ -f "$REPORT" ] && [ "$FORCE" != "true" ]; then
168:             echo "skip=true" >> "$GITHUB_OUTPUT"
169:             echo "SKIP: ${REPORT} already exists for ${DATE} and force was not" \
170:                  "set -- not filing a duplicate report."
171:           else
```

On line 166, `${{ github.event.inputs.force }}` is directly expanded in the script text instead of being passed safely via the step-level `env:` mapping.

### Pre-Patch Empirical Probe Output
Command:
```bash
python3 Tools/check_run_injection.py .github/workflows
```
Output:
```
::error file=.github/workflows/remote-ccp-tbs-morning.yml::NEW untrusted expression in a run: body -- github.event.inputs.force. GitHub pastes this into the script text before bash runs it. Pass it through a step-level env: and use the variable instead.
28 location(s), 56 occurrence(s); 1 new, 0 grown, 0 fixed
##TBS##{"data": {"fixed": 0, "grown": [], "locations": 28, "new": ["remote-ccp-tbs-morning.yml::github.event.inputs.force"], "occurrences": 56}, "probe": "run_injection", "status": "fail", "v": 1}
```
Exit code: `1` (`fail`).

---

## 3. Remediation & Unified Diff Patch

To remediate the vulnerability while maintaining complete operational parity:
1. Map `FORCE: ${{ github.event.inputs.force }}` into the step-level `env:` block of `Check for an existing report (idempotency guard)`.
2. Replace `${{ github.event.inputs.force }}` with `${FORCE:-false}` in the bash `run:` script on line 166.

### Exact Unified Diff Patch (`Reports/agy/acc2/remote-ccp-tbs-morning.patch`)
```diff
diff --git a/.github/workflows/remote-ccp-tbs-morning.yml b/.github/workflows/remote-ccp-tbs-morning.yml
index a737274..362b083 100644
--- a/.github/workflows/remote-ccp-tbs-morning.yml
+++ b/.github/workflows/remote-ccp-tbs-morning.yml
@@ -157,13 +157,14 @@ jobs:
         id: guard
         env:
           GH_TOKEN: ${{ secrets.ACC6_PAT }}
+          FORCE: ${{ github.event.inputs.force }}
         run: |
           exec >>/tmp/private_step.log 2>&1   # public repo: no private output in logs
           export GITHUB_REPOSITORY=mohammadlali0707-stack/Claud-Cloud-Project
           DATE="$(date -u +%F)"
           REPORT="Reports/morning/${DATE}.md"
           TITLE="گزارش صبحگاهی — ${DATE}"
-          FORCE="${{ github.event.inputs.force }}"
+          FORCE="${FORCE:-false}"
           if [ -f "$REPORT" ] && [ "$FORCE" != "true" ]; then
             echo "skip=true" >> "$GITHUB_OUTPUT"
             echo "SKIP: ${REPORT} already exists for ${DATE} and force was not" \
```

---

## 4. Empirical Verification in Isolated Environment

### A. Isolated Environment Setup & Injection Probe
An isolated copy of all repository workflows was created under `isolated_test/workflows/`. The unified diff patch was applied to `remote-ccp-tbs-morning.yml`.

Command:
```bash
python3 Tools/check_run_injection.py isolated_test/workflows
```
Output:
```
27 location(s), 55 occurrence(s); 0 new, 0 grown, 0 fixed
##TBS##{"data": {"fixed": 0, "grown": [], "locations": 27, "new": [], "occurrences": 55}, "probe": "run_injection", "status": "pass", "v": 1}
```
Exit code: `0` (`pass`).

### B. Patch Cleanliness Check
Command:
```bash
git apply --check Reports/agy/acc2/remote-ccp-tbs-morning.patch
```
Exit code: `0` (clean application without conflict or fuzz against current `main`).

### C. Job-Level Context Availability Check
Command:
```bash
python3 Tools/check_job_env_contexts.py isolated_test/workflows
```
Output:
```
85 workflow file(s) scanned, 0 violation(s), discriminates=True
##TBS##{"data": {"discriminates": true, "files": 85, "violations": []}, "probe": "job_env_contexts", "status": "pass", "v": 1}
```
Exit code: `0` (`pass`).

### D. Full Gate 1 Structure and Syntax Validation
All 85 workflows validated for CRLF endings, YAML syntax, and structural requirements (valid jobs with steps): `PASSED` (0 errors).

### E. Fleet Governance & Workspace Hygiene Verification
```bash
git status --porcelain .github/workflows/
```
Output: (empty) — 0 files modified under `.github/workflows/` in the repository checkout.
All isolation directories (`isolated_test/`) have been removed cleanly.

---

## 5. Review & Deployment Instructions

Per fleet governance, workflow changes are not committed directly by workers on `main`.
This proposal is delivered as a structured issue comment on GitHub Issue #123 (`mohammadlali0707-stack/agw-workers`) for Notion AI review and sign-off by repository maintainer Mohammad:

1. **Target File:** `.github/workflows/remote-ccp-tbs-morning.yml`
2. **Patch Source:** `Reports/agy/acc2/remote-ccp-tbs-morning.patch`
3. **Application:** `git apply Reports/agy/acc2/remote-ccp-tbs-morning.patch`
4. **Verification:** Run `python3 Tools/check_run_injection.py .github/workflows` to confirm Gate 1 exit code 0.
5. **Deployment:** Commit and push to `main` across `mohammadlali0707-stack/agw-workers` and fleet mirror repositories (`Mohammadlali`, `lali94m-max`, `hmmletssee7-design`, `kidding602`, `moradzahra85-png`).
