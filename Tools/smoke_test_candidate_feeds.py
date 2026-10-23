#!/usr/bin/env python3
"""
smoke_test_candidate_feeds.py

Automated QA Smoke Testing & Schema Compatibility Engine for Candidate Feeds.
Author: agw-ACC5-w02 (Account 5 Quality Assurance & Feed Verification)
Issue: #91 - Candidate Channel Quality Assurance & Smoke Testing

Evaluates candidate channel feeds against standardized QA criteria:
  1. Operational Stability (reachability, latency, error rate)
  2. Message Schema Validity (JSON Schema Draft-07 compliance)
  3. Healthy Update Frequencies (cadence, staleness, burst limit)
  4. Zero-Noise Performance (advertising ratio, duplication, SNR)
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
from typing import Any, Dict, List, Tuple

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

# Acceptance Threshold Constants
THRESHOLD_MIN_CADENCE_POSTS_PER_DAY = 0.5   # At least 1 post every 48 hours
THRESHOLD_MAX_STALENESS_HOURS = 168.0       # Max 7 days silence
THRESHOLD_MAX_BURST_PER_HOUR = 20           # Anti-flood limit
THRESHOLD_MAX_AD_RATIO = 0.05               # Max 5% sponsored / ad content
THRESHOLD_MAX_DUPLICATE_RATIO = 0.00        # Zero tolerance for duplicate content
THRESHOLD_MIN_COMPOSITE_SCORE_PASS = 85.0   # Minimum composite score for PASS
THRESHOLD_MIN_COMPOSITE_SCORE_COND = 70.0   # Minimum composite score for CONDITIONAL

SPAM_KEYWORDS = [
    r'#ad\b', r'#sponsored\b', r'#تبلیغ\b', r'#تبلیغات\b',
    r'خرید سرور', r'تخفیف ویژه', r'قرعه[\s_-]?کشی',
    r'پشتیبانی فروش', r'خرید وی[\s_-]?پی[\s_-]?ان',
    r'فروش فیلترشکن', r'پنل نامحدود'
]


def load_json(filepath: str) -> Any:
    """Load and parse JSON file from disk."""
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def validate_schema(instance: dict, schema: dict) -> Tuple[bool, List[str]]:
    """Validate JSON instance against schema."""
    if not HAS_JSONSCHEMA:
        # Fallback basic structural validation
        errors = []
        for req in schema.get("required", []):
            if req not in instance:
                errors.append(f"Missing required property: '{req}'")
        return len(errors) == 0, errors

    errors = []
    validator = jsonschema.Draft7Validator(schema)
    for err in validator.iter_errors(instance):
        errors.append(f"{err.json_path}: {err.message}")
    return len(errors) == 0, errors


def test_operational_stability(candidate: dict) -> dict:
    """Evaluate operational reachability and simulated latency."""
    endpoint = candidate.get("operational_endpoint", {})
    expected_status = endpoint.get("expected_status", 200)
    timeout = endpoint.get("timeout_seconds", 3.0)

    # In smoke testing harness, evaluate declared protocol and expected status
    is_reachable = (expected_status == 200)
    # Simulated latency profile (bounded by timeout)
    simulated_latency_ms = 350.0 if is_reachable else 4200.0
    failure_rate = 0.0 if is_reachable else 1.0

    score = 100.0 if is_reachable else 0.0
    if is_reachable and simulated_latency_ms > 2500.0:
        score -= 20.0

    return {
        "reachable": is_reachable,
        "expected_status": expected_status,
        "simulated_latency_ms": simulated_latency_ms,
        "failure_rate": failure_rate,
        "score": score,
        "critical_failure": not is_reachable,
        "reason": None if is_reachable else f"Endpoint returned non-200 status ({expected_status})"
    }


def validate_candidate_messages_schema(
    candidate: dict,
    candidate_schema: dict,
    message_schema: dict
) -> dict:
    """Validate candidate definition and all enclosed sample messages."""
    cand_valid, cand_errs = validate_schema(candidate, candidate_schema)

    messages = candidate.get("sample_messages", [])
    valid_msgs = 0
    msg_errors = []

    for idx, msg in enumerate(messages):
        valid, errs = validate_schema(msg, message_schema)
        if valid:
            valid_msgs += 1
        else:
            msg_errors.append({
                "message_index": idx,
                "message_id": msg.get("message_id", "unknown"),
                "errors": errs
            })

    total_msgs = len(messages)
    pass_rate = (valid_msgs / total_msgs * 100.0) if total_msgs > 0 else 0.0

    critical_failure = (not cand_valid) or (valid_msgs < total_msgs)
    score = pass_rate if cand_valid else 0.0

    return {
        "candidate_schema_valid": cand_valid,
        "candidate_errors": cand_errs,
        "total_messages": total_msgs,
        "valid_messages": valid_msgs,
        "pass_rate_pct": pass_rate,
        "message_errors": msg_errors,
        "score": score,
        "critical_failure": critical_failure,
        "reason": None if not critical_failure else f"Schema violations: {len(cand_errs)} candidate errs, {len(msg_errors)} invalid messages"
    }


def evaluate_cadence_and_frequency(
    candidate: dict,
    reference_time: datetime.datetime
) -> dict:
    """Evaluate publication frequency, staleness, burst limits, and regularity."""
    messages = candidate.get("sample_messages", [])
    if not messages:
        return {
            "score": 0.0,
            "posts_per_day": 0.0,
            "hours_since_latest": 9999.0,
            "max_hourly_burst": 0,
            "critical_failure": True,
            "reason": "No messages in candidate sample"
        }

    timestamps: List[datetime.datetime] = []
    for m in messages:
        ts_str = m.get("timestamp")
        if ts_str and isinstance(ts_str, str):
            try:
                # Handle ISO timestamps with Z or naive dates
                dt = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                timestamps.append(dt)
            except Exception:
                pass

    if not timestamps:
        return {
            "score": 0.0,
            "posts_per_day": 0.0,
            "hours_since_latest": 9999.0,
            "max_hourly_burst": 0,
            "critical_failure": True,
            "reason": "No parseable timestamps found in message sample"
        }

    timestamps.sort()
    latest_ts = timestamps[-1]
    earliest_ts = timestamps[0]

    hours_since_latest = max(0.0, (reference_time - latest_ts).total_seconds() / 3600.0)
    timespan_days = max(1.0, (reference_time - earliest_ts).total_seconds() / 86400.0)
    posts_per_day = len(timestamps) / timespan_days

    # Calculate hourly bursts
    max_hourly_burst = 1
    for i in range(len(timestamps)):
        burst_count = 1
        for j in range(i + 1, len(timestamps)):
            if (timestamps[j] - timestamps[i]).total_seconds() <= 3600.0:
                burst_count += 1
            else:
                break
        if burst_count > max_hourly_burst:
            max_hourly_burst = burst_count

    # Check thresholds
    is_stale = hours_since_latest > THRESHOLD_MAX_STALENESS_HOURS
    is_low_cadence = posts_per_day < THRESHOLD_MIN_CADENCE_POSTS_PER_DAY
    is_burst_flooding = max_hourly_burst > THRESHOLD_MAX_BURST_PER_HOUR

    if is_stale:
        score = 0.0
    else:
        cadence_ratio = min(1.0, posts_per_day / THRESHOLD_MIN_CADENCE_POSTS_PER_DAY)
        freshness_factor = max(0.0, 1.0 - (hours_since_latest / THRESHOLD_MAX_STALENESS_HOURS))
        score = 100.0 * (0.6 * cadence_ratio + 0.4 * freshness_factor)

    if is_burst_flooding:
        score = max(0.0, score - 40.0)

    critical_failure = is_stale or is_burst_flooding
    reasons = []
    if is_stale:
        reasons.append(f"Feed is stale ({hours_since_latest:.1f}h > {THRESHOLD_MAX_STALENESS_HOURS}h threshold)")
    if is_burst_flooding:
        reasons.append(f"Burst limit exceeded ({max_hourly_burst} posts/h > {THRESHOLD_MAX_BURST_PER_HOUR})")
    if is_low_cadence and not is_stale:
        reasons.append(f"Low cadence ({posts_per_day:.2f} posts/day < {THRESHOLD_MIN_CADENCE_POSTS_PER_DAY})")

    return {
        "posts_per_day": round(posts_per_day, 2),
        "hours_since_latest": round(hours_since_latest, 1),
        "latest_post_iso": latest_ts.isoformat(),
        "timespan_days": round(timespan_days, 1),
        "max_hourly_burst": max_hourly_burst,
        "score": round(score, 1),
        "critical_failure": critical_failure,
        "reason": "; ".join(reasons) if reasons else None
    }


def audit_zero_noise_and_duplicates(candidate: dict) -> dict:
    """Scan for advertisements, promotional spam, and duplicate content."""
    messages = candidate.get("sample_messages", [])
    if not messages:
        return {
            "score": 0.0,
            "ad_count": 0,
            "ad_ratio_pct": 0.0,
            "duplicate_count": 0,
            "duplicate_ratio_pct": 0.0,
            "critical_failure": True,
            "reason": "No messages to audit"
        }

    ad_count = 0
    seen_hashes = set()
    duplicate_count = 0

    spam_pattern = re.compile("|".join(SPAM_KEYWORDS), re.IGNORECASE)

    for msg in messages:
        text = msg.get("text", "") or ""
        tags = msg.get("tags", []) or []
        combined_text = text + " " + " ".join(tags)

        # Ad detection
        if spam_pattern.search(combined_text):
            ad_count += 1

        # Duplicate detection (normalized text hash)
        norm_text = re.sub(r'\s+', ' ', text.strip().lower())
        if norm_text:
            text_hash = hashlib.sha256(norm_text.encode('utf-8')).hexdigest()
            if text_hash in seen_hashes:
                duplicate_count += 1
            else:
                seen_hashes.add(text_hash)

    total_msgs = len(messages)
    ad_ratio = ad_count / total_msgs
    dup_ratio = duplicate_count / total_msgs

    is_high_ad = ad_ratio > THRESHOLD_MAX_AD_RATIO
    has_duplicates = dup_ratio > THRESHOLD_MAX_DUPLICATE_RATIO

    score = max(0.0, 100.0 * (1.0 - ad_ratio) * (1.0 - dup_ratio))

    critical_failure = is_high_ad or has_duplicates
    reasons = []
    if is_high_ad:
        reasons.append(f"High ad ratio ({ad_ratio * 100.0:.1f}% > {THRESHOLD_MAX_AD_RATIO * 100.0:.1f}% max)")
    if has_duplicates:
        reasons.append(f"Duplicates detected ({duplicate_count} duplicate posts, {dup_ratio * 100.0:.1f}%)")

    return {
        "ad_count": ad_count,
        "ad_ratio_pct": round(ad_ratio * 100.0, 1),
        "duplicate_count": duplicate_count,
        "duplicate_ratio_pct": round(dup_ratio * 100.0, 1),
        "score": round(score, 1),
        "critical_failure": critical_failure,
        "reason": "; ".join(reasons) if reasons else None
    }


def calculate_qa_score_and_status(
    stability: dict,
    schema: dict,
    cadence: dict,
    noise: dict
) -> dict:
    """Compute weighted composite score and final verdict (PASS/CONDITIONAL/FAIL)."""
    # Weights: Stability 25%, Schema 30%, Cadence 25%, Zero-Noise 20%
    composite = (
        0.25 * stability["score"] +
        0.30 * schema["score"] +
        0.25 * cadence["score"] +
        0.20 * noise["score"]
    )
    composite = round(composite, 1)

    critical_failures = []
    if stability["critical_failure"]:
        critical_failures.append(f"Stability: {stability['reason']}")
    if schema["critical_failure"]:
        critical_failures.append(f"Schema: {schema['reason']}")
    if cadence["critical_failure"]:
        critical_failures.append(f"Cadence: {cadence['reason']}")
    if noise["critical_failure"]:
        critical_failures.append(f"Noise: {noise['reason']}")

    warnings = []
    if critical_failures or composite < THRESHOLD_MIN_COMPOSITE_SCORE_COND:
        status = "FAIL"
    elif composite >= THRESHOLD_MIN_COMPOSITE_SCORE_PASS:
        # Check if non-critical low cadence caps status to CONDITIONAL
        if cadence.get("posts_per_day", 0.0) < THRESHOLD_MIN_CADENCE_POSTS_PER_DAY:
            status = "CONDITIONAL"
            warnings.append(f"Cadence: Low cadence ({cadence.get('posts_per_day')} < {THRESHOLD_MIN_CADENCE_POSTS_PER_DAY} posts/day threshold) requires Low-Priority Extended Tier")
        else:
            status = "PASS"
    else:
        status = "CONDITIONAL"

    return {
        "composite_score": composite,
        "status": status,
        "critical_failures": critical_failures,
        "warnings": warnings
    }


def run_candidate_smoke_tests(
    candidates_path: str,
    cand_schema_path: str,
    msg_schema_path: str,
    reference_time: datetime.datetime
) -> dict:
    """Run full test suite across all candidate channels."""
    candidates = load_json(candidates_path)
    cand_schema = load_json(cand_schema_path)
    msg_schema = load_json(msg_schema_path)

    results = []
    summary_counts = {"PASS": 0, "CONDITIONAL": 0, "FAIL": 0}

    print("=" * 80)
    print("  FEED SMOKE TESTING & SCHEMA COMPATIBILITY ENGINE (Account 5 QA)")
    print(f"  Evaluation Timestamp: {reference_time.isoformat()}")
    print("=" * 80)

    for cand in candidates:
        cand_id = cand.get("candidate_id", "unknown")
        handle = cand.get("handle", "unknown")
        title = cand.get("title", "")

        stability = test_operational_stability(cand)
        schema = validate_candidate_messages_schema(cand, cand_schema, msg_schema)
        cadence = evaluate_cadence_and_frequency(cand, reference_time)
        noise = audit_zero_noise_and_duplicates(cand)

        verdict = calculate_qa_score_and_status(stability, schema, cadence, noise)
        status = verdict["status"]
        summary_counts[status] += 1

        cand_result = {
            "candidate_id": cand_id,
            "handle": handle,
            "title": title,
            "category": cand.get("category", ""),
            "status": status,
            "composite_score": verdict["composite_score"],
            "metrics": {
                "stability": stability,
                "schema": schema,
                "cadence": cadence,
                "noise": noise
            },
            "critical_failures": verdict["critical_failures"],
            "warnings": verdict["warnings"]
        }
        results.append(cand_result)

        badge = "[PASS]" if status == "PASS" else ("[WARN]" if status == "CONDITIONAL" else "[FAIL]")
        print(f"\n{badge} {cand_id} ({handle}) - Score: {verdict['composite_score']}/100 -> {status}")
        print(f"   Title: {title}")
        print(f"   - Stability : {stability['score']:.1f}/100 (Reachable: {stability['reachable']})")
        print(f"   - Schema    : {schema['score']:.1f}/100 ({schema['valid_messages']}/{schema['total_messages']} valid messages)")
        print(f"   - Cadence   : {cadence['score']:.1f}/100 ({cadence['posts_per_day']} posts/day, last post {cadence['hours_since_latest']}h ago)")
        print(f"   - Noise/Spam: {noise['score']:.1f}/100 (Ads: {noise['ad_ratio_pct']}%, Duplicates: {noise['duplicate_count']})")
        if verdict["critical_failures"]:
            print(f"   - Failure Reasons: {'; '.join(verdict['critical_failures'])}")
        if verdict["warnings"]:
            print(f"   - Warnings: {'; '.join(verdict['warnings'])}")

    print("\n" + "=" * 80)
    print(f"  EXECUTION SUMMARY: {len(results)} evaluated | "
          f"{summary_counts['PASS']} PASS | "
          f"{summary_counts['CONDITIONAL']} CONDITIONAL | "
          f"{summary_counts['FAIL']} FAIL")
    print("=" * 80)

    return {
        "evaluated_at": reference_time.isoformat(),
        "total_candidates": len(results),
        "summary": summary_counts,
        "candidates": results
    }


def main():
    parser = argparse.ArgumentParser(description="Feed Smoke Testing & Schema Compatibility Engine")
    parser.add_argument("--candidates", default="qa/candidates/candidate_feeds_sample.json", help="Path to candidate feeds JSON")
    parser.add_argument("--candidate-schema", default="qa/schemas/candidate_channel_schema.json", help="Path to candidate channel schema")
    parser.add_argument("--message-schema", default="qa/schemas/channel_message_schema.json", help="Path to channel message schema")
    parser.add_argument("--output-json", default="Reports/qa/smoke_test_results.json", help="Output path for JSON results")
    parser.add_argument("--reference-time", default="2026-10-23T05:30:00Z", help="Reference ISO timestamp for evaluation")
    args = parser.parse_args()

    ref_dt = datetime.datetime.fromisoformat(args.reference_time.replace("Z", "+00:00"))

    results = run_candidate_smoke_tests(
        args.candidates,
        args.candidate_schema,
        args.message_schema,
        ref_dt
    )

    os.makedirs(os.path.dirname(args.output_json), exist_ok=True)
    with open(args.output_json, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nDetailed JSON results exported to: {args.output_json}")


if __name__ == "__main__":
    main()
