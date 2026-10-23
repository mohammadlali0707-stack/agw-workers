#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""acquisition/test_fixtures.py.

Validation fixtures, sample leads, and test messages for Telegram Lead Listener
and Keyword Gating Filters.
Separated from operational listener logic to ensure production engine is clean
of embedded dummy / test data.

Author: agw-ACC2-w02 (Account 2 Worker 02)
Supervision: Account 2 Lead Supervision
Task: Clean up hardcoded test messages & separate keyword filter test fixtures
"""

from typing import Any, Dict, List, Tuple

# Sample test messages extracted from operational listener logic
CHATTER_MSG: str = "سلام دوستان چطوری میشه توی پایتون لیست رو معکوس کرد؟"
LEAD_MSG: str = "پروژه فوری: نیازمند برنامه‌نویس ارشد جنگو با بودجه ۳۰ میلیون تومان جهت طراحی اختصاصی"

SAMPLE_CHATTER_MSG: str = CHATTER_MSG
SAMPLE_LEAD_MSG: str = LEAD_MSG

# Test channel handles for fixture verification
TIER_3_TEST_CHANNEL: str = "@Group_PyDev"
QUARANTINE_TEST_CHANNEL: str = "@job_freelancer"
GOLDEN_TIER_1_TEST_CHANNEL: str = "@karlancer_projects"

# Comprehensive test fixtures for Tier 3 community chatter vs qualified leads
CHATTER_MESSAGES: List[str] = [
    "سلام دوستان چطوری میشه توی پایتون لیست رو معکوس کرد؟",
    "کسی منبع خوب برای یادگیری فلاتر سراغ داره؟",
    "سلام به نظرتون داکر بهتره یا پادمن؟",
    "بچه‌ها کسی با گیت لب به مشکل نخورده امروز؟",
]

QUALIFIED_LEAD_MESSAGES: List[str] = [
    "پروژه فوری: نیازمند برنامه‌نویس ارشد جنگو با بودجه ۳۰ میلیون تومان جهت طراحی اختصاصی",
    "همکاری پروژه‌ای: نیازمند برنامه‌نویس ری‌اکت و نکست با قرارداد شرکتی",
    "استخدام برنامه‌نویس پایتون با حقوق عالی و بودجه مشخص، سفارش کار فوری",
    "طراحی اختصاصی سایت فروشگاهی، پروژه‌ای با بودجه ۵۰ میلیون تومان",
    "توسعه‌دهنده فلاتر جهت همکاری پروژه‌ای و قرارداد رسمی",
]

# Quarantined spam channel test fixtures (min budget threshold = 20,000,000 Tomans)
QUARANTINE_REJECTED_MESSAGES: List[str] = [
    "سلام کار دانشجویی تایپ و ترجمه پذیرفته می‌شود.",
    "پروژه طراحی لوگو با بودجه ۵۰۰ هزار تومان",
    "نیازمند بنر سایت با بودجه ۲ میلیون تومان",
    "پروژه سئو با هزینه ۱۰,۰۰۰,۰۰۰ تومان",
]

QUARANTINE_ACCEPTED_MESSAGES: List[str] = [
    "پروژه طراحی فروشگاه اینترنتی اختصاصی با بودجه ۲۵,۰۰۰,۰۰۰ تومان",
    "سفارش ساخت سامانه اداری با مبلغ ۳۵,۰۰۰,۰۰۰ تومان نیازمند برنامه‌نویس",
    "قرارداد توسعه اپلیکیشن موبایل با هزینه ۵۰,۰۰۰,۰۰۰ تومان",
]

# Consolidated fixture dictionary
KEYWORD_FILTER_FIXTURES: Dict[str, Any] = {
    "tier_3_test_channel": TIER_3_TEST_CHANNEL,
    "chatter_msg": CHATTER_MSG,
    "lead_msg": LEAD_MSG,
    "chatter_messages": CHATTER_MESSAGES,
    "qualified_lead_messages": QUALIFIED_LEAD_MESSAGES,
    "quarantine_test_channel": QUARANTINE_TEST_CHANNEL,
    "quarantine_suppressed_messages": QUARANTINE_REJECTED_MESSAGES,
    "quarantine_exempted_messages": QUARANTINE_ACCEPTED_MESSAGES,
    "golden_tier_1_test_channel": GOLDEN_TIER_1_TEST_CHANNEL,
}


def get_keyword_filter_fixtures() -> Dict[str, Any]:
    """Returns a copy of keyword filter test fixtures."""
    return dict(KEYWORD_FILTER_FIXTURES)


def get_tier_3_test_pair() -> Tuple[str, str, str]:
    """Returns (test_channel, chatter_message, qualified_lead_message)."""
    return (
        TIER_3_TEST_CHANNEL,
        CHATTER_MSG,
        LEAD_MSG,
    )


def verify_fixtures_against_manager(manager: Any) -> bool:
    """Verifies that the provided ChannelSchedulingManager correctly evaluates fixtures."""
    # 1. Tier 3 Chatter suppression
    for chatter in CHATTER_MESSAGES:
        passed, _ = manager.should_process_event(TIER_3_TEST_CHANNEL, chatter)
        if passed:
            return False

    # 2. Tier 3 Lead admission
    for lead in QUALIFIED_LEAD_MESSAGES:
        passed, _ = manager.should_process_event(TIER_3_TEST_CHANNEL, lead)
        if not passed:
            return False

    # 3. Quarantine suppression (below 20M)
    for q_rej in QUARANTINE_REJECTED_MESSAGES:
        passed, _ = manager.should_process_event(QUARANTINE_TEST_CHANNEL, q_rej)
        if passed:
            return False

    # 4. Quarantine admission (>= 20M)
    for q_acc in QUARANTINE_ACCEPTED_MESSAGES:
        passed, _ = manager.should_process_event(QUARANTINE_TEST_CHANNEL, q_acc)
        if not passed:
            return False

    return True


if __name__ == "__main__":
    import sys
    print("Test fixtures loaded successfully:")
    print(f" - Chatter messages: {len(CHATTER_MESSAGES)}")
    print(f" - Qualified lead messages: {len(QUALIFIED_LEAD_MESSAGES)}")
    print(f" - Quarantine rejected messages: {len(QUARANTINE_REJECTED_MESSAGES)}")
    print(f" - Quarantine accepted messages: {len(QUARANTINE_ACCEPTED_MESSAGES)}")
    sys.exit(0)
