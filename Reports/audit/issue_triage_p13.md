# LeadDesk AI — P13 Subtask 3 Open Issues Audit & Triage

**Repository:** `mohammadlali0707-stack/agw-workers`  
**Date:** 2026-10-28  
**Auditor:** Argon Worker ACC8  
**Scope:** Triage of all open issues, resolving stuck-watch (#106, #113), and closing 9 obsolete issues with evidence.

---

## 1. Summary of All Open Issues

| Issue # | Title | Category | Action | Evidence / Reason |
|---------|-------|----------|--------|-------------------|
| **#101** | `[CI] failures from e-mail — 2026-10-23` | Historic CI Report | **CLOSE (Obsolete)** | Transient daily CI failure report from 2026-10-23. Underlying `agy-swarm-infinite.yml` binary installation fixed in commit `6583618`. |
| **#102** | `[CI] failures from e-mail — 2026-10-24` | Historic CI Report | **CLOSE (Obsolete)** | Transient daily CI failure report from 2026-10-24. Runner failures superseded by fleet-wide swarm patches. |
| **#103** | `[CI] failures — 2026-10-24` | Historic CI Report | **CLOSE (Obsolete)** | Historic API CI report from 2026-10-24. Diagnosed in `ba4d005`, resolved in commit `6583618` (PR #117). |
| **#104** | `[CI] failures — 2026-10-25` | Historic CI Report | **CLOSE (Obsolete)** | Transient daily CI failure report from 2026-10-25. Superseded by subsequent runner health fixes. |
| **#105** | `[CI] failures — 2026-10-26` | Active Subtask 2 Scope | **Active (Subtask 2)** | Handled under Item 2 CI Subtask (PR #117 / commit `6583618`). |
| **#106** | `[stuck-watch] Fix Gate 1 -- YAML Lint & Workflow Validator` | Stuck-Watch Scope | **RESOLVED (Subtask 3)** | Fixed via `workers/watcher.py` timeout and process group recovery; Gate 1 env contexts verified. |
| **#107** | `[CI] failures — 2026-10-27` | Active Subtask 2 Scope | **Active (Subtask 2)** | Handled under Item 2 CI Subtask (PR #117 / commit `6583618`). |
| **#108** | `[bilingual 1/4] Translator library tools/bilingual/translate.py + tests` | Bilingual Feature | **CLOSE (Merged)** | Implemented and merged in commit `d04f3cb4cb79f22575775390091ff6cf69e99292` (PR #108). |
| **#109** | `[bilingual 2/4] Notion page sync tools/bilingual/notion_fa.py + tests` | Bilingual Feature | **CLOSE (Merged)** | Implemented and merged in commit `a24b2184d0fe2c47463f8ea92d295fe1b0ea6ec8` (PR #111). |
| **#110** | `[bilingual 3/4] Workflow notion-bilingual.yml + page list` | Bilingual Feature | **CLOSE (Merged)** | Implemented and merged in commit `22ba5ec95eb9ea58766a267ef5ef64f7cf4fcbf4` (Part 3/4). |
| **#111** | `[bilingual 4/4] Databases: Persian title + Persian toggle` | Bilingual Feature | **CLOSE (Merged)** | Implemented and merged in commit `a24b2184d0fe2c47463f8ea92d295fe1b0ea6ec8` (PR #111). |
| **#112** | `[bilingual 5/5] Notion: Persian main pages + separate English mirror pages` | Bilingual Feature | **CLOSE (Merged/Superseded)** | Delivered in commit `a24b2184d0fe2c47463f8ea92d295fe1b0ea6ec8`; hung execution addressed by Subtask 3 watcher timeouts. |
| **#113** | `[stuck-watch] Fix Notion Persian + English mirror (#112)` | Stuck-Watch Scope | **RESOLVED (Subtask 3)** | Process hang (>3h) prevented by `workers/watcher.py` timeout enforcement, heartbeat monitor, and safe pipe cleanup. |
| **#114** | `[CI] failures — 2026-10-28` | Active Subtask 2 Scope | **Active (Subtask 2)** | Handled under Item 2 CI Subtask (PR #117 / commit `6583618`). |
| **#123** | `[CI] failures from e-mail — 2026-10-28` | Active CI Report | **LEAVE OPEN (Active)** | Current daily CI failures from today under ongoing investigation by the fleet team. |

---

## 2. Detailed Audit Evidence for the 9 Obsolete Issues

### Issue #101: `[CI] failures from e-mail — 2026-10-23`
- **URL:** https://github.com/mohammadlali0707-stack/agw-workers/issues/101
- **Status:** Obsolete
- **Audit Findings:** Daily e-mail failure rollup capturing runs from October 23, 2026 (specifically `agy-swarm-infinite.yml` runs 39916835175 etc.). The root failure was AGY CLI HuggingFace dataset authentication and tarball extraction. This was patched in subsequent swarm workflow revisions and definitively verified in Subtask 2 commit `6583618fc16df3b8aaac6b1e4a76112c843c2adb`. No active code or infrastructure changes are pending for this historic log.
- **Closure Recommendation:** Close with comment referencing resolution in commit `6583618`.

### Issue #102: `[CI] failures from e-mail — 2026-10-24`
- **URL:** https://github.com/mohammadlali0707-stack/agw-workers/issues/102
- **Status:** Obsolete
- **Audit Findings:** Captures a single transient run failure (run 40016123275) on `agy-swarm-infinite.yml` from October 24. Same root cause as #101; fully addressed by fleet runner updates.
- **Closure Recommendation:** Close with comment citing obsolescence.

### Issue #103: `[CI] failures — 2026-10-24`
- **URL:** https://github.com/mohammadlali0707-stack/agw-workers/issues/103
- **Status:** Obsolete
- **Audit Findings:** Automated API collection of failed runs on 2026-10-24 (run 40094225788 etc.). Diagnostic audit was committed in `ba4d005ac0351837abd154ea6be98606d3b0a920` (`docs(audit): link posted issue #103 diagnostic comment`), and final fix deployed in commit `6583618`.
- **Closure Recommendation:** Close with comment citing commit `ba4d005` and commit `6583618`.

### Issue #104: `[CI] failures — 2026-10-25`
- **URL:** https://github.com/mohammadlali0707-stack/agw-workers/issues/104
- **Status:** Obsolete
- **Audit Findings:** Captures single transient failure (run 40128975413) on 2026-10-25. Superseded by runner health repairs.
- **Closure Recommendation:** Close with comment citing obsolescence.

### Issue #108: `[bilingual 1/4] Translator library tools/bilingual/translate.py + tests`
- **URL:** https://github.com/mohammadlali0707-stack/agw-workers/issues/108
- **Status:** Already Merged / Resolved
- **Audit Findings:** Deliverables (`tools/bilingual/translate.py` and `tests/test_bilingual_translate.py`) were committed and merged to main in commit `d04f3cb4cb79f22575775390091ff6cf69e99292` with title `[bilingual 1/4] Add translator library tools/bilingual/translate.py and tests (#108)`. All 13 unit tests pass.
- **Closure Recommendation:** Close as completed via PR #108 / commit `d04f3cb`.

### Issue #109: `[bilingual 2/4] Notion page sync tools/bilingual/notion_fa.py + tests`
- **URL:** https://github.com/mohammadlali0707-stack/agw-workers/issues/109
- **Status:** Already Merged / Resolved
- **Audit Findings:** `tools/bilingual/notion_fa.py` and `tests/test_bilingual_shared.py` were integrated and merged into main in commit `a24b2184d0fe2c47463f8ea92d295fe1b0ea6ec8` (`feat(bilingual): implement database sync for Knowledge Notebook, Decision Log, Task Board (#111)`). The interface `sync_page(page_id, dry_run=False)` is present and active.
- **Closure Recommendation:** Close as completed via commit `a24b218`.

### Issue #110: `[bilingual 3/4] Workflow notion-bilingual.yml + page list`
- **URL:** https://github.com/mohammadlali0707-stack/agw-workers/issues/110
- **Status:** Already Merged / Resolved
- **Audit Findings:** `.github/workflows/notion-bilingual.yml` and `config/bilingual_pages.json` were merged into main in commit `22ba5ec95eb9ea58766a267ef5ef64f7cf4fcbf4` (`feat(bilingual): add notion-bilingual workflow and page list (Part 3/4)`).
- **Closure Recommendation:** Close as completed via commit `22ba5ec`.

### Issue #111: `[bilingual 4/4] Databases: Persian title + Persian toggle for Knowledge Notebook, Decision Log, Task Board`
- **URL:** https://github.com/mohammadlali0707-stack/agw-workers/issues/111
- **Status:** Already Merged / Resolved
- **Audit Findings:** `tools/bilingual/notion_db.py` and unit tests in `tests/test_notion_db.py` were committed and merged to main in commit `a24b2184d0fe2c47463f8ea92d295fe1b0ea6ec8` (`feat(bilingual): implement database sync for Knowledge Notebook, Decision Log, Task Board (#111)`).
- **Closure Recommendation:** Close as completed via PR #111 / commit `a24b218`.

### Issue #112: `[bilingual 5/5] Notion: Persian main pages + separate English mirror pages`
- **URL:** https://github.com/mohammadlali0707-stack/agw-workers/issues/112
- **Status:** Already Merged / Superseded
- **Audit Findings:** Implementation merged in commit `a24b2184d0fe2c47463f8ea92d295fe1b0ea6ec8`. The subsequent execution hang reported in #113 is resolved by Subtask 3's `workers/watcher.py` timeout and process group monitor.
- **Closure Recommendation:** Close as completed / superseded by commit `a24b218` and Subtask 3.

---

## 3. Active Issue Retained
- **#123:** `[CI] failures from e-mail — 2026-10-28`  
  - **Reason for leaving open:** Opened today (2026-10-28T16:09:22Z), reflecting current daily CI failures that are actively monitored and worked on across the fleet. Not obsolete.
