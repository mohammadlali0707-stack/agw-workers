### 🛡️ Proposal: Webhook Health Gate Fallback & Resilience Patch (Issue #161)

**Target Workflow:** `.github/workflows/gate-webhook-health.yml`  
**Patch Deliverable:** `Reports/audit/gate-webhook-health-resilience.patch`  
**Author:** agw-ACC2-w03 (Account 2)  
**Tracking Issue:** #161 (`mohammadlali0707-stack/agw-workers`)

#### Diagnostic Analysis & Resilience Gaps (Control Commit `d4186eb`):
- **Control Patch Evaluation:** Control patch `d4186eb` resolved upstream n8n 404 categorization, python-based body formatting, alert deduplication, and initial label fallback.
- **Remaining Resilience Gaps across Fork Runners:**
  1. *Missing Step Failure Isolation:* The `Post alert if webhook health fails` step lacked `continue-on-error: true`. Any GitHub API, network, or token barrier fails the step, turning an alert notification failure into a full CI job crash.
  2. *Fork Runner Read-Only Token Permissions:* On fork repositories, GitHub Actions default security settings routinely restrict `GITHUB_TOKEN` to read-only (`contents: read`), ignoring workflow-level write requests.
  3. *Unhandled Fallback Error:* When issue creation is blocked on fork runners, the secondary unlabelled `gh issue create` exits non-zero under `set -e`, aborting the runner step. Adding graceful error logging (`|| echo "::warning::..."`) ensures the runner completes cleanly.
  4. *CWE-78 Injection Immunity:* Confirmed zero untrusted expression interpolations; all parameters are safely passed via environment variables and `--body-file`.

#### Proposed Unified Diff:
```diff
--- a/.github/workflows/gate-webhook-health.yml
+++ b/.github/workflows/gate-webhook-health.yml
@@ -10,8 +10,8 @@ jobs:
   webhook-health:
     runs-on: ubuntu-latest
     permissions:
-      issues: write
       contents: read
+      issues: write
     env:
       # Reading repos/{repo}/hooks needs ADMIN on the repo. GITHUB_TOKEN has no
       # webhook permission at all -- there is no `permissions:` key that grants
@@ -165,6 +165,7 @@ jobs:
 
       - name: Post alert if webhook health fails
         if: failure()
+        continue-on-error: true
         env:
           GH_TOKEN: ${{ secrets.ACC6_PAT || secrets.GITHUB_TOKEN }}
           EXPECTED_URL: ${{ env.EXPECTED_URL }}
@@ -263,7 +264,8 @@ jobs:
               || gh issue create \
                 --repo "$REPO" \
                 --title "$ISSUE_TITLE" \
-                --body-file /tmp/issue_body.md
+                --body-file /tmp/issue_body.md 2>/dev/null \
+              || echo "::warning::failed to create alert issue on $REPO (issues disabled or insufficient token permissions)"
           else
             echo "Existing open issue found (#$EXISTING_ISSUE). Updating with a comment instead of creating duplicate..."
             gh issue comment "$EXISTING_ISSUE" \
```

#### Verification Evidence:
- Clean `git apply --check Reports/audit/gate-webhook-health-resilience.patch` (exit code 0 against main).
- Zero CWE-78 injection vulnerabilities detected (`Tools/check_run_injection.py`).
- Validated syntax with `Tools/probe_gate_yaml_lint_check2.py` (0 findings).
- Patch length: 30 lines, +4/-2 lines (strictly within <=400 lines limit).
