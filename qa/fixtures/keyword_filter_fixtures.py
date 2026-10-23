#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Independent Test Fixtures for Channel Keyword Filtering and Gating Verification.

Provides structured test fixtures to validate employer keyword filtering,
community chatter blocking, quarantine budget thresholds, and dormant channel
suppression across priority tiers.

Author: agw-ACC2-w03
Issue: #91
Plan: 3d88bb5ac734
"""

from typing import Any, Dict, List

# ------------------------------------------------------------------------------
# 1. Tier 3 Deprioritized Supergroup Fixtures (Employer Keywords vs Chatter)
# ------------------------------------------------------------------------------
TIER_3_KEYWORD_FIXTURES: List[Dict[str, Any]] = [
    # Qualified leads (Must PASS Tier 3 gate)
    {
        "id": "tier3_lead_django_project",
        "channel": "@Group_PyDev",
        "tier": "tier_3",
        "message": "پروژه فوری: نیازمند برنامه‌نویس ارشد جنگو با بودجه ۳۰ میلیون تومان جهت طراحی اختصاصی",
        "expected_pass": True,
        "category": "qualified_lead",
        "matched_keywords": ["پروژه", "نیازمند برنامه‌نویس", "بودجه", "طراحی اختصاصی"],
        "description": "Tier 3 qualified lead with multiple required employer keywords.",
    },
    {
        "id": "tier3_lead_react_recruitment",
        "channel": "@js_group",
        "tier": "tier_3",
        "message": "استخدام برنامه‌نویس React و Next.js به صورت همکاری پروژه‌ای با شرایط عالی",
        "expected_pass": True,
        "category": "qualified_lead",
        "matched_keywords": ["استخدام برنامه‌نویس", "همکاری پروژه‌ای"],
        "description": "Tier 3 recruitment lead matching hiring keyword.",
    },
    {
        "id": "tier3_lead_ai_contract",
        "channel": "@ai_automation_ir",
        "tier": "tier_3",
        "message": "قرارداد توسعه‌دهنده پایتون جهت ساخت دستیار هوشمند و پایپ‌لاین اتوماسیون داده",
        "expected_pass": True,
        "category": "qualified_lead",
        "matched_keywords": ["قرارداد", "توسعه‌دهنده"],
        "description": "Tier 3 automation contract lead matching contract and developer keywords.",
    },
    {
        "id": "tier3_lead_php_order",
        "channel": "@phpgp",
        "tier": "tier_3",
        "message": "سفارش کار طراحی سیستم انبارداری و اتصال به درگاه پرداخت با فریمورک لاراول",
        "expected_pass": True,
        "category": "qualified_lead",
        "matched_keywords": ["سفارش کار"],
        "description": "Tier 3 work order lead matching order keyword.",
    },
    {
        "id": "tier3_lead_dm_seo_project",
        "channel": "@dm_jobs",
        "tier": "tier_3",
        "message": "پروژه سئو و بهینه‌سازی فروشگاه آنلاین با بودجه توافقی",
        "expected_pass": True,
        "category": "qualified_lead",
        "matched_keywords": ["پروژه", "بودجه"],
        "description": "Tier 3 marketing project lead with budget.",
    },
    {
        "id": "tier3_lead_flutter_dev_needed",
        "channel": "@flutter_iranian",
        "tier": "tier_3",
        "message": "نیازمند برنامه‌نویس فلاتر جهت توسعه اپلیکیشن فروشگاهی iOS و اندروید",
        "expected_pass": True,
        "category": "qualified_lead",
        "matched_keywords": ["نیازمند برنامه‌نویس"],
        "description": "Tier 3 mobile app dev recruitment lead.",
    },
    {
        "id": "tier3_lead_uiux_custom_design",
        "channel": "@uiux_designers_ir",
        "tier": "tier_3",
        "message": "طراحی اختصاصی رابط کاربری داشبورد مدیریتی با فیگما",
        "expected_pass": True,
        "category": "qualified_lead",
        "matched_keywords": ["طراحی اختصاصی"],
        "description": "Tier 3 custom design specification lead.",
    },
    {
        "id": "tier3_lead_startup_partnership",
        "channel": "@ir_startups",
        "tier": "tier_3",
        "message": "همکاری پروژه‌ای با توسعه‌دهنده بک‌اند برای MVP استارتاپ سلامت",
        "expected_pass": True,
        "category": "qualified_lead",
        "matched_keywords": ["همکاری پروژه‌ای", "توسعه‌دهنده"],
        "description": "Tier 3 startup project partnership lead.",
    },

    # Community Chatter / Noise (Must FAIL Tier 3 gate)
    {
        "id": "tier3_chatter_python_list_reverse",
        "channel": "@Group_PyDev",
        "tier": "tier_3",
        "message": "سلام دوستان چطوری میشه توی پایتون لیست رو معکوس کرد؟",
        "expected_pass": False,
        "category": "student_chatter",
        "matched_keywords": [],
        "description": "Tier 3 student chatter without commercial intent.",
    },
    {
        "id": "tier3_chatter_react_render_issue",
        "channel": "@js_group",
        "tier": "tier_3",
        "message": "کسی می‌دونه چرا هوک useEffect دو بار رندر میشه توی حالت strict mode؟",
        "expected_pass": False,
        "category": "student_chatter",
        "matched_keywords": [],
        "description": "Tier 3 React debugging inquiry.",
    },
    {
        "id": "tier3_chatter_dm_tool_inquiry",
        "channel": "@dm_jobs",
        "tier": "tier_3",
        "message": "بهترین ابزار برای آنالیز ترافیک سایت به نظرتون چیه؟",
        "expected_pass": False,
        "category": "student_chatter",
        "matched_keywords": [],
        "description": "Tier 3 general discussion inquiry.",
    },
    {
        "id": "tier3_chatter_startup_webinar_link",
        "channel": "@ir_startups",
        "tier": "tier_3",
        "message": "صبح همگی بخیر، لینک ثبت‌نام وبینار سرمایه‌گذاری رو دارید؟",
        "expected_pass": False,
        "category": "student_chatter",
        "matched_keywords": [],
        "description": "Tier 3 social banter and webinar inquiry.",
    },
    {
        "id": "tier3_chatter_ai_model_discussion",
        "channel": "@ai_automation_ir",
        "tier": "tier_3",
        "message": "مدل جدید Ollama رو تست کردید؟ سرعتش خیلی خوبه.",
        "expected_pass": False,
        "category": "student_chatter",
        "matched_keywords": [],
        "description": "Tier 3 tech community discussion.",
    },
    {
        "id": "tier3_chatter_php_security_debate",
        "channel": "@phpgp",
        "tier": "tier_3",
        "message": "تفاوت PDO با MySQLi چیه؟ کدومش امن‌تره؟",
        "expected_pass": False,
        "category": "student_chatter",
        "matched_keywords": [],
        "description": "Tier 3 programming debate without employer context.",
    },
    {
        "id": "tier3_chatter_flutter_scrollview_bug",
        "channel": "@flutter_iranian",
        "tier": "tier_3",
        "message": "ویجت CustomScrollView ارور رندرینگ میده، کسی راهکاری داره؟",
        "expected_pass": False,
        "category": "student_chatter",
        "matched_keywords": [],
        "description": "Tier 3 technical troubleshooting question.",
    },
    {
        "id": "tier3_chatter_uiux_palette_feedback",
        "channel": "@uiux_designers_ir",
        "tier": "tier_3",
        "message": "نظرتون درباره پالت رنگی این لندینگ چیه؟ لینک در کامنت‌ها.",
        "expected_pass": False,
        "category": "student_chatter",
        "matched_keywords": [],
        "description": "Tier 3 portfolio feedback inquiry.",
    },
]

# ------------------------------------------------------------------------------
# 2. Quarantine Fixtures (Budget Threshold >= 20,000,000 Toman)
# ------------------------------------------------------------------------------
QUARANTINE_FIXTURES: List[Dict[str, Any]] = [
    {
        "id": "quarantine_pass_high_budget_fa_digits",
        "channel": "@job_freelancer",
        "tier": "quarantine",
        "message": "پروژه فروشگاه آنلاین، بودجه: ۲۵,۰۰۰,۰۰۰ تومان همراه با درگاه پرداخت",
        "expected_pass": True,
        "category": "quarantine_exemption",
        "matched_keywords": ["بودجه"],
        "description": "Quarantine message with budget >= 20M Toman (Persian digits).",
    },
    {
        "id": "quarantine_pass_high_budget_en_digits",
        "channel": "@freelancer_job",
        "tier": "quarantine",
        "message": "طراحی پلتفرم اتوماسیون سازمانی، مبلغ: 35000000 تومان تسویه مرحله‌ای",
        "expected_pass": True,
        "category": "quarantine_exemption",
        "matched_keywords": ["مبلغ"],
        "description": "Quarantine message with budget >= 20M Toman (English digits).",
    },
    {
        "id": "quarantine_fail_low_budget_fa_digits",
        "channel": "@job_freelancer",
        "tier": "quarantine",
        "message": "تایپ و ترجمه متون دانشجویی، هزینه: ۵۰۰,۰۰۰ تومان",
        "expected_pass": False,
        "category": "quarantine_suppression",
        "matched_keywords": ["هزینه"],
        "description": "Quarantine message with budget below 20M Toman threshold.",
    },
    {
        "id": "quarantine_fail_low_budget_en_digits",
        "channel": "@freelancer_job",
        "tier": "quarantine",
        "message": "بنر تبلیغاتی اینستاگرام، بودجه: 1500000 تومان",
        "expected_pass": False,
        "category": "quarantine_suppression",
        "matched_keywords": ["بودجه"],
        "description": "Quarantine message with budget below 20M Toman threshold.",
    },
    {
        "id": "quarantine_fail_no_budget_stated",
        "channel": "@job_freelancer",
        "tier": "quarantine",
        "message": "نیازمند ادمین پاسخگویی تلگرام پاره وقت با حقوق توافقی",
        "expected_pass": False,
        "category": "quarantine_suppression",
        "matched_keywords": [],
        "description": "Quarantine message without explicit numerical budget statement.",
    },
]

# ------------------------------------------------------------------------------
# 3. Golden Tier 1 & Standard Tier 2 Fixtures (Direct Ingestion)
# ------------------------------------------------------------------------------
TIER_1_2_FIXTURES: List[Dict[str, Any]] = [
    {
        "id": "tier1_pass_karlancer",
        "channel": "@karlancer_projects",
        "tier": "tier_1",
        "message": "پروژه طراحی مجدد وب‌سایت شرکتی با ری‌اکت و تیلویند",
        "expected_pass": True,
        "category": "golden_tier_lead",
        "matched_keywords": ["پروژه"],
        "description": "Tier 1 golden commercial feed directly admitted.",
    },
    {
        "id": "tier1_pass_parscoders",
        "channel": "@ParsCodersCom",
        "tier": "tier_1",
        "message": "پیاده‌سازی الگوریتم یادگیری ماشین در پایتون",
        "expected_pass": True,
        "category": "golden_tier_lead",
        "matched_keywords": [],
        "description": "Tier 1 golden commercial feed directly admitted.",
    },
    {
        "id": "tier2_pass_laravel",
        "channel": "@laravelGroup",
        "tier": "tier_2",
        "message": "فرصت شغلی سنیور لاراول و معماری مایکروسرویس",
        "expected_pass": True,
        "category": "standard_tier_lead",
        "matched_keywords": [],
        "description": "Tier 2 standard moderated source directly admitted.",
    },
]

# ------------------------------------------------------------------------------
# 4. Dormant / Dead Channel Fixtures (Unconditionally Blocked)
# ------------------------------------------------------------------------------
DORMANT_FIXTURES: List[Dict[str, Any]] = [
    {
        "id": "dormant_blocked_freelance_projects",
        "channel": "@freelance_projects_ir",
        "tier": "dormant",
        "message": "پروژه فوری با بودجه ۵۰ میلیون تومان نیازمند برنامه‌نویس ارشد",
        "expected_pass": False,
        "category": "dormant_purged",
        "matched_keywords": ["پروژه", "بودجه", "نیازمند برنامه‌نویس"],
        "description": "Dormant channel unconditionally suppressed regardless of lead keywords.",
    },
    {
        "id": "dormant_blocked_karyab_it",
        "channel": "@karyab_it",
        "tier": "dormant",
        "message": "استخدام برنامه‌نویس پایتون با حقوق عالی و قرارداد رسمی",
        "expected_pass": False,
        "category": "dormant_purged",
        "matched_keywords": ["استخدام برنامه‌نویس", "قرارداد"],
        "description": "Dormant channel unconditionally suppressed.",
    },
    {
        "id": "dormant_blocked_ecommerce_iranian",
        "channel": "@ecommerce_iranian",
        "tier": "dormant",
        "message": "نیازمند برنامه‌نویس جهت توسعه وب‌سایت فروشگاهی",
        "expected_pass": False,
        "category": "dormant_purged",
        "matched_keywords": ["نیازمند برنامه‌نویس"],
        "description": "Dormant private channel unconditionally suppressed.",
    },
    {
        "id": "dormant_blocked_xseo_web",
        "channel": "@xseo_web",
        "tier": "dormant",
        "message": "پروژه سئو و طراحی اختصاصی با بودجه بالا",
        "expected_pass": False,
        "category": "dormant_purged",
        "matched_keywords": ["پروژه", "طراحی اختصاصی", "بودجه"],
        "description": "Dormant channel unconditionally suppressed.",
    },
    {
        "id": "dormant_blocked_dev_jobs_iran",
        "channel": "@dev_jobs_iran",
        "tier": "dormant",
        "message": "همکاری پروژه‌ای برای توسعه‌دهنده فرانت‌اند",
        "expected_pass": False,
        "category": "dormant_purged",
        "matched_keywords": ["همکاری پروژه‌ای", "توسعه‌دهنده"],
        "description": "Dormant channel unconditionally suppressed.",
    },
]

# ------------------------------------------------------------------------------
# 5. Combined Comprehensive Fixture Catalog
# ------------------------------------------------------------------------------
KEYWORD_FILTER_FIXTURES: List[Dict[str, Any]] = (
    TIER_3_KEYWORD_FIXTURES +
    QUARANTINE_FIXTURES +
    TIER_1_2_FIXTURES +
    DORMANT_FIXTURES
)


def get_keyword_filter_fixtures() -> List[Dict[str, Any]]:
    """Returns the full list of keyword filter validation fixtures."""
    return list(KEYWORD_FILTER_FIXTURES)


def get_tier3_fixtures() -> List[Dict[str, Any]]:
    """Returns only Tier 3 keyword filter fixtures."""
    return list(TIER_3_KEYWORD_FIXTURES)


def get_quarantine_fixtures() -> List[Dict[str, Any]]:
    """Returns only Quarantine budget threshold fixtures."""
    return list(QUARANTINE_FIXTURES)


def validate_scheduling_manager(manager: Any) -> Dict[str, Any]:
    """Validates a ChannelSchedulingManager instance against all fixtures.

    Returns summary metrics dict with counts and any failures.
    """
    passed = 0
    failures: List[Dict[str, Any]] = []

    for fix in KEYWORD_FILTER_FIXTURES:
        ch = fix["channel"]
        msg = fix["message"]
        expected = fix["expected_pass"]
        actual_pass, reason = manager.should_process_event(ch, msg)

        if actual_pass == expected:
            passed += 1
        else:
            failures.append({
                "id": fix["id"],
                "channel": ch,
                "tier": fix["tier"],
                "expected": expected,
                "actual": actual_pass,
                "reason": reason,
                "message": msg,
            })

    return {
        "total": len(KEYWORD_FILTER_FIXTURES),
        "passed": passed,
        "failed": len(failures),
        "failures": failures,
        "status": "pass" if not failures else "fail",
    }
