# Diagnostic Report: Gate 1 Injection Failure & `remote-ccp-tbs-morning.yml` Patch

- **Worker ID:** `agw-ACC2-w02`
- **Account:** Account 2 (`agw-ACC2`)
- **Date:** 2026-10-28
- **Target Repositories:** `mohammadlali0707-stack`, `Mohammadlali`, `lali94m-max`, `hmmletssee7-design`, `kidding602`, `moradzahra85-png`
- **Component:** `.github/workflows/remote-ccp-tbs-morning.yml`
- **Gate:** `.github/workflows/gate-yaml-lint.yml` (Step: `No new untrusted expression in a run body`)
- **Detection Script:** `Tools/check_run_injection.py`
- **Baseline File:** `Tools/injection_baseline.json`

---

## 1. Executive Summary

Continuous integration workflows across all six fleet repositories (`mohammadlali0707-stack`, `Mohammadlali`, `lali94m-max`, `hmmletssee7-design`, `kidding602`, `moradzahra85-png`) failed on **Gate 1 (YAML Lint & Workflow Validator)** during the step `No new untrusted expression in a run body`.

Investigation confirmed that the failure is triggered by `Tools/check_run_injection.py` scanning `.github/workflows/remote-ccp-tbs-morning.yml`. On line 166 of `.github/workflows/remote-ccp-tbs-morning.yml`, the expression `${{ github.event.inputs.force }}` was directly interpolated inside the `run:` body of the `guard` step (`Check for an existing report (idempotency guard)`). Because this file and expression combination was not registered in `Tools/injection_baseline.json`, the validator flagged it as a newly introduced script injection risk and exited with status code `1`.

In strict compliance with fleet governance rules prohibiting automated worker accounts from directly modifying files under `.github/workflows/`, this document provides the complete root cause diagnosis, the baseline mechanism explanation, empirical verification outputs, and the exact minimal unified diff patch ready for repository lead deployment.

---

## 2. Baseline Architecture & Detection Mechanism

### Evolution from CHECK-1 to `check_run_injection.py`
Historically, Gate 1 used `CHECK-1` to forbid double quotes inside single-quoted `printf` content strings as an indirect heuristic against bash string breakage. However, `CHECK-1` proved defective: it produced false positives on legitimate JSON template strings in workflow files (such as `agy-plan-bot.yml`), while failing to reliably detect untrusted context interpolation elsewhere.

Consequently, `CHECK-1` was retired and replaced by `Tools/check_run_injection.py`. This tool targets the primary injection vector: direct interpolation of untrusted input expressions `${{ inputs.* }}` and `${{ github.event.* }}` inside a `run:` body.

### Ratcheted Baseline (`Tools/injection_baseline.json`)
At the time of introduction, 27 unique file-and-expression locations (accounting for 55 occurrences) were already present across legacy workflow files. Converting all legacy instances into hard failures simultaneously would have blocked CI across the entire fleet.

To avoid alert fatigue while stopping regressions, `Tools/check_run_injection.py` implements a ratcheted baseline stored in `Tools/injection_baseline.json`:
1. **Key Structure:** Each finding is recorded as `<filename>::<expression>` (e.g. `tbs-worker.yml::github.event.inputs.max_parallel`). This key design ensures that line number shifts or indentation changes do not register as false positive modifications.
2. **Legacy Tolerance:** Existing entries recorded in `Tools/injection_baseline.json` are reported as informational entries.
3. **Hard Failure on New Occurrences:** Any finding `k` where `k not in base` or where `v > base[k]` triggers a hard CI failure (exit code 1).
4. **Ratchet Down:** Fixed entries are reported, enabling `--update` runs to shrink the baseline over time without allowing new additions.

---

## 3. Failure Trigger & Root Cause Analysis

### Vulnerability Mechanism (CWE-78)
In GitHub Actions, workflow template expressions enclosed in `${{ ... }}` are evaluated and expanded as raw string substitutions by the runner *before* the script text is handed over to the shell process (`bash`). 

When an untrusted input such as `${{ github.event.inputs.force }}` is placed directly inside a `run:` string:
```bash
FORCE="${{ github.event.inputs.force }}"
```
If an adversary or payload supplies quotes, backticks, dollar signs, command separators (`;`, `&&`, `|`), or newline characters within `inputs.force`, the runner pastes those characters literally into the bash script body. This breaks out of string delimiters and executes arbitrary shell commands under the permissions of the runner (`GITHUB_TOKEN` / `ACC6_PAT`).

The secure design pattern is to pass untrusted inputs through step-level environment variables:
```yaml
env:
  FORCE: ${{ github.event.inputs.force }}
```
In this model, the runner sets `FORCE` in the environment of the child process. Bash reads the value purely as data via `$FORCE`, rendering quote characters and metacharacters completely inert.

### Location in `.github/workflows/remote-ccp-tbs-morning.yml`
In `.github/workflows/remote-ccp-tbs-morning.yml`, lines 156–171:

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

On line 166, `FORCE="${{ github.event.inputs.force }}"` was placed in the script body rather than under `env:`.

