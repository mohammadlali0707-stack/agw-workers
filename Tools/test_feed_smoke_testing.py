#!/usr/bin/env python3
"""
test_feed_smoke_testing.py

Unit test verification for candidate feed smoke testing suite.
Author: agw-ACC5-w02
"""

import datetime
import unittest
from Tools.smoke_test_candidate_feeds import (
    validate_schema,
    test_operational_stability,
    evaluate_cadence_and_frequency,
    audit_zero_noise_and_duplicates,
    calculate_qa_score_and_status,
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


if __name__ == "__main__":
    unittest.main()
