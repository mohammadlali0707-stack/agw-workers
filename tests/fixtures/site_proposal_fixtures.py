#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test fixtures and verification helpers for site-proposal dispatch prerequisites (SDD P15).

Ensures all jobs dispatched for site-proposal.yml in mohammadlali0707-stack/hq-ai-results
include a valid jobs/<job_id>/proposal.json payload before initiating Playwright automation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.site_proposal.runner import validate_proposal_payload

# Valid standard proposal payload containing all recommended and mandatory fields
VALID_PROPOSAL_PAYLOAD = {
    "job_id": "sp-job-sample-001",
    "lead_id": "notion-lead-9999",
    "site": "ponisha",
    "project_url": "https://ponisha.ir/project/sample-project-101",
    "proposal_text": "پیشنهاد حرفه‌ای جهت اجرای پروژه طبق الزامات فنی اعلام شده.",
    "bid_amount": 5000000,
    "currency": "TOMAN",
    "delivery_days": 5,
    "milestone_title": "تحویل کامل فاز اول و تست",
    "created_at": "2026-11-15T12:00:00Z",
}

# Minimal valid proposal payload containing bare essential fields
MINIMAL_PROPOSAL_PAYLOAD = {
    "proposal_text": "پیشنهاد مختصر پروژه با حداقل فیلدهای اجباری",
}

# Valid explore mode payload (does not submit, inspects form locators)
EXPLORE_PROPOSAL_PAYLOAD = {
    "_explore": True,
    "_wait_ms": 2000,
    "_click": [],
}

# Negative test fixtures for testing prerequisite enforcement
INVALID_PAYLOAD_NON_OBJECT = ["not", "a", "json", "object"]

INVALID_PAYLOAD_MISSING_TEXT = {
    "job_id": "sp-missing-text",
    "bid_amount": 5000000,
    "delivery_days": 3,
}

INVALID_PAYLOAD_EMPTY_TEXT = {
    "job_id": "sp-empty-text",
    "proposal_text": "   ",
    "bid_amount": 5000000,
}

INVALID_PAYLOAD_NEGATIVE_BID = {
    "job_id": "sp-negative-bid",
    "proposal_text": "متن پیشنهاد",
    "bid_amount": -1000,
}

INVALID_PAYLOAD_INVALID_DAYS = {
    "job_id": "sp-invalid-days",
    "proposal_text": "متن پیشنهاد",
    "delivery_days": 0,
}


def verify_dispatched_job_payload(proposal_data: Any) -> tuple[bool, str | None]:
    """Verify that a proposal payload dictionary satisfies all dispatch prerequisites.

    Args:
        proposal_data: Parsed JSON object from proposal.json

    Returns:
        tuple[bool, str | None]: (True, None) if valid, or (False, error_message)
    """
    return validate_proposal_payload(proposal_data)


def verify_job_dispatch_prerequisites(job_path_or_dir: Path | str) -> tuple[bool, str | None]:
    """Verify that a job directory contains a valid proposal.json payload before dispatch.

    Args:
        job_path_or_dir: Path to jobs/<job_id> directory or direct path to proposal.json

    Returns:
        tuple[bool, str | None]: (True, None) if valid, or (False, error_message)
    """
    p = Path(job_path_or_dir)
    if p.is_dir():
        proposal_file = p / "proposal.json"
    else:
        proposal_file = p

    if not proposal_file.exists():
        return False, f"proposal.json not found at {proposal_file}"

    try:
        with open(proposal_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return False, f"Failed to parse proposal.json: {type(e).__name__}"

    return verify_dispatched_job_payload(data)
