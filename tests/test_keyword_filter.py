#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_keyword_filter.py.

Unit tests for Telegram Lead Listener keyword gating, deprioritization rules,
and quarantine budget filters using external test fixtures.
"""

import os
import sys
import unittest

# Ensure root is in sys.path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from acquisition.telegram_lead_listener import ChannelSchedulingManager
from acquisition.test_fixtures import (
    CHATTER_MSG,
    LEAD_MSG,
    CHATTER_MESSAGES,
    QUALIFIED_LEAD_MESSAGES,
    QUARANTINE_REJECTED_MESSAGES,
    QUARANTINE_ACCEPTED_MESSAGES,
    TIER_3_TEST_CHANNEL,
    QUARANTINE_TEST_CHANNEL,
    GOLDEN_TIER_1_TEST_CHANNEL,
    verify_fixtures_against_manager,
)


class TestKeywordFilterAndDeprioritization(unittest.TestCase):
    """Test suite for keyword gating and channel tier filtering."""

    @classmethod
    def setUpClass(cls):
        cls.mgr = ChannelSchedulingManager()

    def test_tier_3_chatter_suppressed(self):
        for msg in CHATTER_MESSAGES:
            passed, reason = self.mgr.should_process_event(TIER_3_TEST_CHANNEL, msg)
            self.assertFalse(passed, f"Expected chatter to be blocked, but passed: {msg} (reason: {reason})")

    def test_tier_3_qualified_leads_passed(self):
        for msg in QUALIFIED_LEAD_MESSAGES:
            passed, reason = self.mgr.should_process_event(TIER_3_TEST_CHANNEL, msg)
            self.assertTrue(passed, f"Expected lead to pass, but failed: {msg} (reason: {reason})")

    def test_quarantine_budget_filtering(self):
        for msg in QUARANTINE_REJECTED_MESSAGES:
            passed, reason = self.mgr.should_process_event(QUARANTINE_TEST_CHANNEL, msg)
            self.assertFalse(passed, f"Expected quarantine rejection, but passed: {msg}")

        for msg in QUARANTINE_ACCEPTED_MESSAGES:
            passed, reason = self.mgr.should_process_event(QUARANTINE_TEST_CHANNEL, msg)
            self.assertTrue(passed, f"Expected quarantine acceptance, but failed: {msg}")

    def test_fixture_verification_helper(self):
        self.assertTrue(verify_fixtures_against_manager(self.mgr))

    def test_primary_chatter_and_lead_variables(self):
        passed_chatter, _ = self.mgr.should_process_event(TIER_3_TEST_CHANNEL, CHATTER_MSG)
        self.assertFalse(passed_chatter)
        passed_lead, _ = self.mgr.should_process_event(TIER_3_TEST_CHANNEL, LEAD_MSG)
        self.assertTrue(passed_lead)


if __name__ == "__main__":
    unittest.main()
