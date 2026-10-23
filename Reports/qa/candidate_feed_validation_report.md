# Candidate Feed QA Validation & Live Listener Verification Report

**Task:** Live Listener Regression & Deprioritization Verification, QA Criteria & Feed Smoke Testing  
**Executors:** `agw-ACC5-w03` (Live Listener Regression & Integration) & `agw-ACC5-w02` (QA & Smoke Testing)  
**Parent Plan:** `issue-91-acc5-3d88bb5ac734` (Issue #91: *"کاهش اولویت کانال‌های تلگرامی بدون خروجی و جستجو برای سورس‌های جدید"*)  
**Execution Timestamp:** 2026-10-23T07:25:00Z  
**Verification Tooling:**  
- [`Tools/verify_channel_deprioritization.py`](../../Tools/verify_channel_deprioritization.py)  
- [`Tools/smoke_test_candidate_feeds.py`](../../Tools/smoke_test_candidate_feeds.py)  
- [`Tools/test_feed_smoke_testing.py`](../../Tools/test_feed_smoke_testing.py)  

**Artifacts & Configurations Maintained:**  
- Sources Configuration: [`acquisition/sources.yaml`](../../acquisition/sources.yaml)  
- Lead Listener Engine: [`acquisition/telegram_lead_listener.py`](../../acquisition/telegram_lead_listener.py)  
- QA Criteria & Procedures: [`qa/criteria/qa_criteria_and_procedures.md`](../../qa/criteria/qa_criteria_and_procedures.md)  
- Candidate Channel Schema: [`qa/schemas/candidate_channel_schema.json`](../../qa/schemas/candidate_channel_schema.json)  
- Channel Message Schema: [`qa/schemas/channel_message_schema.json`](../../qa/schemas/channel_message_schema.json)  
- Clean Operational Candidate Feeds: [`qa/candidates/candidate_feeds_sample.json`](../../qa/candidates/candidate_feeds_sample.json)  
- Segregated Negative Test Fixtures: [`qa/candidates/test_fixtures/negative_test_feeds.json`](../../qa/candidates/test_fixtures/negative_test_feeds.json)  
- Keyword Filter Test Fixtures: [`qa/fixtures/keyword_filter_fixtures.py`](../../qa/fixtures/keyword_filter_fixtures.py)  
- Operational Smoke Test Results: [`Reports/qa/smoke_test_results.json`](smoke_test_results.json)  
- Negative Fixture Test Results: [`Reports/qa/negative_test_results.json`](negative_test_results.json)  

---

## 1. Executive Summary & Live Listener Deprioritization Verification

Under Account 5's supervision, `agw-ACC5-w03` executed and completed end-to-end regression testing and deprioritization verification via [`Tools/verify_channel_deprioritization.py`](../../Tools/verify_channel_deprioritization.py).

### Verified Deprioritization & Scheduling Metrics
- **Total Channels Assessed:** 24 channels verified across [`acquisition/sources.yaml`](../../acquisition/sources.yaml) and [`acquisition/telegram_lead_listener.py`](../../acquisition/telegram_lead_listener.py).
- 🟢 **Golden Tier 1 (15m interval / 96 daily fetches):** 6 channels (`@karlancer_projects`, `@ParsCodersCom`, `@job_coder`, `@remote_ir`, `@WordPressProgettoGp`, `@woocommerce_iran`).
- 🟢 **Standard Tier 2 (60m interval / 24 daily fetches):** 3 channels (`@laravelGroup`, `@iran_itjob`, `@Quera`).
- 🟡 **Deprioritized Tier 3 (360m interval / 4 daily fetches):** 8 channels (`@Group_PyDev`, `@js_group`, `@dm_jobs`, `@ir_startups`, `@ai_automation_ir`, `@phpgp`, `@flutter_iranian`, `@uiux_designers_ir`).
- 🟡 **Quarantined / Muted (1440m interval / 1 daily fetch):** 2 channels (`@job_freelancer`, `@freelancer_job`).
- 🔴 **Dormant / Purged (0m interval / 0 daily fetches):** 5 channels (`@freelance_projects_ir`, `@karyab_it`, `@ecommerce_iranian`, `@xseo_web`, `@dev_jobs_iran`).

### Fleet Cycle Reduction & Optimization Targets
- **Baseline Daily Fetches (Pre-Audit @ 15m default):** 2,304 fetches/day.
- **Optimized Daily Fetches (Post-Deprioritization):** 682 fetches/day.
- **Net Daily Fetch Reduction:** **70.4% savings** (1,622 runner cycles saved daily).
- **Dormant Channel Exclusion:** 100% purged from `DEFAULT_TARGET_GROUPS` (0 active listener cycles for dead channels).
- **Keyword Filter Fixtures:** **29/29 independent fixtures passed** (16 Tier 3 employer/chatter fixtures, 5 quarantine fixtures, 3 Tier 1/2 pass-through fixtures, 5 dormant block fixtures).
- **Budget Multiplier Threshold Parsing:** Verified for Persian multipliers (`میلیون` [x1,000,000] and `هزار` [x1,000]) against `QUARANTINE_MIN_BUDGET_TOMAN` (20,000,000 Toman) with zero regressions.

---

## 2. Standardized QA Criteria & Threshold Policies

Candidate feeds evaluated by Account 5 QA undergo rigorous scoring across four core quality dimensions:

| Dimension | Target Metric | Acceptance Threshold | Gating Action |
|---|---|---|---|
| **1. Operational Stability** | HTTP Endpoint Status | 200 OK | Reject if non-200 |
| | Latency (p95) | < 2,500 ms | Downgrade stability score |
| | Extraction Error Rate | 0.0% | Immediate Reject |
| **2. Schema Compatibility** | JSON Schema Draft-07 | 100% pass rate | Immediate Reject |
| | Mandatory Properties | 100% present | Immediate Reject |
| | Character Encoding | Valid UTF-8 NFC | Reject on malformed text |
| **3. Update Frequency Health** | Publication Cadence | >= 0.5 posts/day | CONDITIONAL extended tier |
| | Maximum Staleness | <= 168 hours (7 days) | Immediate Reject |
| | Burst Flooding | <= 20 posts/hour | Immediate Reject |
| **4. Zero-Noise Performance** | Ad / Sponsored Ratio | <= 5.0% | Immediate Reject |
| | Exact Duplicate Rate | 0.0% duplicates | Immediate Reject |
| | Substantive Length | >= 25 characters | Noise suppression |

### Composite Scoring Formula
$$S_{composite} = 0.25 \cdot S_{stability} + 0.30 \cdot S_{schema} + 0.25 \cdot S_{cadence} + 0.20 \cdot S_{noise}$$
- **PASS**: $S_{composite} \ge 85.0$, zero critical failures, cadence $\ge 0.5$ posts/day.
- **CONDITIONAL**: $70.0 \le S_{composite} < 85.0$ (or low cadence warning), admitted to Low-Priority Extended Tier (360m).
- **FAIL**: $S_{composite} < 70.0$ or any critical gate breach.

---

## 3. Schema Validation Outcomes

Schema validation was executed against Draft-07 schemas: [`qa/schemas/candidate_channel_schema.json`](../../qa/schemas/candidate_channel_schema.json) and [`qa/schemas/channel_message_schema.json`](../../qa/schemas/channel_message_schema.json).

### Operational Candidates Schema Validation
- **Candidate Definition Validation:** 4/4 operational candidate channel definitions valid (100%).
- **Message Schema Validation:**
  - `cand-tech-ai-01` (`@tech_ai_persian`): 5/5 messages valid (100%).
  - `cand-gamedev-godot-02` (`@godot_gamecraft_fa`): 4/4 messages valid (100%).
  - `cand-automation-cloud-03` (`@devops_automation_pro`): 3/3 messages valid (100%).
  - `cand-narrative-sound-07` (`@ambient_soundscapes_fa`): 2/2 messages valid (100%).
- **Overall Operational Schema Pass Rate:** **100.0% (14/14 valid messages)**.

### Synthetic Negative Probes Schema Rejection
- `cand-malformed-bot-05` (`@broken_crypto_signals`): Correctly failed 2/2 messages (invalid data types, negative view count, invalid timestamp format, non-200 HTTP 500 endpoint).

---

## 4. Smoke Test Execution Results

Smoke tests were executed via [`Tools/smoke_test_candidate_feeds.py`](../../Tools/smoke_test_candidate_feeds.py) and unit-tested via [`Tools/test_feed_smoke_testing.py`](../../Tools/test_feed_smoke_testing.py).

### 4.1 Clean Operational Candidate Feeds (`candidate_feeds_sample.json`)

| Candidate ID | Telegram Handle | Topic / Domain Category | Stability | Schema Pass | Cadence (Posts/Day) | Ad Ratio | Dupes | Composite Score | Final Status |
|---|---|---|---|---|---|---|---|---|---|
| `cand-tech-ai-01` | `@tech_ai_persian` | `ai_tech` | 100.0 | 100% (5/5) | 1.33 (10.0h ago) | 0.0% | 0 | **99.4** | 🟢 **PASS** (60m poll) |
| `cand-gamedev-godot-02` | `@godot_gamecraft_fa` | `game_development` | 100.0 | 100% (4/4) | 0.87 (8.5h ago) | 0.0% | 0 | **99.5** | 🟢 **PASS** (120m poll) |
| `cand-automation-cloud-03` | `@devops_automation_pro` | `automation_devops` | 100.0 | 100% (3/3) | 0.89 (12.3h ago) | 0.0% | 0 | **99.3** | 🟢 **PASS** (180m poll) |
| `cand-narrative-sound-07` | `@ambient_soundscapes_fa` | `narrative_audio` | 100.0 | 100% (2/2) | 0.15 (39.5h ago) | 0.0% | 0 | **87.1** | 🟡 **CONDITIONAL** (360m poll) |

### 4.2 Segregated Negative Test Fixtures Suite (`test_fixtures/negative_test_feeds.json`)

| Fixture ID | Telegram Handle | Target Test Dimension | Stability | Schema Pass | Cadence (Posts/Day) | Ad / Dupe Ratio | Composite Score | Gating Verdict | Target Failure Mode Caught |
|---|---|---|---|---|---|---|---|---|---|
| `cand-dormant-legacy-04` | `@retro_archive_dormant` | Cadence & Staleness | 100.0 | 100% (1/1) | 0.02 (1145.5h ago) | 0.0% / 0 | 75.0 | 🔴 **FAIL** | Critical Staleness Breach (1145.5h > 168.0h threshold) |
| `cand-malformed-bot-05` | `@broken_crypto_signals` | Operational & Schema | 0.0 (500) | 0% (0/2) | 0.81 (29.5h ago) | 0.0% / 0 | 43.2 | 🔴 **FAIL** | HTTP 500 Endpoint Failure & Message Schema Violations |
| `cand-ad-spam-forwarder-06` | `@vpn_promo_linkfarm` | Zero-Noise & Spam | 100.0 | 100% (5/5) | 5.00 (9.5h ago) | 80.0% / 20.0% | 82.6 | 🔴 **FAIL** | Commercial Ad Ratio (80% > 5%) & Broadcast Duplicate Spam |

---

## 5. Candidate Feed Deep-Dive & Source Profile

1. **`cand-tech-ai-01` (`@tech_ai_persian`):** Score 99.4/100. High-signal Persian deep learning and AI architectural posts. Polling interval: **60m**.
2. **`cand-gamedev-godot-02` (`@godot_gamecraft_fa`):** Score 99.5/100. Technical Godot 4 shader tutorials, GDScript snippets, and 3D pipeline discussions. Polling interval: **120m**.
3. **`cand-automation-cloud-03` (`@devops_automation_pro`):** Score 99.3/100. Production CI/CD pipelines, runner hygiene, and Docker/Podman workflows. Polling interval: **180m**.
4. **`cand-narrative-sound-07` (`@ambient_soundscapes_fa`):** Score 87.1/100. Low publication velocity (0.15 posts/day) triggers conditional routing to the low-priority extended tier. Polling interval: **360m**.

---

## 6. Verification Tooling & Regression Test Execution Summary

The regression and smoke testing suites were executed cleanly in the test environment:

```text
================================================================================
🔍 VERIFYING CHANNEL DEPRIORITIZATION RULES & CONFIGURATION UPDATES
================================================================================
✅ Found acquisition/sources.yaml: acquisition/sources.yaml
✅ Found acquisition/channel_registry.json: acquisition/channel_registry.json
✅ Found audit/telegram_channels_inventory.json: audit/telegram_channels_inventory.json
✅ Found telegram_lead_listener.py: telegram_lead_listener.py
✅ Found Reports/audit/channel_deprioritization_audit_report.md
✅ Found audit/channel_deprioritization_audit_report.md
✅ Found qa/fixtures/keyword_filter_fixtures.py
✅ Verified: All 29 independent keyword filter test fixtures passed.
✅ Verified: Quarantine budget multiplier parsing ('میلیون', 'هزار') passed.

📊 Evaluated Channels Verified: 24
   🟢 Golden Tier 1 (15m poll): 6
   🟢 Standard Tier 2 (60m poll): 3
   🟡 Deprioritized Tier 3 (360m poll): 8
   🟡 Quarantined / Muted (1440m poll): 2
   🔴 Dormant / Inactive Purged (0m poll): 5
   ⚡ Cycle Reduction: 70.4% (2304 -> 682 daily fetches)
   🎯 Keyword Filter Fixtures: 29/29 passed
   💰 Budget Multipliers Verified: 'میلیون' & 'هزار' against QUARANTINE_MIN_BUDGET_TOMAN

✅ PASS: All deprioritization rules, configurations, and dormant states verified.
```

- **Unit Test Suite Probes (`Tools/test_feed_smoke_testing.py`):** 8/8 tests passed (`Ran 8 tests in 0.009s, OK`).
- **Keyword Filter Unit Tests (`tests/test_keyword_filter.py`):** 5/5 tests passed (`Ran 5 tests in 0.044s, OK`).
- **Overall Verdict:** 🟢 **ALL QA CRITERIA & REGRESSION TESTS PASSED (ZERO REGRESSIONS)**
