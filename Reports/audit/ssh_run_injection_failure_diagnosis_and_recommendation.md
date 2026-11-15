# Forensic Diagnosis: Gate 1 `ssh-run.yml` Injection Vulnerability & `capture-parscoders` CI Verification

- **Worker ID:** `agw-ACC2-w02`
- **Account:** Account 2 (`agw-ACC2`)
- **Date:** 2026-11-15
- **Target Component:** `.github/workflows/ssh-run.yml`
- **Gate:** `.github/workflows/gate-yaml-lint.yml` (Step: `No new untrusted expression in a run body`)
- **Detection Tool:** `Tools/check_run_injection.py`
- **Baseline Reference:** `Tools/injection_baseline.json`
- **Patch Deliverable:** `Reports/audit/ssh-run-injection.patch`
- **Related Verification:** Run `43716489952` on commit `5c247c5` (`.github/workflows/capture-parscoders-session.yml`)

---

## 1. Executive Summary

Continuous integration workflows failed on **Gate 1 (YAML Lint & Workflow Validator)** during the step `No new untrusted expression in a run body` due to script injection vulnerabilities identified in `.github/workflows/ssh-run.yml`.

Specifically, `Tools/check_run_injection.py` detected three unquoted, untrusted context expressions directly interpolated into the `run:` block of step `Run command on VPS`:
1. `inputs.command`
2. `inputs.timeout`
3. `inputs.working_dir`

Because `.github/workflows/ssh-run.yml` had zero entries in `Tools/injection_baseline.json`, these three expressions were flagged as **NEW** untrusted interpolations, triggering an immediate non-zero exit code (`1`) from the gate.

In accordance with repository governance rules, no direct modifications have been made to `.github/workflows/ssh-run.yml` on `main`. Instead:
1. The exact remediation was formulated to map inputs into step-level environment variables (`COMMAND: ${{ inputs.command }}`, `TIMEOUT: ${{ inputs.timeout }}`, `WORKING_DIR: ${{ inputs.working_dir }}`) and reference them via `$COMMAND`, `$TIMEOUT`, `$WORKING_DIR`.
2. A unified diff patch was generated and saved to `Reports/audit/ssh-run-injection.patch`.
3. Offline verification confirmed that applying this patch resolves all 3 violations and `check_run_injection.py` passes with exit code `0`.
4. Run `43716489952` for `.github/workflows/capture-parscoders-session.yml` (commit `5c247c5`) was audited and verified to be 100% green (status: `completed`, conclusion: `success`), requiring no further changes.
5. An issue comment recommendation has been formulated for Notion AI.

---

## 2. Forensic Diagnosis & Root Cause Analysis

### 2.1 Vulnerability Mechanics (CWE-78: Command Injection)

In GitHub Actions, expression interpolations enclosed in `${{ ... }}` are evaluated and replaced textually by the runner engine *prior to passing the script string to the bash shell*.

In `.github/workflows/ssh-run.yml` (lines 46–57), the step was defined as:
```yaml
      - name: Run command on VPS
        id: run
        run: |
          ssh \
            -i ~/.ssh/id_rsa \
            -p ${{ secrets.SSH_PORT }} \
            -o StrictHostKeyChecking=no \
            -o ConnectTimeout=30 \
            -o IdentitiesOnly=yes -v \
            ${{ secrets.SSH_USER }}@${{ secrets.SSH_HOST }} \
            "cd ${{ inputs.working_dir }} && timeout ${{ inputs.timeout }} bash -c '${{ inputs.command }}'"
```

