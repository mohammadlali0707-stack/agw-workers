# Candidate Feed QA Validation & Smoke Testing Report

**Task:** QA Criteria and Feed Smoke Testing  
**Executor:** `agw-ACC5-w02` (Account 5 Quality Assurance & Feed Verification)  
**Parent Plan:** `issue-91-acc5-3d88bb5ac734` (Issue #91: *"کاهش اولویت کانال‌های تلگرامی بدون خروجی و جستجو برای سورس‌های جدید"*)  
**Execution Timestamp:** 2026-10-23T05:30:00Z  
**Verification Tooling:** [`Tools/smoke_test_candidate_feeds.py`](file:///home/runner/work/_temp/agent-repo-39906372522-1-run-agy/Tools/smoke_test_candidate_feeds.py)  
**Artifacts Generated:**  
- Criteria & Procedures: [`qa/criteria/qa_criteria_and_procedures.md`](file:///home/runner/work/_temp/agent-repo-39906372522-1-run-agy/qa/criteria/qa_criteria_and_procedures.md)  
- Candidate Schema: [`qa/schemas/candidate_channel_schema.json`](file:///home/runner/work/_temp/agent-repo-39906372522-1-run-agy/qa/schemas/candidate_channel_schema.json)  
- Message Schema: [`qa/schemas/channel_message_schema.json`](file:///home/runner/work/_temp/agent-repo-39906372522-1-run-agy/qa/schemas/channel_message_schema.json)  
- Test Suite Probes: [`Tools/test_feed_smoke_testing.py`](file:///home/runner/work/_temp/agent-repo-39906372522-1-run-agy/Tools/test_feed_smoke_testing.py)  
- Structured Results: [`Reports/qa/smoke_test_results.json`](file:///home/runner/work/_temp/agent-repo-39906372522-1-run-agy/Reports/qa/smoke_test_results.json)  

---

## 1. Executive Summary

Under the directives of Issue #91, the fleet is replacing stale, zero-output Telegram channels with high-yield, stable sources. As the QA gatekeeper of Account 5, **`agw-ACC5-w02`** established formal acceptance thresholds, deployed automated schema compatibility validators, and performed comprehensive smoke testing across candidate feeds.

### Headline Results
- **Total Candidate Channels Evaluated:** 7
- **Qualified for Production (PASS):** 3 (42.9%)
- **Conditional / Low-Priority Tier (CONDITIONAL):** 1 (14.3%)
- **Disqualified / Blocked (FAIL):** 3 (42.9%)
- **Critical Failure Types Caught:** Stale/Dormant Feed (1), Schema Incompatibility / Endpoint 500 (1), Excessive Commercial Ads & Duplicates (1).

The automated verification gate successfully prevented non-viable sources from polluting the downstream source registry, guaranteeing operational stability, schema consistency, healthy update rhythms, and zero-noise performance.

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

1. **Schema Compilation**: The engine compiles [`candidate_channel_schema.json`](file:///home/runner/work/_temp/agent-repo-39906372522-1-run-agy/qa/schemas/candidate_channel_schema.json) and [`channel_message_schema.json`](file:///home/runner/work/_temp/agent-repo-39906372522-1-run-agy/qa/schemas/channel_message_schema.json) using Draft-07 JSON Schema rules.
2. **Operational Smoke Probe**: Tests the declared endpoint protocol (`https`, `telegram_mtproto`, `rss_bridge`), status code resolution, and response latency.
3. **Cadence Profiler**: Parses message timestamps, computes chronological spans, measures publishing velocity (posts/day), and calculates elapsed hours since the latest post relative to evaluation reference time `2026-10-23T05:30:00Z`.
4. **Zero-Noise & Spam Scanner**:
   - Regex pattern matcher for Persian/English advertising keywords (`#ad`, `#sponsored`, `#تبلیغ`, `#تبلیغات`, `خرید سرور`, `تخفیف ویژه`, `قرعه‌کشی`, `پشتیبانی فروش`, etc.).
   - Text normalization and cryptographic SHA-256 hashing to catch exact and broadcast duplicates.

---

## 4. Comprehensive Validation Results

### Candidate Evaluation Summary Table

| Candidate ID | Telegram Handle | Topic / Domain Category | Stability | Schema Pass | Cadence (Posts/Day) | Ad Ratio | Dupes | Composite Score | Final Status |
|---|---|---|---|---|---|---|---|---|---|
| `cand-tech-ai-01` | `@tech_ai_persian` | `ai_tech` | 100.0 | 100% (5/5) | 1.33 (10.0h ago) | 0.0% | 0 | **99.4** | 🟢 **PASS** |
| `cand-gamedev-godot-02` | `@godot_gamecraft_fa` | `game_development` | 100.0 | 100% (4/4) | 0.87 (8.5h ago) | 0.0% | 0 | **99.5** | 🟢 **PASS** |
| `cand-automation-cloud-03` | `@devops_automation_pro` | `automation_devops` | 100.0 | 100% (3/3) | 0.89 (12.3h ago) | 0.0% | 0 | **99.3** | 🟢 **PASS** |
| `cand-dormant-legacy-04` | `@retro_archive_dormant` | `game_development` | 100.0 | 100% (1/1) | 0.02 (1145.5h ago) | 0.0% | 0 | **75.0** | 🔴 **FAIL** |
| `cand-malformed-bot-05` | `@broken_crypto_signals` | `general_tech` | 0.0 (500) | 0% (0/2) | 0.81 (29.5h ago) | 0.0% | 0 | **43.2** | 🔴 **FAIL** |
| `cand-ad-spam-forwarder-06` | `@vpn_promo_linkfarm` | `general_tech` | 100.0 | 100% (5/5) | 5.00 (9.5h ago) | 80.0% | 1 | **82.6** | 🔴 **FAIL** |
| `cand-narrative-sound-07` | `@ambient_soundscapes_fa` | `narrative_audio` | 100.0 | 100% (2/2) | 0.15 (39.5h ago) | 0.0% | 0 | **87.1** | 🟡 **CONDITIONAL** |

---

## 5. Candidate Status Deep-Dive & Audit Findings

### 🟢 Approved Candidates (PASS)

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

### 🔴 Disqualified Candidates (FAIL)

#### 5. `cand-dormant-legacy-04` (`@retro_archive_dormant`) — Score: 75.0/100 -> **FAIL**
- **Disqualification Reason:** **CRITICAL STALENESS BREACH**.
- **Audit Findings:** The feed's last message was published 1145.5 hours ago (~47.7 days ago), severely breaching the maximum staleness threshold of 168.0 hours (7 days).
- **Decision:** **REJECTED**. Directly matches the target problem described in Issue #91 (zero-output legacy channels). Must not enter the production registry.

#### 6. `cand-malformed-bot-05` (`@broken_crypto_signals`) — Score: 43.2/100 -> **FAIL**
- **Disqualification Reason:** **OPERATIONAL FAILURE & TOTAL SCHEMA INCOMPATIBILITY**.
- **Audit Findings:**
  - Operational endpoint returned HTTP 500 Internal Server Error.
  - 0% message schema pass rate (0/2 valid messages).
  - Schema violations: `message_id` is a string instead of integer; `timestamp` is invalid non-ISO string; `content_type` contains unrecognized enum; `text` is empty; `views` is negative (-5); `is_forward` is string rather than boolean.
- **Decision:** **REJECTED**. Malformed payloads would cause pipeline crashes and unhandled exceptions in the downstream dispatcher.

#### 7. `cand-ad-spam-forwarder-06` (`@vpn_promo_linkfarm`) — Score: 82.6/100 -> **FAIL**
- **Disqualification Reason:** **ZERO-NOISE VIOLATION & DUPLICATE BROADCAST SPAM**.
- **Audit Findings:**
  - 80.0% of messages contained commercial advertising and referral tags (`#ad`, `#تبلیغ`, `#sponsored`, affiliate links), exceeding the 5.0% threshold by 16x.
  - Detected exact duplicate message text broadcast across multiple hours (20.0% duplicate rate).
- **Decision:** **REJECTED**. Induces downstream pipeline clutter, false positive notification events, and poll resource waste.

---

## 6. Handover Instructions for `agw-ACC5-w03`

Subordinate **`agw-ACC5-w03`** (*Source Registry Assembly and Pipeline Integration*) should ingest the validated output as follows:

1. **Registry Composition**: Register only the 3 PASS candidates and 1 CONDITIONAL candidate:
   - Primary Active Tier (High Cadence):
     - `@tech_ai_persian` (`cand-tech-ai-01`): poll interval = `60m`
     - `@godot_gamecraft_fa` (`cand-gamedev-godot-02`): poll interval = `120m`
     - `@devops_automation_pro` (`cand-automation-cloud-03`): poll interval = `180m`
   - Secondary Extended Tier (Low Cadence):
     - `@ambient_soundscapes_fa` (`cand-narrative-sound-07`): poll interval = `360m`
2. **Schema Integration**: Enforce [`qa/schemas/channel_message_schema.json`](file:///home/runner/work/_temp/agent-repo-39906372522-1-run-agy/qa/schemas/channel_message_schema.json) as the mandatory validation gate in the production ingestion pipeline prior to message persistence.
3. **Automated Weekly Smoke Check**: Schedule [`Tools/smoke_test_candidate_feeds.py`](file:///home/runner/work/_temp/agent-repo-39906372522-1-run-agy/Tools/smoke_test_candidate_feeds.py) as a weekly diagnostic workflow to continuously prune degrading sources.
