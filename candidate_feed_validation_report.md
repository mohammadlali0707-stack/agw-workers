# Candidate Feed QA Validation & Smoke Testing Report

**Task:** QA Criteria, Feed Smoke Testing & Test Fixtures Segregation  
**Executor:** `agw-ACC5-w02` (Account 5 Quality Assurance & Feed Verification)  
**Parent Plan:** `issue-91-acc5-3d88bb5ac734` (Issue #91: *"کاهش اولویت کانال‌های تلگرامی بدون خروجی و جستجو برای سورس‌های جدید"*)  
**Execution Timestamp:** 2026-10-23T07:00:00Z  
**Verification Tooling:** [`Tools/smoke_test_candidate_feeds.py`](Tools/smoke_test_candidate_feeds.py)  
**Artifacts Generated & Maintained:**  
- Criteria & Procedures: [`qa/criteria/qa_criteria_and_procedures.md`](qa/criteria/qa_criteria_and_procedures.md)  
- Candidate Schema: [`qa/schemas/candidate_channel_schema.json`](qa/schemas/candidate_channel_schema.json)  
- Message Schema: [`qa/schemas/channel_message_schema.json`](qa/schemas/channel_message_schema.json)  
- Clean Operational Candidate Feeds: [`qa/candidates/candidate_feeds_sample.json`](qa/candidates/candidate_feeds_sample.json)  
- Segregated Negative Test Fixtures: [`qa/candidates/test_fixtures/negative_test_feeds.json`](qa/candidates/test_fixtures/negative_test_feeds.json)  
- Operational Smoke Test Results: [`Reports/qa/smoke_test_results.json`](Reports/qa/smoke_test_results.json)  
- Negative Fixture Test Results: [`Reports/qa/negative_test_results.json`](Reports/qa/negative_test_results.json)  
- Test Suite Probes: [`Tools/test_feed_smoke_testing.py`](Tools/test_feed_smoke_testing.py)  

---

## 1. Executive Summary & Audit Overview

Under the directives of Issue #91, the fleet is replacing stale, zero-output Telegram channels with high-yield, stable sources. As the QA gatekeeper of Account 5, **`agw-ACC5-w02`** established formal acceptance thresholds, deployed automated schema compatibility validators, and conducted a thorough audit and cleanup of candidate feeds.

### Dataset Audit and Test Fixture Segregation
An audit of the initial sample revealed that synthetic failure probes and negative test fixtures were co-mingled with live candidate channels. To guarantee that production ingestion pipelines remain clean and free of experimental artifacts, a strict architectural segregation was executed:
1. **Operational Clean Feeds (`candidate_feeds_sample.json`):** Now contains **exclusively** legitimate, operational candidates submitted for production registry consideration.
2. **Negative Test Fixtures (`test_fixtures/negative_test_feeds.json`):** All synthetic probes (`cand-dormant-legacy-04`, `cand-malformed-bot-05`, and `cand-ad-spam-forwarder-06`) were definitively relocated into a dedicated test fixtures directory to serve as baseline validation fixtures for the smoke testing suite.

### Headline Results

#### Clean Operational Candidate Feeds (`candidate_feeds_sample.json`)
- **Total Operational Channels Evaluated:** 4
- **Qualified for Production (PASS):** 3 (75.0%)
- **Conditional / Low-Priority Tier (CONDITIONAL):** 1 (25.0%)
- **Disqualified / Blocked (FAIL):** 0 (0.0%)

#### Negative Test Fixtures Suite (`test_fixtures/negative_test_feeds.json`)
- **Total Injected Negative Fixtures Evaluated:** 3
- **Correctly Caught & Disqualified by Gates (FAIL):** 3 (100.0%)
- **Target Failure Modes Verified:**
  - *Stale / Dormant Channel Breach:* `cand-dormant-legacy-04` (Staleness: 1145.5h vs. 168.0h threshold)
  - *Endpoint Error & Schema Malformation:* `cand-malformed-bot-05` (HTTP 500 & 0% message schema pass rate)
  - *Zero-Noise & Broadcast Duplication:* `cand-ad-spam-forwarder-06` (Ad ratio: 80.0% vs. 5.0% threshold & 20% duplicate rate)

---

## 2. Standardized QA Criteria & Acceptance Thresholds

The standardized QA policy evaluates candidate channels against four core dimensions with non-negotiable acceptance thresholds:

```
+---------------------------------------------------------------------------------------------------+
| DIMENSION                  | TARGET METRIC               | THRESHOLD          | GATE ACTION       |
+----------------------------+-----------------------------+--------------------+-------------------+
| 1. Operational Stability   | Endpoint HTTP Status        | 200 OK             | Rejection if !=200|
|                            | Latency (p95)               | < 2500 ms          | Downgrade score   |
|                            | Extraction Error Rate       | 0.0%               | Immediate Fail    |
| 2. Schema Compatibility    | JSON Schema Draft-07        | 100% Pass Rate     | Immediate Fail    |
|                            | Mandatory Properties        | 100% Present       | Immediate Fail    |
|                            | Character Encoding          | Valid UTF-8 NFC    | Rejection on error|
| 3. Update Frequency Health | Minimum Publication Cadence | >= 0.5 posts/day   | CONDITIONAL cap   |
|                            | Maximum Staleness           | <= 168 hours (7d)  | Immediate Fail    |
|                            | Anti-Flood Burst Limit      | <= 20 posts/hour   | Immediate Fail    |
| 4. Zero-Noise Performance  | Advertising / Sponsor Ratio | <= 5.0%            | Immediate Fail    |
|                            | Duplicate Message Rate      | 0.0% exact dups    | Immediate Fail    |
|                            | Minimum Substantive Length  | >= 25 characters   | Filtered out      |
+---------------------------------------------------------------------------------------------------+
```

### Composite Scoring Formula
$$S_{composite} = 0.25 \cdot S_{stability} + 0.30 \cdot S_{schema} + 0.25 \cdot S_{cadence} + 0.20 \cdot S_{noise}$$
- **PASS**: $S_{composite} \ge 85.0$, zero critical failures, cadence $\ge 0.5$ posts/day.
- **CONDITIONAL**: $70.0 \le S_{composite} < 85.0$ (or $S_{composite} \ge 85.0$ with low cadence warning), zero critical failures. Admitted to extended polling tier.
- **FAIL**: $S_{composite} < 70.0$ or ANY critical threshold breach.

---

## 3. Test Methodology & Execution Suite

The test methodology combines static analysis, schema compilation, and dynamic behavioral simulation:

1. **Schema Compilation**: The engine compiles [`candidate_channel_schema.json`](qa/schemas/candidate_channel_schema.json) and [`channel_message_schema.json`](qa/schemas/channel_message_schema.json) using Draft-07 JSON Schema rules.
2. **Operational Smoke Probe**: Tests the declared endpoint protocol (`https`, `telegram_mtproto`, `rss_bridge`), status code resolution, and response latency.
3. **Cadence Profiler**: Parses message timestamps, computes chronological spans, measures publishing velocity (posts/day), and calculates elapsed hours since the latest post relative to evaluation reference time `2026-10-23T05:30:00Z`.
4. **Zero-Noise & Spam Scanner**:
   - Regex pattern matcher for Persian/English advertising keywords (`#ad`, `#sponsored`, `#تبلیغ`, `#تبلیغات`, `خرید سرور`, `تخفیف ویژه`, `قرعه‌کشی`, `پشتیبانی فروش`, etc.).
   - Text normalization and cryptographic SHA-256 hashing to catch exact and broadcast duplicates.

---

## 4. Comprehensive Validation Results

### 4.1 Clean Operational Candidate Channels (`candidate_feeds_sample.json`)

| Candidate ID | Telegram Handle | Topic / Domain Category | Stability | Schema Pass | Cadence (Posts/Day) | Ad Ratio | Dupes | Composite Score | Final Status |
|---|---|---|---|---|---|---|---|---|---|
| `cand-tech-ai-01` | `@tech_ai_persian` | `ai_tech` | 100.0 | 100% (5/5) | 1.33 (10.0h ago) | 0.0% | 0 | **99.4** | 🟢 **PASS** |
| `cand-gamedev-godot-02` | `@godot_gamecraft_fa` | `game_development` | 100.0 | 100% (4/4) | 0.87 (8.5h ago) | 0.0% | 0 | **99.5** | 🟢 **PASS** |
| `cand-automation-cloud-03` | `@devops_automation_pro` | `automation_devops` | 100.0 | 100% (3/3) | 0.89 (12.3h ago) | 0.0% | 0 | **99.3** | 🟢 **PASS** |
| `cand-narrative-sound-07` | `@ambient_soundscapes_fa` | `narrative_audio` | 100.0 | 100% (2/2) | 0.15 (39.5h ago) | 0.0% | 0 | **87.1** | 🟡 **CONDITIONAL** |

### 4.2 Segregated Negative Test Fixtures Suite (`qa/candidates/test_fixtures/negative_test_feeds.json`)

| Fixture ID | Telegram Handle | Target Test Dimension | Stability | Schema Pass | Cadence (Posts/Day) | Ad / Dupe Ratio | Composite Score | Gating Verdict | Target Failure Mode Caught |
|---|---|---|---|---|---|---|---|---|---|
| `cand-dormant-legacy-04` | `@retro_archive_dormant` | Cadence & Staleness | 100.0 | 100% (1/1) | 0.02 (1145.5h ago) | 0.0% / 0 | 75.0 | 🔴 **FAIL (Expected)** | Critical Staleness Breach (1145.5h > 168.0h threshold) |
| `cand-malformed-bot-05` | `@broken_crypto_signals` | Operational & Schema | 0.0 (500) | 0% (0/2) | 0.81 (29.5h ago) | 0.0% / 0 | 43.2 | 🔴 **FAIL (Expected)** | HTTP 500 Endpoint Failure & Message Schema Violations |
| `cand-ad-spam-forwarder-06` | `@vpn_promo_linkfarm` | Zero-Noise & Spam | 100.0 | 100% (5/5) | 5.00 (9.5h ago) | 80.0% / 20.0% | 82.6 | 🔴 **FAIL (Expected)** | Commercial Ad Ratio (80% > 5%) & Broadcast Duplicate Spam |

---

## 5. Candidate Status Deep-Dive & Audit Findings

### 🟢 Approved Production Candidates (PASS)

#### 1. `cand-tech-ai-01` (`@tech_ai_persian`) — Score: 99.4/100
- **Category:** AI & Machine Learning (`ai_tech`)
- **Compatibility:** Passed 5/5 message schema validations with full metadata (tags, forwards, views).
- **Cadence:** Healthy update rate of 1.33 posts/day; latest post 10 hours ago. Zero burst flooding.
- **Noise:** 0.0% ads, 0 duplicates. High substantive technical content (MoE architectures, quantization benchmarks, agent protocols).
- **Recommendation:** **Approved** for primary production registry. Ingestion polling interval: **60 minutes**.

#### 2. `cand-gamedev-godot-02` (`@godot_gamecraft_fa`) — Score: 99.5/100
- **Category:** Game Development & 3D Shaders (`game_development`)
- **Compatibility:** Passed 4/4 message schema validations. Valid UTF-8 Persian text with attached technical code snippets and shader explanations.
- **Cadence:** 0.87 posts/day; latest post 8.5 hours ago.
- **Noise:** 0.0% ads, 0 duplicates. Directly aligns with TBS (Godot 4.x interaction, headless testing, audio ambience).
- **Recommendation:** **Approved** for primary production registry. Ingestion polling interval: **120 minutes**.

#### 3. `cand-automation-cloud-03` (`@devops_automation_pro`) — Score: 99.3/100
- **Category:** CI/CD & Automation (`automation_devops`)
- **Compatibility:** Passed 3/3 message schema validations.
- **Cadence:** 0.89 posts/day; latest post 12.3 hours ago.
- **Noise:** 0.0% ads, 0 duplicates. High-signal content covering runner quotas, event-driven webhooks, and multi-account secret hygiene.
- **Recommendation:** **Approved** for primary production registry. Ingestion polling interval: **180 minutes**.

---

### 🟡 Conditional Candidates (CONDITIONAL)

#### 4. `cand-narrative-sound-07` (`@ambient_soundscapes_fa`) — Score: 87.1/100
- **Category:** Narrative Audio & Sound Design (`narrative_audio`)
- **Audit Finding:**
  - Operational stability: 100% reachable.
  - Schema compatibility: 100% valid (2/2 audio messages with `media_metadata` including duration and mime-types).
  - Noise: 0.0% ads, 0 duplicates.
  - **Cadence Warning:** Publication cadence is 0.15 posts/day (only 2 posts over 13 days). While the last post is fresh (39.5 hours ago), the posting frequency falls below the standard 0.5 posts/day threshold.
- **Gating Decision:** Gated to **CONDITIONAL**. Admitted to the **Low-Priority Extended Polling Tier**.
- **Recommendation:** Ingestion polling interval configured to **360 minutes** (6 hours) to conserve runner cycles while capturing ambient foley releases.

---

### 🔴 Segregated Negative Test Fixtures Suite (FAIL - Injected Test Probes)

These channels were audited, cataloged, and moved to [`qa/candidates/test_fixtures/negative_test_feeds.json`](qa/candidates/test_fixtures/negative_test_feeds.json) to serve as dedicated regression fixtures:

#### 5. `cand-dormant-legacy-04` (`@retro_archive_dormant`) — Score: 75.0/100 -> **FAIL**
- **Test Objective:** Verification of Staleness and Inactivity Thresholds.
- **Target Breach:** **CRITICAL STALENESS BREACH**.
- **Audit Findings:** The feed's last message was published 1145.5 hours ago (~47.7 days ago), severely breaching the maximum staleness threshold of 168.0 hours (7 days).
- **Segregation Status:** Relocated to test fixtures. Excluded from production candidate feeds.

#### 6. `cand-malformed-bot-05` (`@broken_crypto_signals`) — Score: 43.2/100 -> **FAIL**
- **Test Objective:** Verification of Endpoint Error Detection and JSON Schema Guardrails.
- **Target Breach:** **OPERATIONAL FAILURE & TOTAL SCHEMA INCOMPATIBILITY**.
- **Audit Findings:**
  - Operational endpoint returned HTTP 500 Internal Server Error.
  - 0% message schema pass rate (0/2 valid messages).
  - Schema violations: `message_id` is a string instead of integer; `timestamp` is invalid non-ISO string; `content_type` contains unrecognized enum; `text` is empty; `views` is negative (-5); `is_forward` is string rather than boolean.
- **Segregation Status:** Relocated to test fixtures. Excluded from production candidate feeds.

#### 7. `cand-ad-spam-forwarder-06` (`@vpn_promo_linkfarm`) — Score: 82.6/100 -> **FAIL**
- **Test Objective:** Verification of Commercial Spam Regex and Broadcast Duplicate Hash Matching.
- **Target Breach:** **ZERO-NOISE VIOLATION & DUPLICATE BROADCAST SPAM**.
- **Audit Findings:**
  - 80.0% of messages contained commercial advertising and referral tags (`#ad`, `#تبلیغ`, `#sponsored`, affiliate links), exceeding the 5.0% threshold by 16x.
  - Detected exact duplicate message text broadcast across multiple hours (20.0% duplicate rate).
- **Segregation Status:** Relocated to test fixtures. Excluded from production candidate feeds.

---

## 6. Handover Instructions for `agw-ACC5-w03`

Subordinate **`agw-ACC5-w03`** (*Source Registry Assembly and Pipeline Integration*) should ingest the validated output as follows:

1. **Registry Source File**: Ingest **exclusively** from [`qa/candidates/candidate_feeds_sample.json`](qa/candidates/candidate_feeds_sample.json), which has been audited and cleaned of all negative test fixtures.
2. **Registry Composition**: Register only the 3 PASS candidates and 1 CONDITIONAL candidate:
   - Primary Active Tier (High Cadence):
     - `@tech_ai_persian` (`cand-tech-ai-01`): poll interval = `60m`
     - `@godot_gamecraft_fa` (`cand-gamedev-godot-02`): poll interval = `120m`
     - `@devops_automation_pro` (`cand-automation-cloud-03`): poll interval = `180m`
   - Secondary Extended Tier (Low Cadence):
     - `@ambient_soundscapes_fa` (`cand-narrative-sound-07`): poll interval = `360m`
3. **CI/CD Regression Probes**: Use [`qa/candidates/test_fixtures/negative_test_feeds.json`](qa/candidates/test_fixtures/negative_test_feeds.json) in automated pipeline tests to continuously verify that rejection gates prevent faulty channels from entering the registry.
4. **Schema Enforcement**: Enforce [`qa/schemas/channel_message_schema.json`](qa/schemas/channel_message_schema.json) as the mandatory validation gate in the production ingestion pipeline prior to message persistence.
5. **Automated Weekly Smoke Check**: Schedule [`Tools/smoke_test_candidate_feeds.py`](Tools/smoke_test_candidate_feeds.py) as a weekly diagnostic workflow to continuously prune degrading sources.
