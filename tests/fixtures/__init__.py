#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/fixtures package.

Exposes test fixtures for lead listener and keyword filtering.
"""

import os
import sys

# Ensure repository root is on sys.path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from acquisition.test_fixtures import (
    CHATTER_MSG,
    LEAD_MSG,
    SAMPLE_CHATTER_MSG,
    SAMPLE_LEAD_MSG,
    TIER_3_TEST_CHANNEL,
    QUARANTINE_TEST_CHANNEL,
    GOLDEN_TIER_1_TEST_CHANNEL,
    CHATTER_MESSAGES,
    QUALIFIED_LEAD_MESSAGES,
    QUARANTINE_REJECTED_MESSAGES,
    QUARANTINE_ACCEPTED_MESSAGES,
    KEYWORD_FILTER_FIXTURES,
    get_keyword_filter_fixtures,
    get_tier_3_test_pair,
    verify_fixtures_against_manager,
)

__all__ = [
    "CHATTER_MSG",
    "LEAD_MSG",
    "SAMPLE_CHATTER_MSG",
    "SAMPLE_LEAD_MSG",
    "TIER_3_TEST_CHANNEL",
    "QUARANTINE_TEST_CHANNEL",
    "GOLDEN_TIER_1_TEST_CHANNEL",
    "CHATTER_MESSAGES",
    "QUALIFIED_LEAD_MESSAGES",
    "QUARANTINE_REJECTED_MESSAGES",
    "QUARANTINE_ACCEPTED_MESSAGES",
    "KEYWORD_FILTER_FIXTURES",
    "get_keyword_filter_fixtures",
    "get_tier_3_test_pair",
    "verify_fixtures_against_manager",
]