### Detection Output
Running `python3 Tools/check_run_injection.py .github/workflows` reproduces the failure:

```
::error file=.github/workflows/remote-ccp-tbs-morning.yml::NEW untrusted expression in a run: body -- github.event.inputs.force. GitHub pastes this into the script text before bash runs it. Pass it through a step-level env: and use the variable instead.
28 location(s), 56 occurrence(s); 1 new, 0 grown, 0 fixed
##TBS##{"data": {"fixed": 0, "grown": [], "locations": 28, "new": ["remote-ccp-tbs-morning.yml::github.event.inputs.force"], "occurrences": 56}, "probe": "run_injection", "status": "fail", "v": 1}
```

The script exits with code `1`, causing Gate 1 to fail.

---

## 4. Fleet Impact

The `.github/workflows/remote-ccp-tbs-morning.yml` workflow file is synchronized across all six worker fleet repositories:
1. `mohammadlali0707-stack`
2. `Mohammadlali`
3. `lali94m-max`
4. `hmmletssee7-design`
5. `kidding602`
6. `moradzahra85-png`

Because `gate-yaml-lint.yml` runs on every push and pull request targeting `.github/workflows/**.yml` or `.github/workflows/**.yaml`, this injection violation fails Gate 1 uniformly across the entire fleet.

---

## 5. Unified Diff Patch

To resolve the failure while maintaining full behavioral parity:
1. Define `FORCE: ${{ github.event.inputs.force }}` under the step-level `env:` block of step `guard`.
2. Remove the inline assignment `FORCE="${{ github.event.inputs.force }}"` from line 166 in the `run:` body. In bash, the variable `$FORCE` is already populated in the process environment from `env:`, allowing expressions like `[ "$FORCE" != "true" ]` to evaluate correctly and securely.

### Exact Unified Diff

```diff
diff --git a/.github/workflows/remote-ccp-tbs-morning.yml b/.github/workflows/remote-ccp-tbs-morning.yml
index 60db894..166d11d 100644
--- a/.github/workflows/remote-ccp-tbs-morning.yml
+++ b/.github/workflows/remote-ccp-tbs-morning.yml
@@ -157,13 +157,13 @@ jobs:
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
           if [ -f "$REPORT" ] && [ "$FORCE" != "true" ]; then
             echo "skip=true" >> "$GITHUB_OUTPUT"
             echo "SKIP: ${REPORT} already exists for ${DATE} and force was not" \
```

This patch is saved in the repository as:
- `Reports/agy/acc2/remote-ccp-tbs-morning.patch`
- `patches/remote-ccp-tbs-morning.patch`

---

## 6. Verification & Empirical Probe Results

### Pre-Patch Validation
```bash
$ python3 Tools/check_run_injection.py .github/workflows
::error file=.github/workflows/remote-ccp-tbs-morning.yml::NEW untrusted expression in a run: body -- github.event.inputs.force. GitHub pastes this into the script text before bash runs it. Pass it through a step-level env: and use the variable instead.
28 location(s), 56 occurrence(s); 1 new, 0 grown, 0 fixed
##TBS##{"data": {"fixed": 0, "grown": [], "locations": 28, "new": ["remote-ccp-tbs-morning.yml::github.event.inputs.force"], "occurrences": 56}, "probe": "run_injection", "status": "fail", "v": 1}
```
*Result:* Exit code `1` (`fail`).

### Post-Patch Validation
When the patch is applied:
```bash
$ python3 Tools/check_run_injection.py .github/workflows
27 location(s), 55 occurrence(s); 0 new, 0 grown, 0 fixed
##TBS##{"data": {"fixed": 0, "grown": [], "locations": 27, "new": [], "occurrences": 55}, "probe": "run_injection", "status": "pass", "v": 1}
```
*Result:* Exit code `0` (`pass`). `new: []`, 0 new, 0 grown, 0 fixed.

### Context Safety Verification
```bash
$ python3 Tools/check_job_env_contexts.py .github/workflows
85 workflow file(s) scanned, 0 violation(s), discriminates=True
##TBS##{"data": {"discriminates": true, "files": 85, "violations": []}, "probe": "job_env_contexts", "status": "pass", "v": 1}
```
*Result:* Exit code `0` (`pass`). The context is used at the step level (`env:`), which is legal per GitHub Actions execution semantics.

### Patch Application Check
```bash
$ git apply --check Reports/agy/acc2/remote-ccp-tbs-morning.patch
$ git apply --check patches/remote-ccp-tbs-morning.patch
```
*Result:* Both commands exit with status `0` (clean application against current `main` branch).

---

## 7. Fleet Governance & Policy Compliance

- **No Direct Workflow Edits:** Fleet rules strictly prohibit automated worker accounts from directly modifying files under `.github/workflows/`.
- **Working Tree State:** The repository's `.github/workflows/` directory remains unmodified on branch `main`.
- **Deliverables:** The complete diagnostic report and verified unified diff patch files are committed under `Reports/agy/acc2/` and `patches/`.
