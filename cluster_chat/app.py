"""cluster_chat/app.py

Cluster Chat application module providing Asia/Tehran timezone formatting and UTC ISO helpers (P9).
Used by Cluster Chat services to ensure all stored timestamps remain in UTC with explicit 'Z',
while server-side rendered and display timestamps use Asia/Tehran (UTC+03:30, 24-hour).
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from cluster_chat.timezone_utils import (
    TEHRAN_TZ,
    ensure_utc_iso,
    format_tehran_datetime,
    format_tehran_display,
    format_tehran_time,
    parse_utc_datetime,
    to_tehran_datetime,
    utc_now,
    utc_now_iso,
)

__all__ = [
    "TEHRAN_TZ",
    "utc_now",
    "utc_now_iso",
    "parse_utc_datetime",
    "to_tehran_datetime",
    "ensure_utc_iso",
    "format_tehran_time",
    "format_tehran_display",
    "format_tehran_datetime",
]
