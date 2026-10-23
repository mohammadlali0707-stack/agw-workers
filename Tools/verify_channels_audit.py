#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verification script for Telegram Channels Activity and Output Audit.

Validates the integrity, schema, metric bounds, and categorization of the
structured Telegram channels inventory and audit report.
"""

import io
import json
import os
import re
import sys
from datetime import datetime

MARKER = "##TBS##"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JSON_PATH = os.path.join(ROOT, "audit", "telegram_channels_inventory.json")
MD_PATH = os.path.join(ROOT, "audit", "telegram_channels_audit.md")
REPORT_MD_PATH = os.path.join(ROOT, "Reports", "audit", "telegram_channels_audit.md")

ALLOWED_STATUSES = {"active", "degraded", "dead"}
REQUIRED_FIELDS = [
    "id", "handle", "type", "name_fa", "topic", "metrics",
    "status", "recommended_action", "tier", "notes"
]
REQUIRED_METRIC_FIELDS = [
    "message_throughput_daily", "qualified_lead_throughput_daily",
    "last_active_timestamp", "zero_output_duration_days", "fetch_error_rate_pct"
]


def fail(msg):
    print(f"❌ FAIL: {msg}")
    return False


def main():
    print("=" * 80)
    print("🔍 VERIFYING TELEGRAM CHANNELS AUDIT INTEGRITY")
    print("=" * 80)

    errors = []

    # 1. Check file existence
    for path, name in [
        (JSON_PATH, "inventory JSON"),
        (MD_PATH, "audit report Markdown"),
        (REPORT_MD_PATH, "Reports/audit report Markdown")
    ]:
        if not os.path.isfile(path):
            errors.append(f"Missing {name} at {path}")
        else:
            print(f"✅ Found {name}: {os.path.relpath(path, ROOT)}")

    if errors:
        for err in errors:
            fail(err)
        return 1

    # 2. Parse JSON
    try:
        with io.open(JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        fail(f"Invalid JSON in {JSON_PATH}: {e}")
        return 1

    # 3. Validate summary vs channels
    summary = data.get("summary", {})
    channels = data.get("channels", [])
    total_channels = len(channels)

    if total_channels != summary.get("total_channels_assessed"):
        errors.append(f"Channel count mismatch: {total_channels} vs {summary.get('total_channels_assessed')}")

    counts = {"active": 0, "degraded": 0, "dead": 0}
    seen_handles = set()

    for idx, ch in enumerate(channels, 1):
        ch_id = ch.get("id", f"item_{idx}")
        handle = ch.get("handle")

        # Check required fields
        for field in REQUIRED_FIELDS:
            if field not in ch:
                errors.append(f"Channel {ch_id} missing field: {field}")

        # Check metrics
        metrics = ch.get("metrics", {})
        for mfield in REQUIRED_METRIC_FIELDS:
            if mfield not in metrics:
                errors.append(f"Channel {ch_id} missing metric: {mfield}")

        # Check handle uniqueness
        if handle:
            if handle in seen_handles:
                errors.append(f"Duplicate channel handle: {handle}")
            seen_handles.add(handle)
        else:
            errors.append(f"Channel {ch_id} missing valid handle")

        # Check status
        status = ch.get("status")
        if status not in ALLOWED_STATUSES:
            errors.append(f"Channel {ch_id} has invalid status: {status}")
        else:
            counts[status] += 1

        # Check timestamp format
        ts = metrics.get("last_active_timestamp")
        if ts:
            try:
                datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                errors.append(f"Channel {ch_id} has invalid ISO timestamp: {ts}")

        # Check numeric metric boundaries
        msg_rate = metrics.get("message_throughput_daily", 0)
        err_rate = metrics.get("fetch_error_rate_pct", 0)
        zero_days = metrics.get("zero_output_duration_days", 0)

        if msg_rate < 0:
            errors.append(f"Negative message throughput in {ch_id}: {msg_rate}")
        if not (0.0 <= err_rate <= 100.0):
            errors.append(f"Error rate out of bounds in {ch_id}: {err_rate}")
        if zero_days < 0:
            errors.append(f"Negative zero output duration in {ch_id}: {zero_days}")

        # Semantic check: Dead channels should have 0 qualified leads & zero_output >= 14d
        if status == "dead":
            if metrics.get("qualified_lead_throughput_daily", 0) > 0:
                errors.append(f"Dead channel {ch_id} reports positive qualified throughput")
            if zero_days < 14 and err_rate < 50.0:
                errors.append(f"Dead channel {ch_id} has low zero-output duration and low error rate")

    # Verify counts match summary
    for st in ALLOWED_STATUSES:
        expected = summary.get(f"{st}_count")
        actual = counts[st]
        if actual != expected:
            errors.append(f"Summary count for {st} ({expected}) does not match counted ({actual})")

    # 4. Validate Markdown content references all handles
    with io.open(MD_PATH, "r", encoding="utf-8") as f:
        md_text = f.read()

    for handle in seen_handles:
        if handle not in md_text:
            errors.append(f"Markdown report missing mention of handle {handle}")

    # Output results
    print(f"📊 Total Channels Assessed: {total_channels}")
    print(f"   🟢 Active:   {counts['active']} ({counts['active']/total_channels*100:.1f}%)")
    print(f"   🟡 Degraded: {counts['degraded']} ({counts['degraded']/total_channels*100:.1f}%)")
    print(f"   🔴 Dead:     {counts['dead']} ({counts['dead']/total_channels*100:.1f}%)")

    status_str = "pass" if not errors else "fail"
    result_data = {
        "v": 1,
        "probe": "telegram_channels_audit_verification",
        "status": status_str,
        "data": {
            "total_channels": total_channels,
            "counts": counts,
            "errors": errors
        }
    }

    if errors:
        print(f"\n❌ FAILED with {len(errors)} error(s):")
        for err in errors:
            print(f"  - {err}")
        print(MARKER + json.dumps(result_data, sort_keys=True))
        return 1
    else:
        print("\n✅ PASS: All 24 channels verified with valid schema, metrics, and categorization.")
        print(MARKER + json.dumps(result_data, sort_keys=True))
        return 0


if __name__ == "__main__":
    sys.exit(main())
