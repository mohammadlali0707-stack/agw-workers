# Forensic Audit: Gate 1 (`gate-yaml-lint.yml`) Workflow Safety & Input Injection Remediation

- **Worker ID:** `agw-ACC2-w03`
- **Account:** Account 2 (`agw-ACC2`)
- **Date:** 2026-11-22
- **Target Workflows:** `.github/workflows/gate-yaml-lint.yml`, `.github/workflows/site-chat-read.yml`
- **Audited Commit:** `38f9cf6d5371077117cc45d7601c3b6b4dd8ad22` (`38f9cf6`)
- **Gates Verified:** Gate 1 (YAML Lint & Workflow Validator), `check_run_injection.py`, `check_job_env_contexts.py`
- **Detection & Verification Tools:**
  - `python3 Tools/check_run_injection.py .github/workflows`
  - `python3 Tools/check_job_env_contexts.py .github/workflows`
  - `python3 Tools/probe_gate_yaml_lint_check2.py`
- **Status:** Remediation Confirmed; 0 Untrusted Expressions; 0 Residual Defects

---

## 1. Executive Summary

A forensic validation was conducted on the safety mechanisms of `.github/workflows/gate-yaml-lint.yml` and the input injection remediation applied in commit `38f9cf6`. 

Prior to commit `38f9cf6`, `.github/workflows/site-chat-read.yml` introduced direct textual interpolation of five workflow inputs (`inputs.site`, `inputs.project_url`, `inputs.project_title`, `inputs.job_id`, `inputs.contact`) inside a bash `run:` execution block. Because `site-chat-read.yml` was not part of `Tools/injection_baseline.json`, Gate 1 failed with non-zero exit code 1 (`NEW untrusted expression in a run: body`).

Commit `38f9cf6` eliminated inline interpolations by routing workflow inputs through job-level environment variables (`jobs.site-chat-read.env`) and substituting parameter expressions in the script with shell environment references (`"$SITE_INPUT"`, `"$PROJECT_URL_INPUT"`, etc.).

Local safety verifications across all 93 repository workflow files confirmed that:
1. `Tools/check_run_injection.py` passes with exit code 0 (`0 new, 0 grown, 0 fixed`).
2. `Tools/check_job_env_contexts.py` passes with exit code 0 (`0 violations, discriminates=True`).
3. Gate 1 YAML syntax checks, empty job validations, heredoc backtick inspections, and indentation checks pass 100% across the repository.
4. No residual untrusted expressions or illegal context evaluations exist on `main`.

---

## 2. Pre-Remediation CI Failure Analysis (Commit `38f9cf6` Root Cause)

### 2.1 Vulnerability Mechanism (CWE-78: Command Injection)
In GitHub Actions workflows, expressions inside `${{ ... }}` are evaluated and replaced textually by the runner prior to shell invocation. In `.github/workflows/site-chat-read.yml` (introduced in commit `a15ef73` and augmented in `f063c1d`), step `Run chat reader (read-only)` declared:

```bash
python3 -m scripts.site_proposal.read_chat \
  --site "${{ inputs.site }}" \
  --project-url "${{ inputs.project_url }}" \
  --project-title "${{ inputs.project_title }}" \
  --job-id "${{ inputs.job_id }}" \
  --contact "${{ inputs.contact }}" \
  --results-dir chat-results
```

When user inputs contain double quotes, backticks, subshells (`$()`), or semicolons, the expanded bash script breaks out of argument boundaries, allowing arbitrary command execution within the runner container.

### 2.2 Gate 1 Detection Failure
Gate 1 (`.github/workflows/gate-yaml-lint.yml`) runs `Tools/check_run_injection.py .github/workflows`. Simulating the pre-commit state against `38f9cf6~1` reproduced the exact CI failure:
- **Violations Flagged:**
  - `site-chat-read.yml::inputs.contact` (1)
  - `site-chat-read.yml::inputs.job_id` (1)
  - `site-chat-read.yml::inputs.project_title` (1)
  - `site-chat-read.yml::inputs.project_url` (1)
  - `site-chat-read.yml::inputs.site` (1)
- **Gate Output:** `32 location(s), 60 occurrence(s); 5 new, 0 grown, 0 fixed`
- **Result:** Exit code 1; build failure.

---

## 3. Remediation Architecture in Commit `38f9cf6`

Commit `38f9cf6` (`fix: inputs via job-level env (gate untrusted expressions)`) restructured `.github/workflows/site-chat-read.yml` in accordance with repository security policy:

