#!/usr/bin/env python3
"""
test_feed_smoke_testing.py

Unit test verification for candidate feed smoke testing suite.
Author: agw-ACC5-w02
"""

import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Tools.smoke_test_candidate_feeds import (
    validate_schema,
    test_operational_stability,
    evaluate_cadence_and_frequency,
    audit_zero_noise_and_duplicates,
    calculate_qa_score_and_status,
    run_candidate_smoke_tests,
    run_fixture_smoke_tests,
    load_json,
    THRESHOLD_MAX_STALENESS_HOURS,
    THRESHOLD_MAX_AD_RATIO
)


class TestFeedSmokeEngine(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.cand_schema = load_json("qa/schemas/candidate_channel_schema.json")
        cls.msg_schema = load_json("qa/schemas/channel_message_schema.json")
        cls.ref_time = datetime.datetime.fromisoformat("2026-10-23T05:30:00+00:00")

    def test_schema_valid_message(self):
        valid_msg = {
            "message_id": 999,
            "channel_id": "@valid_channel",
            "timestamp": "2026-10-22T10:00:00Z",
            "content_type": "text",
            "text": "This is a completely valid test message payload.",
            "views": 500,
            "is_forward": False,
            "language": "en"
        }
        valid, errors = validate_schema(valid_msg, self.msg_schema)
        self.assertTrue(valid, f"Expected valid message, got errors: {errors}")

    def test_schema_invalid_message_missing_required(self):
        invalid_msg = {
            "channel_id": "missing_at_and_missing_required_fields"
        }
        valid, errors = validate_schema(invalid_msg, self.msg_schema)
        self.assertFalse(valid, "Expected invalid message to fail schema validation")
        self.assertTrue(len(errors) > 0)

    def test_operational_stability_success_and_failure(self):
        cand_healthy = {
            "operational_endpoint": {
                "protocol": "https",
                "target": "https://t.me/s/valid",
                "expected_status": 200,
                "timeout_seconds": 3.0
            }
        }
        cand_broken = {
            "operational_endpoint": {
                "protocol": "https",
                "target": "https://t.me/s/broken",
                "expected_status": 503,
                "timeout_seconds": 3.0
            }
        }
        res_healthy = test_operational_stability(cand_healthy)
        self.assertTrue(res_healthy["reachable"])
        self.assertEqual(res_healthy["score"], 100.0)

        res_broken = test_operational_stability(cand_broken)
        self.assertFalse(res_broken["reachable"])
        self.assertEqual(res_broken["score"], 0.0)
        self.assertTrue(res_broken["critical_failure"])

    def test_cadence_stale_detection(self):
        stale_cand = {
            "sample_messages": [
                {
                    "message_id": 1,
                    "timestamp": "2026-08-01T00:00:00Z"
                }
            ]
        }
        res = evaluate_cadence_and_frequency(stale_cand, self.ref_time)
        self.assertTrue(res["critical_failure"])
        self.assertTrue(res["hours_since_latest"] > THRESHOLD_MAX_STALENESS_HOURS)
        self.assertEqual(res["score"], 0.0)

    def test_noise_and_duplicate_detection(self):
        spam_cand = {
            "sample_messages": [
                {"text": "خرید سرور اختصاصی و فروش فیلترشکن با تخفیف ویژه #ad"},
                {"text": "خرید سرور اختصاصی و فروش فیلترشکن با تخفیف ویژه #ad"},
                {"text": "مطلب عادی و بدون نویز پیرامون برنامه‌نویسی."}
            ]
        }
        res = audit_zero_noise_and_duplicates(spam_cand)
        self.assertTrue(res["critical_failure"])
        self.assertTrue(res["ad_count"] >= 2)
        self.assertTrue(res["duplicate_count"] >= 1)
        self.assertTrue(res["ad_ratio_pct"] > (THRESHOLD_MAX_AD_RATIO * 100))

    def test_scoring_and_status_gating(self):
        # Perfect metrics
        stab = {"score": 100.0, "critical_failure": False, "reason": None}
        sch = {"score": 100.0, "critical_failure": False, "reason": None}
        cad = {"score": 100.0, "critical_failure": False, "reason": None, "posts_per_day": 1.2}
        noise = {"score": 100.0, "critical_failure": False, "reason": None}
        v = calculate_qa_score_and_status(stab, sch, cad, noise)
        self.assertEqual(v["status"], "PASS")
        self.assertEqual(v["composite_score"], 100.0)

        # Critical failure drops directly to FAIL
        stab_fail = {"score": 0.0, "critical_failure": True, "reason": "Endpoint dead"}
        v_fail = calculate_qa_score_and_status(stab_fail, sch, cad, noise)
        self.assertEqual(v_fail["status"], "FAIL")

    def test_candidate_channels_schema_conformance(self):
        """Verify candidate channels in acquisition/sources.yaml and qa/candidates strictly conform to schema."""
        required_props = [
            "candidate_id", "handle", "title", "category",
            "primary_language", "feed_url", "poll_interval_minutes",
            "operational_endpoint", "sample_messages"
        ]
        sources = [
            ("acquisition/sources.yaml", load_json("acquisition/sources.yaml")),
            ("qa/candidates/candidate_feeds_operational.json", load_json("qa/candidates/candidate_feeds_operational.json")),
            ("qa/candidates/candidate_feeds_sample.json", load_json("qa/candidates/candidate_feeds_sample.json"))
        ]
        for src_name, candidates in sources:
            self.assertTrue(len(candidates) >= 4, f"Expected >= 4 candidates in {src_name}")
            for cand in candidates:
                for prop in required_props:
                    self.assertIn(prop, cand, f"Candidate {cand.get('candidate_id')} in {src_name} missing: {prop}")
                valid, errors = validate_schema(cand, self.cand_schema)
                self.assertTrue(valid, f"Candidate {cand.get('candidate_id')} in {src_name} failed schema: {errors}")

    def test_operational_candidate_feeds_segregation(self):
        """Verify operational candidate feeds contain only clean feeds and no negative fixtures."""
        cand_paths = [
            "qa/candidates/candidate_feeds_operational.json",
            "qa/candidates/candidate_feeds_sample.json"
        ]
        for cand_path in cand_paths:
            self.assertTrue(os.path.exists(cand_path), f"Missing {cand_path}")
            results = run_candidate_smoke_tests(
                cand_path,
                "qa/schemas/candidate_channel_schema.json",
                "qa/schemas/channel_message_schema.json",
                self.ref_time
            )
            self.assertEqual(results["total_candidates"], 4)
            self.assertEqual(results["summary"]["FAIL"], 0)
            self.assertEqual(results["summary"]["PASS"], 3)
            self.assertEqual(results["summary"]["CONDITIONAL"], 1)

            cand_ids = {c["candidate_id"] for c in results["candidates"]}
            fixture_ids = {"cand-dormant-legacy-04", "cand-malformed-bot-05", "cand-ad-spam-forwarder-06"}
            self.assertTrue(fixture_ids.isdisjoint(cand_ids), f"Fixture leaked into {cand_path}")

    def test_negative_test_fixtures_segregation(self):
        """Verify negative test fixtures isolate failing test fixtures with 100% rejection."""
        fixture_paths = [
            "qa/candidates/negative_test_fixtures.json",
            "qa/fixtures/negative_test_fixtures.json",
            "qa/candidates/test_fixtures/negative_test_feeds.json"
        ]
        for fixtures_path in fixture_paths:
            self.assertTrue(os.path.exists(fixtures_path), f"Missing {fixtures_path}")
            results = run_fixture_smoke_tests(
                fixtures_path,
                "qa/schemas/candidate_channel_schema.json",
                "qa/schemas/channel_message_schema.json",
                self.ref_time
            )
            self.assertEqual(results["total_fixtures"], 3)
            self.assertEqual(results["summary"]["FAIL"], 3)
            self.assertEqual(results["summary"]["PASS"], 0)
            self.assertEqual(results["summary"]["CONDITIONAL"], 0)
            self.assertTrue(results["all_fixtures_properly_rejected"])

            fixture_ids = {c["candidate_id"] for c in results["fixtures"]}
            expected_ids = {"cand-dormant-legacy-04", "cand-malformed-bot-05", "cand-ad-spam-forwarder-06"}
            self.assertEqual(fixture_ids, expected_ids)


if __name__ == "__main__":
    unittest.main()
