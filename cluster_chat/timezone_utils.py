"""cluster_chat/timezone_utils.py

Shared utilities for handling UTC storage and Asia/Tehran timezone formatting (P9).
Guarantees:
1. Internal/stored ISO timestamps remain in UTC with explicit 'Z' designator.
2. Subsecond precision (microseconds) is preserved when present.
3. User-facing display timestamps are converted and formatted in Asia/Tehran (UTC+03:30).
4. Naive ISO timestamps are treated safely as UTC instead of local system time.
5. Strict validation avoids silent timestamp corruption, boolean type confusion, and greedy numeric parsing.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Any
from zoneinfo import ZoneInfo

TEHRAN_TZ = ZoneInfo("Asia/Tehran")


def utc_now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Return current UTC ISO-8601 timestamp string ending with 'Z'."""
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_utc_iso_string(dt: datetime) -> str:
    """Format a timezone-aware datetime to UTC ISO-8601 string ending with 'Z', preserving microseconds if non-zero."""
    dt_utc = dt.astimezone(timezone.utc) if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    if dt_utc.microsecond:
        # e.g. 2026-10-28T10:00:00.123456Z
        return dt_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc_datetime(ts: Any) -> datetime | None:
    """Parse a datetime, float/int epoch timestamp, or ISO string into a timezone-aware UTC datetime.

    If the input is naive (lacks timezone information), it is treated as UTC.
    Returns None if ts is None, boolean, or invalid.
    """
    if ts is None or isinstance(ts, bool):
        return None

    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)

    if isinstance(ts, (int, float)):
        if not math.isfinite(ts):
            return None
        val = float(ts)
        try:
            if val > 1e11:  # milliseconds epoch
                val /= 1000.0
            return datetime.fromtimestamp(val, tz=timezone.utc)
        except (OverflowError, ValueError, OSError):
            return None

    if not isinstance(ts, str):
        return None

    s = ts.strip()
    if not s:
        return None

    # Disallow non-finite float strings like 'inf', '-inf', 'nan'
    if s.lower() in ("inf", "-inf", "+inf", "nan"):
        return None

    # Prioritize standard ISO date strings (starts with YYYY-MM-DD)
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        clean_s = s.replace(" ", "T")
        # Replace trailing Z or z with +00:00 for fromisoformat compatibility
        if clean_s.endswith(("Z", "z")):
            clean_s = clean_s[:-1] + "+00:00"

        try:
            dt = datetime.fromisoformat(clean_s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (ValueError, TypeError):
            pass

    # Epoch string: only accept numeric strings of 10 digits (seconds) or 13 digits (milliseconds)
    if re.match(r"^\d{10}(?:\.\d+)?$", s) or re.match(r"^\d{13}$", s):
        try:
            val = float(s)
            if not math.isfinite(val):
                return None
            if val > 1e11:
                val /= 1000.0
            return datetime.fromtimestamp(val, tz=timezone.utc)
        except (OverflowError, ValueError, OSError):
            return None

    return None


def to_tehran_datetime(ts: Any) -> datetime | None:
    """Convert input timestamp to Asia/Tehran timezone-aware datetime."""
    dt_utc = parse_utc_datetime(ts)
    if dt_utc is None:
        return None
    return dt_utc.astimezone(TEHRAN_TZ)


def ensure_utc_iso(ts: Any) -> str:
    """Normalize any datetime, timestamp, or ISO string to a UTC ISO-8601 string ending with 'Z'.

    - If input is None or empty string, returns current UTC time ISO string.
    - Preserves subsecond precision (microseconds) if present.
    - Raises ValueError if a non-empty timestamp is provided but invalid (prevents silent data corruption).
    """
    if ts is None or (isinstance(ts, str) and not ts.strip()):
        return utc_now_iso()

    dt = parse_utc_datetime(ts)
    if dt is None:
        raise ValueError(f"Invalid timestamp cannot be converted to UTC ISO: {ts!r}")

    return _format_utc_iso_string(dt)


def format_tehran_time(ts: Any) -> str:
    """Convert UTC timestamp to Asia/Tehran 24-hour time string 'HH:MM'.

    Example: '2026-10-28T10:00:00Z' -> '13:30'
    Returns empty string for None/empty, or str(ts) if unparseable.
    """
    if ts is None or ts == "":
        return ""
    dt = to_tehran_datetime(ts)
    if dt is None:
        return str(ts)
    return dt.strftime("%H:%M")


def format_tehran_display(
    ts: Any,
    include_date: bool = False,
    include_seconds: bool = False,
) -> str:
    """Convert UTC timestamp to Asia/Tehran display format.

    - include_date=False, include_seconds=False: 'HH:MM'
    - include_date=False, include_seconds=True:  'HH:MM:SS'
    - include_date=True,  include_seconds=False: 'YYYY-MM-DD HH:MM'
    - include_date=True,  include_seconds=True:  'YYYY-MM-DD HH:MM:SS'
    """
    if ts is None or ts == "":
        return ""
    dt = to_tehran_datetime(ts)
    if dt is None:
        return str(ts)

    if include_date:
        if include_seconds:
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        return dt.strftime("%Y-%m-%d %H:%M")

    if include_seconds:
        return dt.strftime("%H:%M:%S")
    return dt.strftime("%H:%M")


def format_tehran_datetime(ts: Any, include_seconds: bool = True) -> str:
    """Convert UTC timestamp to Asia/Tehran 'YYYY-MM-DD HH:MM:SS' string."""
    return format_tehran_display(ts, include_date=True, include_seconds=include_seconds)
