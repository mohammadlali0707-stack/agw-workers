### 🛠️ Proposal: Event Coverage Alignment for agy-plan-dispatch.yml Gate Condition

**Target Workflow:** `.github/workflows/agy-plan-dispatch.yml`  
**Patch Deliverable:** `Reports/audit/agy_plan_dispatch_trigger_coverage.patch`  
**Author:** agw-ACC2-w03 (Mohammad's GitHub Worker Fleet -- Account 2)  
**Target Step:** `check-marker` job condition (lines 35–38)  
**Correlated Audit Probe:** `Tools/probe_trigger_event_coverage.py`  
**Status:** Proposal Only (0 Edits to Main Workflows)

---

#### 1. Forensic Summary & Probe Finding

When executing diagnostic probe `Tools/probe_trigger_event_coverage.py`, the following coverage finding was identified:
```text
FAIL: 1 finding(s):
  - agy-plan-dispatch.yml: `on:` subscribes to 'issue_comment' but check-marker's `if:` never admits it -- the workflow fires and then refuses itself, green and silent
```

##### 1.1 Root Cause Isolation
The workflow trigger section subscribes to two events:
```yaml
on:
  issue_comment:
    types: [created]
  workflow_dispatch:
    inputs: ...
```

However, the entry job `check-marker` uses the following condition:
```yaml
jobs:
  check-marker:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    if: >
      github.event_name == 'workflow_dispatch' ||
      contains(github.event.comment.body, '@agy-plan-approved') ||
      contains(github.event.comment.body, '@sonnet-plan-approved')
```

Per the architectural rule enforced by `probe_trigger_event_coverage.py`, if a workflow's entry gate inspects `github.event_name`, it must explicitly admit every event listed under `on:`. Because `github.event_name == 'issue_comment'` is omitted before evaluating `github.event.comment.body`, the gate creates an asymmetric event filter where `workflow_dispatch` is checked by event name while `issue_comment` is implicitly inferred solely via comment payload.

##### 1.2 Proposed Remediation
Explicitly group the `issue_comment` event evaluation:
```yaml
    if: >
      github.event_name == 'workflow_dispatch' ||
      (github.event_name == 'issue_comment' && (
        contains(github.event.comment.body, '@agy-plan-approved') ||
        contains(github.event.comment.body, '@sonnet-plan-approved')
      ))
```

#### 2. Verification
1. **Clean Patch Application:** `git apply --check Reports/audit/agy_plan_dispatch_trigger_coverage.patch` exits with status code 0.
2. **Probe Compliance:** Validated in memory against `Tools/probe_trigger_event_coverage.py` logic; resolves the coverage failure cleanly.
3. **Zero Main Direct Edits:** Retained strictly offline in `Reports/audit/` per fleet change-management protocol.
