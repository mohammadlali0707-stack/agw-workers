#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""telegram_lead_listener.py.

Telegram Lead Ingestion & Event Dispatcher Engine with Deprioritization Rules.
Author: agw-ACC2-w03 (Account 2 Worker 03)
Under: Account 2 Lead Supervision
Issue: #91 - Channel Deprioritization Rules and Config Updates
Plan ID: 3d88bb5ac734

Implements:
1. Complete removal and de-registration of confirmed dead channels from active polling.
2. Quarantining and muting high-noise spam channels (@job_freelancer, @freelancer_job).
3. Throttling and downgrading community chat supergroups to Tier 3 (360m poll interval)
   with strict employer keyword gating.
4. Retaining Tier 1 high-cadence priority (15m poll interval) on golden channels.
"""

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("telegram_lead_listener")

# ==============================================================================
# 1. CHANNEL GROUP CONSTANTS (Updated following Issue #91 Audit)
# ==============================================================================

# Tier 1: Golden High-Yield Commercial Sources (15m poll, immediate webhook)
GOLDEN_TIER_1_GROUPS: List[str] = [
    "@karlancer_projects",
    "@ParsCodersCom",
    "@job_coder",
    "@remote_ir",
    "@WordPressProgettoGp",
    "@woocommerce_iran",
]

# Tier 2: Standard Priority Moderated Sources (60m poll, batched)
STANDARD_TIER_2_GROUPS: List[str] = [
    "@laravelGroup",
    "@iran_itjob",
    "@Quera",
]

# Tier 3: Deprioritized Community Chat Groups (360m poll, strict keyword gate)
# Downgraded during audit due to high chatter ratio (>95%) and prolonged zero output
DEPRIORITIZED_TIER_3_GROUPS: List[str] = [
    "@Group_PyDev",
    "@js_group",
    "@dm_jobs",
    "@ir_startups",
    "@ai_automation_ir",
    "@phpgp",
    "@flutter_iranian",
    "@uiux_designers_ir",
]

# Quarantine: Muted Spam / Classifieds (1440m poll, muted, high budget barrier)
QUARANTINED_GROUPS: List[str] = [
    "@job_freelancer",
    "@freelancer_job",
]

# Dormant / Dead Channels (EXPLICITLY DEREGISTERED & PURGED - 0m poll, inactive)
# These channels are permanently excluded from DEFAULT_TARGET_GROUPS to eliminate
# wasted runner cycles and prevent MTProto crash loops.
DORMANT_DEAD_GROUPS: Dict[str, Dict[str, Any]] = {
    "@freelance_projects_ir": {
        "reason": "CHANNEL_STALL_NO_NEW_POSTS",
        "zero_output_duration_days": 25,
        "error_rate_pct": 85.0,
        "action": "purged_from_listener",
    },
    "@karyab_it": {
        "reason": "POLL_TIMEOUT_ZERO_OUTPUT",
        "zero_output_duration_days": 21,
        "error_rate_pct": 78.4,
        "action": "purged_from_listener",
    },
    "@ecommerce_iranian": {
        "reason": "MTPROTO_CHANNEL_PRIVATE",
        "zero_output_duration_days": 38,
        "error_rate_pct": 100.0,
        "action": "purged_from_listener",
    },
    "@xseo_web": {
        "reason": "CHAT_ADMIN_REQUIRED",
        "zero_output_duration_days": 22,
        "error_rate_pct": 100.0,
        "action": "purged_from_listener",
    },
    "@dev_jobs_iran": {
        "reason": "MTPROTO_USERNAME_INVALID",
        "zero_output_duration_days": 33,
        "error_rate_pct": 100.0,
        "action": "purged_from_listener",
    },
}

# DEFAULT_TARGET_GROUPS: The authoritative active listener list.
# Dead channels are strictly excluded.
DEFAULT_TARGET_GROUPS: List[str] = (
    GOLDEN_TIER_1_GROUPS +
    STANDARD_TIER_2_GROUPS +
    DEPRIORITIZED_TIER_3_GROUPS
)

# Mandatory employer keywords required for Tier 3 community messages
TIER_3_REQUIRED_KEYWORDS: List[str] = [
    r"بودجه",
    r"نیازمند\s+برنامه‌نویس",
    r"پروژه",
    r"طراحی\s+اختصاصی",
    r"استخدام\s+برنامه‌نویس",
    r"همکاری\s+پروژه‌ای",
    r"قرارداد",
    r"توسعه‌دهنده",
    r"سفارش\s+کار",
]

TIER_3_KEYWORD_REGEX = re.compile("|".join(TIER_3_REQUIRED_KEYWORDS), re.IGNORECASE | re.UNICODE)

# Minimum budget requirement for quarantined channels (20,000,000 Tomans)
QUARANTINE_MIN_BUDGET_TOMAN = 20_000_000
BUDGET_EXTRACTION_REGEX = re.compile(
    r"(?:بودجه|مبلغ|هزینه)\s*[:=\-]?\s*(\d[\d,\.]{4,12})\s*(?:تومان|میلیون|هزار)",
    re.IGNORECASE | re.UNICODE
)


# ==============================================================================
# 2. SCHEDULING & CONFIGURATION ENGINE
# ==============================================================================

class ChannelSchedulingManager:
    """Manages channel scheduling intervals, tiers, and filtering policies."""

    def __init__(self, sources_file: Optional[str] = None):
        if sources_file:
            self.sources_file = sources_file
        else:
            cur_dir = os.path.dirname(os.path.abspath(__file__))
            candidate1 = os.path.join(cur_dir, "acquisition", "sources.yaml")
            candidate2 = os.path.join(cur_dir, "sources.yaml")
            self.sources_file = candidate1 if os.path.isfile(candidate1) else candidate2
        self.channel_configs: Dict[str, Dict[str, Any]] = {}
        self._load_configs()

    def _load_configs(self) -> None:
        """Loads channel configurations from YAML file or falls back to constants."""
        if os.path.isfile(self.sources_file):
            try:
                import yaml
                with open(self.sources_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    for ch in data.get("channels", []):
                        handle = ch.get("handle")
                        if handle:
                            self.channel_configs[handle] = ch
                logger.info(f"Loaded {len(self.channel_configs)} channel configurations from {self.sources_file}")
                return
            except Exception as e:
                logger.warning(f"Could not parse YAML ({e}), falling back to in-memory configuration.")

        # In-memory configuration fallback
        for h in GOLDEN_TIER_1_GROUPS:
            self.channel_configs[h] = {
                "handle": h,
                "tier": "tier_1",
                "operational_status": "active",
                "polling_interval_minutes": 15,
                "fetch_frequency_daily": 96,
                "active": True,
                "mute": False,
                "immediate_webhook": True,
            }
        for h in STANDARD_TIER_2_GROUPS:
            self.channel_configs[h] = {
                "handle": h,
                "tier": "tier_2",
                "operational_status": "active",
                "polling_interval_minutes": 60,
                "fetch_frequency_daily": 24,
                "active": True,
                "mute": False,
                "immediate_webhook": False,
            }
        for h in DEPRIORITIZED_TIER_3_GROUPS:
            self.channel_configs[h] = {
                "handle": h,
                "tier": "tier_3",
                "operational_status": "deprioritized",
                "polling_interval_minutes": 360,
                "fetch_frequency_daily": 4,
                "active": True,
                "mute": False,
                "immediate_webhook": False,
                "keyword_filter_strict": True,
            }
        for h in QUARANTINED_GROUPS:
            self.channel_configs[h] = {
                "handle": h,
                "tier": "quarantine",
                "operational_status": "quarantined",
                "polling_interval_minutes": 1440,
                "fetch_frequency_daily": 1,
                "active": True,
                "mute": True,
                "immediate_webhook": False,
            }
        for h, meta in DORMANT_DEAD_GROUPS.items():
            self.channel_configs[h] = {
                "handle": h,
                "tier": "dormant",
                "operational_status": "dormant",
                "polling_interval_minutes": 0,
                "fetch_frequency_daily": 0,
                "active": False,
                "mute": True,
                "immediate_webhook": False,
                "deprioritization_reason": meta["reason"],
            }

    def get_tier(self, handle: str) -> str:
        """Returns the scheduling tier for the given handle."""
        cfg = self.channel_configs.get(handle)
        if cfg:
            return cfg.get("tier", "unknown")
        if handle in DORMANT_DEAD_GROUPS:
            return "dormant"
        return "tier_3"

    def get_polling_interval(self, handle: str) -> int:
        """Returns polling interval in minutes. Returns 0 for dormant/dead channels."""
        cfg = self.channel_configs.get(handle)
        if cfg:
            return cfg.get("polling_interval_minutes", 360)
        if handle in DORMANT_DEAD_GROUPS:
            return 0
        if handle in GOLDEN_TIER_1_GROUPS:
            return 15
        if handle in STANDARD_TIER_2_GROUPS:
            return 60
        if handle in QUARANTINED_GROUPS:
            return 1440
        return 360

    def is_active(self, handle: str) -> bool:
        """Checks if a channel is authorized for active polling."""
        if handle in DORMANT_DEAD_GROUPS:
            return False
        cfg = self.channel_configs.get(handle)
        if cfg:
            return cfg.get("active", True)
        return True

    def should_process_event(self, handle: str, message_text: str) -> Tuple[bool, str]:
        """Evaluates whether an incoming message passes tier filtering gates.

        Returns (pass_filter, reason).
        """
        if not self.is_active(handle):
            return False, f"Channel {handle} is dormant/inactive; events suppressed."

        tier = self.get_tier(handle)

        # Tier 1 & 2: Process directly
        if tier in ("tier_1", "tier_2"):
            return True, f"Passed standard pipeline for {tier}."

        # Quarantine Tier: Muted; requires high budget verification
        if tier == "quarantine":
            match = BUDGET_EXTRACTION_REGEX.search(message_text)
            if not match:
                return False, "Quarantined channel: suppressed due to missing explicit commercial budget."
            raw_val = match.group(1).replace(",", "").replace(".", "")
            try:
                budget = int(raw_val)
                if budget >= QUARANTINE_MIN_BUDGET_TOMAN:
                    return True, f"Quarantine exemption granted: verified budget >= {QUARANTINE_MIN_BUDGET_TOMAN} Toman."
            except ValueError:
                pass
            return False, "Quarantined channel: budget below 20M Toman threshold."

        # Tier 3: Strict employer keyword gating
        if tier == "tier_3":
            if not TIER_3_KEYWORD_REGEX.search(message_text):
                return False, "Tier 3 Community Chatter Gate: message missing required employer keywords."
            return True, "Tier 3 Gate: matched required employer keyword pattern."

        return False, "Unrecognized channel tier; suppressed."

    def calculate_cycle_savings(self) -> Dict[str, Any]:
        """Computes before vs after daily fetch statistics and cycle savings."""
        # Baseline before audit: All 24 channels polled at default 15m (96 fetches/day)
        baseline_fetches_per_channel = 96
        total_channels = 24
        baseline_total_daily_fetches = total_channels * baseline_fetches_per_channel

        # After deprioritization rules:
        # Tier 1: 6 channels * 96 = 576
        # Tier 2: 3 channels * 24 = 72
        # Tier 3: 8 channels * 4 = 32
        # Quarantine: 2 channels * 1 = 2
        # Dormant: 5 channels * 0 = 0
        tier_1_daily = len(GOLDEN_TIER_1_GROUPS) * 96
        tier_2_daily = len(STANDARD_TIER_2_GROUPS) * 24
        tier_3_daily = len(DEPRIORITIZED_TIER_3_GROUPS) * 4
        quarantine_daily = len(QUARANTINED_GROUPS) * 1
        dormant_daily = len(DORMANT_DEAD_GROUPS) * 0

        post_total_daily_fetches = (
            tier_1_daily + tier_2_daily + tier_3_daily + quarantine_daily + dormant_daily
        )

        savings_fetches = baseline_total_daily_fetches - post_total_daily_fetches
        savings_pct = (savings_fetches / baseline_total_daily_fetches) * 100.0

        return {
            "total_channels": total_channels,
            "baseline_daily_fetches": baseline_total_daily_fetches,
            "post_daily_fetches": post_total_daily_fetches,
            "daily_fetches_saved": savings_fetches,
            "reduction_percentage": round(savings_pct, 2),
            "tier_breakdown": {
                "tier_1_golden": {"count": len(GOLDEN_TIER_1_GROUPS), "interval_m": 15, "daily_fetches": tier_1_daily},
                "tier_2_standard": {"count": len(STANDARD_TIER_2_GROUPS), "interval_m": 60, "daily_fetches": tier_2_daily},
                "tier_3_deprioritized": {"count": len(DEPRIORITIZED_TIER_3_GROUPS), "interval_m": 360, "daily_fetches": tier_3_daily},
                "quarantine_muted": {"count": len(QUARANTINED_GROUPS), "interval_m": 1440, "daily_fetches": quarantine_daily},
                "dormant_eliminated": {"count": len(DORMANT_DEAD_GROUPS), "interval_m": 0, "daily_fetches": dormant_daily},
            }
        }


# ==============================================================================
# 3. VERIFICATION & TEST ROUTINE
# ==============================================================================

def _load_fixtures() -> Dict[str, Any]:
    """Loads keyword filter test fixtures from external fixture module.

    Separates validation sample data from listener engine operational logic.
    """
    try:
        from acquisition.test_fixtures import KEYWORD_FILTER_FIXTURES
        return KEYWORD_FILTER_FIXTURES
    except ImportError:
        pass
    try:
        from tests.fixtures import KEYWORD_FILTER_FIXTURES
        return KEYWORD_FILTER_FIXTURES
    except ImportError:
        pass

    cur_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = cur_dir if os.path.basename(cur_dir) != "acquisition" else os.path.dirname(cur_dir)
    for p in [repo_root, os.path.join(repo_root, "acquisition")]:
        if p not in sys.path:
            sys.path.insert(0, p)

    try:
        from acquisition.test_fixtures import KEYWORD_FILTER_FIXTURES
        return KEYWORD_FILTER_FIXTURES
    except ImportError:
        from test_fixtures import KEYWORD_FILTER_FIXTURES
        return KEYWORD_FILTER_FIXTURES


def verify_deprioritization_wiring(fixtures: Optional[Dict[str, Any]] = None) -> bool:
    """Verifies that all deprioritization constraints and rules are properly wired."""
    print("=" * 80)
    print("🔍 VERIFYING TELEGRAM LEAD LISTENER CONFIGURATION & WIRING")
    print("=" * 80)

    mgr = ChannelSchedulingManager()
    stats = mgr.calculate_cycle_savings()

    # 1. Verify dead channels are completely excluded from DEFAULT_TARGET_GROUPS
    dead_leaks = [ch for ch in DORMANT_DEAD_GROUPS if ch in DEFAULT_TARGET_GROUPS]
    if dead_leaks:
        print(f"❌ FAIL: Confirmed dead channels leaked into DEFAULT_TARGET_GROUPS: {dead_leaks}")
        return False
    print(f"✅ Verified: All 5 confirmed dead channels are strictly excluded from DEFAULT_TARGET_GROUPS.")

    # 2. Verify all dead channels return interval 0 and active False
    for dead_ch in DORMANT_DEAD_GROUPS:
        if mgr.is_active(dead_ch):
            print(f"❌ FAIL: Dead channel {dead_ch} reports active = True")
            return False
        if mgr.get_polling_interval(dead_ch) != 0:
            print(f"❌ FAIL: Dead channel {dead_ch} has non-zero interval: {mgr.get_polling_interval(dead_ch)}")
            return False
    print(f"✅ Verified: All 5 dead channels return active=False and polling_interval=0m.")

    # 3. Verify Tier 3 polling intervals are exactly 360 minutes
    for t3_ch in DEPRIORITIZED_TIER_3_GROUPS:
        if mgr.get_polling_interval(t3_ch) != 360:
            print(f"❌ FAIL: Tier 3 channel {t3_ch} has invalid interval: {mgr.get_polling_interval(t3_ch)}")
            return False
    print(f"✅ Verified: All 8 Tier 3 channels configured with 360m (6h) polling intervals.")

    # 4. Verify Quarantine polling intervals are exactly 1440 minutes
    for q_ch in QUARANTINED_GROUPS:
        if mgr.get_polling_interval(q_ch) != 1440:
            print(f"❌ FAIL: Quarantine channel {q_ch} has invalid interval: {mgr.get_polling_interval(q_ch)}")
            return False
    print(f"✅ Verified: All 2 Quarantined channels configured with 1440m (24h) polling intervals.")

    # 5. Verify Tier 1 polling intervals are exactly 15 minutes
    for t1_ch in GOLDEN_TIER_1_GROUPS:
        if mgr.get_polling_interval(t1_ch) != 15:
            print(f"❌ FAIL: Tier 1 channel {t1_ch} has invalid interval: {mgr.get_polling_interval(t1_ch)}")
            return False
    print(f"✅ Verified: All 6 Golden Tier 1 channels configured with 15m polling intervals.")

    # 6. Verify Keyword Gating on Tier 3 using external fixtures
    fx = fixtures if fixtures is not None else _load_fixtures()
    test_ch = fx.get("tier_3_test_channel", "@Group_PyDev")
    chatter_msgs = fx.get("chatter_messages") or [fx.get("chatter_msg", "")]
    lead_msgs = fx.get("qualified_lead_messages") or [fx.get("lead_msg", "")]
    chatter_msg = chatter_msgs[0]
    lead_msg = lead_msgs[0]

    passed_chatter, _ = mgr.should_process_event(test_ch, chatter_msg)
    passed_lead, _ = mgr.should_process_event(test_ch, lead_msg)

    if passed_chatter:
        print(f"❌ FAIL: Chatter message unexpectedly passed Tier 3 filter.")
        return False
    if not passed_lead:
        print(f"❌ FAIL: Qualified lead message failed Tier 3 filter.")
        return False
    print(f"✅ Verified: Tier 3 keyword filter correctly blocks student chatter and admits qualified leads.")

    # 7. Print savings summary
    print("\n📊 OPERATIONAL CYCLE SAVINGS METRICS:")
    print(f"   Baseline Daily Fetches:  {stats['baseline_daily_fetches']}")
    print(f"   Post-Deprioritization:   {stats['post_daily_fetches']}")
    print(f"   Daily Fetches Saved:     {stats['daily_fetches_saved']} ({stats['reduction_percentage']}%)")
    print(f"   Dead Channel Wasted Cycles: 100% Eliminated (0 fetches)")

    result_data = {
        "v": 1,
        "probe": "telegram_lead_listener_deprioritization",
        "status": "pass",
        "data": stats
    }
    print(f"\n##TBS##{json.dumps(result_data, sort_keys=True)}")
    return True


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--verify", "-v", "verify", "--status"):
        success = verify_deprioritization_wiring()
        sys.exit(0 if success else 1)
    else:
        # Default execution: run verification
        success = verify_deprioritization_wiring()
        sys.exit(0 if success else 1)