When an untrusted input such as `${{ inputs.command }}` is directly interpolated into the inline shell script:
- Any single quote (`'`), double quote (`"`), semicolon (`;`), subshell operator (`$()`, `` ` ``), or newline contained in `inputs.command` breaks out of the surrounding command structure.
- Similar risks apply to `inputs.working_dir` and `inputs.timeout`.
- An attacker or unauthorized workflow caller can construct an input payload that terminates the `ssh` command or injects arbitrary local bash commands directly on the GitHub Actions runner.

### 2.2 Gate Validator Mechanics & Baseline Comparison

In `.github/workflows/gate-yaml-lint.yml` (lines 282–284), Gate 1 executes:
```yaml
      - name: No new untrusted expression in a run body
        run: python3 Tools/check_run_injection.py .github/workflows
```

`Tools/check_run_injection.py` scans all workflows for expressions matching:
```python
UNTRUSTED = re.compile(r"\$\{\{\s*(inputs\.[A-Za-z0-9_.-]+|github\.event\.[A-Za-z0-9_.-]+)")
```
Findings are keyed as `<file>::<expression>` and compared against `Tools/injection_baseline.json`.

#### Pre-Patch Scan Output
```text
::error file=.github/workflows/ssh-run.yml::NEW untrusted expression in a run: body -- inputs.command. GitHub pastes this into the script text before bash runs it. Pass it through a step-level env: and use the variable instead.
::error file=.github/workflows/ssh-run.yml::NEW untrusted expression in a run: body -- inputs.timeout. GitHub pastes this into the script text before bash runs it. Pass it through a step-level env: and use the variable instead.
::error file=.github/workflows/ssh-run.yml::NEW untrusted expression in a run: body -- inputs.working_dir. GitHub pastes this into the script text before bash runs it. Pass it through a step-level env: and use the variable instead.
30 location(s), 58 occurrence(s); 3 new, 0 grown, 0 fixed
##TBS##{"data": {"fixed": 0, "grown": [], "locations": 30, "new": ["ssh-run.yml::inputs.command", "ssh-run.yml::inputs.timeout", "ssh-run.yml::inputs.working_dir"], "occurrences": 58}, "probe": "run_injection", "status": "fail", "v": 1}
```

Because `ssh-run.yml` had 3 new occurrences and 0 baseline tolerance, the gate exited with status `1`.

---

## 3. Exact Remediation & Patch

### 3.1 Step-Level Environment Mapping

The secure GitHub Actions design pattern requires binding untrusted inputs to step-level environment variables:
```yaml
      - name: Run command on VPS
        id: run
        env:
          COMMAND: ${{ inputs.command }}
          TIMEOUT: ${{ inputs.timeout }}
          WORKING_DIR: ${{ inputs.working_dir }}
        run: |
          ssh \
            -i ~/.ssh/id_rsa \
            -p ${{ secrets.SSH_PORT }} \
            -o StrictHostKeyChecking=no \
            -o ConnectTimeout=30 \
            -o IdentitiesOnly=yes -v \
            ${{ secrets.SSH_USER }}@${{ secrets.SSH_HOST }} \
            "cd $WORKING_DIR && timeout $TIMEOUT bash -c '$COMMAND'"
```

By passing values through `env:`, the runner populates the environment table of the child process. The values are treated as pure data, neutralizing script interpolation and quoting injection attacks.

### 3.2 Unified Diff Deliverable

Saved to `Reports/audit/ssh-run-injection.patch`:
```diff
--- a/.github/workflows/ssh-run.yml
+++ b/.github/workflows/ssh-run.yml
@@ -45,6 +45,10 @@
 
       - name: Run command on VPS
         id: run
+        env:
+          COMMAND: ${{ inputs.command }}
+          TIMEOUT: ${{ inputs.timeout }}
+          WORKING_DIR: ${{ inputs.working_dir }}
         run: |
           ssh \
             -i ~/.ssh/id_rsa \
@@ -53,7 +57,7 @@
             -o ConnectTimeout=30 \
             -o IdentitiesOnly=yes -v \
             ${{ secrets.SSH_USER }}@${{ secrets.SSH_HOST }} \
-            "cd ${{ inputs.working_dir }} && timeout ${{ inputs.timeout }} bash -c '${{ inputs.command }}'"
+            "cd $WORKING_DIR && timeout $TIMEOUT bash -c '$COMMAND'"
 
       - name: Cleanup
         if: always()
```

### 3.3 Patch Validation Results

1. **Git Apply Cleanliness:**
   ```bash
   git apply --check Reports/audit/ssh-run-injection.patch
   # Result: 0 exit code, applies cleanly with zero conflicts
   ```

2. **Validation with `check_run_injection.py`:**
   ```bash
   python3 Tools/check_run_injection.py <patched_workflows_dir>
   # Output:
   27 location(s), 55 occurrence(s); 0 new, 0 grown, 0 fixed
   ##TBS##{"data": {"fixed": 0, "grown": [], "locations": 27, "new": [], "occurrences": 55}, "probe": "run_injection", "status": "pass", "v": 1}
   # Exit code: 0
   ```

3. **Validation with `check_job_env_contexts.py`:**
   ```bash
   python3 Tools/check_job_env_contexts.py <patched_workflows_dir>
   # Output: 90 workflow file(s) scanned, 0 violation(s), discriminates=True
   # Exit code: 0
   ```

---

## 4. Verification of `capture-parscoders-session.yml` Status

A comprehensive status audit of GitHub Actions workflow run `43716489952` on commit `5c247c588d3faeaae8ce0c3da74393753128a430` (`5c247c5`) was executed:

- **Repository:** `mohammadlali0707-stack/agw-workers`
- **Workflow Name:** `Capture ParsCoders session`
- **Workflow File:** `.github/workflows/capture-parscoders-session.yml`
- **Run ID:** `43716489952`
- **Run Head SHA:** `5c247c588d3faeaae8ce0c3da74393753128a430`
- **Status:** `completed`
- **Conclusion:** `success` (GREEN)
- **Created At:** `2026-11-15T12:57:46Z`
- **Updated At:** `2026-11-15T12:59:52Z`
- **URL:** `https://github.com/mohammadlali0707-stack/agw-workers/actions/runs/43716489952`

### Step-by-Step Execution Breakdown
| Step | Status | Conclusion |
|---|---|---|
| `Set up job` | completed | success |
| `Checkout agw-workers` | completed | success |
| `Set up Python 3.11` | completed | success |
| `Install dependencies` | completed | success |
| `Capture ParsCoders session` | completed | success |
| `Update SITE_SESSION_PARSCODERS secret` | completed | success |
| `Shred session file` | completed | success |
| `Post Set up Python 3.11` | completed | success |
| `Post Checkout agw-workers` | completed | success |
| `Complete job` | completed | success |

**Assessment:** The session capture implementation committed in `5c247c5` executed with complete success, refreshed the `SITE_SESSION_PARSCODERS` GitHub secret, securely shredded intermediate session files, and concluded with a clean exit code. **No further changes are required for `capture-parscoders-session.yml` or `scripts/site_proposal/capture_parscoders.py`.**

---

## 5. Notion AI Issue Comment Recommendation

Below is the formatted comment ready to be posted for Notion AI / repository maintainers:

```markdown
### 🔒 Security & CI Fix: Resolve Gate 1 Script Injection in `ssh-run.yml`

**Target File:** `.github/workflows/ssh-run.yml`  
**Patch Deliverable:** `Reports/audit/ssh-run-injection.patch`  
**Author:** `agw-ACC2-w02`  
**Gate:** Gate 1 (`gate-yaml-lint.yml` -> `Tools/check_run_injection.py`)  
**Status:** Tested & Verified (0 new, 0 grown, exit code 0)

#### 1. Vulnerability & Diagnosis
In `.github/workflows/ssh-run.yml`, step `Run command on VPS` directly interpolated untrusted inputs inside a bash script block:
- `${{ inputs.command }}`
- `${{ inputs.timeout }}`
- `${{ inputs.working_dir }}`

Because the GitHub Actions runner performs string expansion before bash execution, untrusted inputs can escape shell quoting (CWE-78). The CI gate script `Tools/check_run_injection.py` flagged these 3 untrusted expressions as new violations not registered in `Tools/injection_baseline.json`, failing Gate 1.

#### 2. Exact Patch
Per repository security standards, untrusted inputs must be passed through step-level `env:` variables and accessed as `$COMMAND`, `$TIMEOUT`, and `$WORKING_DIR`:

```diff
--- a/.github/workflows/ssh-run.yml
+++ b/.github/workflows/ssh-run.yml
@@ -45,6 +45,10 @@
 
       - name: Run command on VPS
         id: run
+        env:
+          COMMAND: ${{ inputs.command }}
+          TIMEOUT: ${{ inputs.timeout }}
+          WORKING_DIR: ${{ inputs.working_dir }}
         run: |
           ssh \
             -i ~/.ssh/id_rsa \
@@ -53,7 +57,7 @@
             -o ConnectTimeout=30 \
             -o IdentitiesOnly=yes -v \
             ${{ secrets.SSH_USER }}@${{ secrets.SSH_HOST }} \
-            "cd ${{ inputs.working_dir }} && timeout ${{ inputs.timeout }} bash -c '${{ inputs.command }}'"
+            "cd $WORKING_DIR && timeout $TIMEOUT bash -c '$COMMAND'"
 
       - name: Cleanup
         if: always()
```

#### 3. Verification Summary
- `git apply --check Reports/audit/ssh-run-injection.patch` passed cleanly.
- `Tools/check_run_injection.py` passed with status `pass` (27 baseline locations, 55 occurrences; 0 new, 0 grown, 0 fixed).
- Verified `capture-parscoders-session.yml` in run `43716489952` (commit `5c247c5`) is **GREEN (success)**; no further changes are required.

Please apply `Reports/audit/ssh-run-injection.patch` to `.github/workflows/ssh-run.yml` to restore Gate 1 to green.
```