1. **Job-Level Environment Mapping:**
   Inputs are bound to environment variables at the job scope (`jobs.site-chat-read.env`):
   ```yaml
   env:
     SITE_INPUT: ${{ inputs.site }}
     PROJECT_URL_INPUT: ${{ inputs.project_url }}
     PROJECT_TITLE_INPUT: ${{ inputs.project_title }}
     JOB_ID_INPUT: ${{ inputs.job_id }}
     CONTACT_INPUT: ${{ inputs.contact }}
   ```
   Because `inputs.*` is explicitly permitted at the job scope (evaluated when configuring the job container), values are passed as process environment strings rather than bash script source code.

2. **Parameterization in `run:` Block:**
   The CLI invocation was updated to reference double-quoted shell variables:
   ```bash
   python3 -m scripts.site_proposal.read_chat \
     --site "$SITE_INPUT" \
     --project-url "$PROJECT_URL_INPUT" \
     --project-title "$PROJECT_TITLE_INPUT" \
     --job-id "$JOB_ID_INPUT" \
     --contact "$CONTACT_INPUT" \
     --results-dir chat-results
   ```

3. **Artifact Reference Modernization:**
   Step `Upload result artifact` switched reference from inline input to `chat-${{ env.JOB_ID_INPUT }}`, keeping naming aligned with job environment state.

---

## 4. Local Safety Verification & Test Execution Results

All verification suites were executed locally from repository root:

### 4.1 Run Injection Detection (`Tools/check_run_injection.py`)
```bash
python3 Tools/check_run_injection.py .github/workflows
```
- **Output:**
  ```text
  27 location(s), 55 occurrence(s); 0 new, 0 grown, 0 fixed
  ##TBS##{"data": {"fixed": 0, "grown": [], "locations": 27, "new": [], "occurrences": 55}, "probe": "run_injection", "status": "pass", "v": 1}
  ```
- **Conclusion:** PASS (exit code 0). Zero new or grown untrusted expressions across all workflow files.

### 4.2 Job-Level Context Availability (`Tools/check_job_env_contexts.py`)
```bash
python3 Tools/check_job_env_contexts.py .github/workflows
```
- **Output:**
  ```text
  93 workflow file(s) scanned, 0 violation(s), discriminates=True
  ##TBS##{"data": {"discriminates": true, "files": 93, "violations": []}, "probe": "job_env_contexts", "status": "pass", "v": 1}
  ```
- **Conclusion:** PASS (exit code 0). Validated that no forbidden contexts (`runner`, `steps`, `job`, `env`) are evaluated before step execution.

### 4.3 Gate 1 Comprehensive Suite Validation
- **CRLF Detection:** Zero CRLF line endings in `.github/workflows/`.
- **YAML Syntax Validation:** 93/93 workflow files parsed successfully via `yaml.safe_load`.
- **Workflow Job Structure:** All jobs in all 93 workflows possess defined, non-empty `steps`.
- **Python -c Indentation Safety:** 0 indentation escape defects (`check_2_python_inline_yaml_indent`).
- **Heredoc Backtick Check:** 0 unescaped backticks in unquoted heredocs (`check_heredoc_backticks`).
- **Check 2 Regression Probe (`Tools/probe_gate_yaml_lint_check2.py`):**
  ```text
  PASS  test-agy-chat-live.yml is clean -- 0 finding(s)
  PASS  no workflow in the repo reports -- {}
  PASS  a genuinely under-indented block IS still caught -- 2 finding(s)
  PASS  a correctly indented block does not report
  PASS  a self-closing one-liner reports nothing -- []
  ##TBS##{"data": {"failed": 0, "names": []}, "probe": "gate_yaml_lint_check2", "status": "pass", "v": 1}
  ```

---

## 5. Residual Workflow Defect Assessment & Notion AI Status

- **Residual Defect Status:** Zero (0) residual workflow safety or syntax defects were identified on `main`.
- **Patch Deliverable Requirement:** Because commit `38f9cf6` completely resolved the untrusted expression failure and all 93 workflow files satisfy Gate 1 and Gate 4 invariants, no additional patch file is required for Notion AI to apply.
- **Repository Modification Constraint:** No files under `.github/workflows/` were modified on this branch or checkout.
- **Diff Footprint:** Total modifications are strictly confined to `Reports/audit/` and remain well under the 400-line repository ceiling.

---

## 6. Review & Compliance Sign-Off

- **Auditor:** `agw-ACC2-w03`
- **Lead Account:** Account 2 (`agw-ACC2`)
- **Review Tool:** `python3 /tmp/hqteam.py review`
- **Result Status:** APPROVED
