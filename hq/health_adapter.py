"""Read-only presentation boundary for a trusted local health collector.

Not a network endpoint. A host application MUST authenticate the caller and
provide an authorization callback. Do not pass arbitrary external snapshots.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from threading import Lock
from time import monotonic

try:
    from . import health_snapshot as health
except ImportError:
    import health_snapshot as health

CHANNELS = frozenset(("telegram", "bale", "cluster"))
ALLOWED_NAMES = frozenset((*health.SERVICES, "disk", "memory", "gemini-model", "n8n-workflows"))
DENIED = "⛔ دسترسی به وضعیت HQ مجاز نیست."
UNAVAILABLE = "وضعیت HQ: نامشخص؛ دریافت وضعیت موقتاً ممکن نیست."
BUSY = "وضعیت HQ: نامشخص؛ بررسی دیگری در حال انجام است."
STALE_NOTE = "⚠️ دریافت جدید ناموفق بود؛ داده زیر آخرین نمونه موجود است و قدیمی محسوب می‌شود.\n"


def validate_snapshot(value):
    """Reject malformed/injected shape; collector remains a trusted boundary."""
    if not isinstance(value, dict) or not isinstance(value.get("generated_at"), str):
        raise ValueError("invalid snapshot")
    checks = value.get("checks")
    if not isinstance(checks, list) or len(checks) != len(ALLOWED_NAMES):
        raise ValueError("invalid checks")
    names = set()
    for item in checks:
        if not isinstance(item, dict) or item.get("name") not in ALLOWED_NAMES:
            raise ValueError("invalid name")
        if item["name"] in names or item.get("status") not in health.STATUSES:
            raise ValueError("invalid check")
        if not all(isinstance(item.get(k), str) and len(item[k]) <= 300
                   for k in ("checked_at", "source", "detail")):
            raise ValueError("invalid check fields")
        names.add(item["name"])
    generated = datetime.fromisoformat(value["generated_at"].replace("Z", "+00:00"))
    if generated.tzinfo is None:
        raise ValueError("generation timestamp requires a timezone")
    # Derive freshness and overall; retain the original collection time.
    result = health.make_snapshot(checks)
    result["generated_at"] = value["generated_at"]
    return result


class HealthPresenter:
    def __init__(self, authorize, collector=health.collect, ttl_seconds=30,
                 retry_seconds=5, clock=monotonic, lock_timeout=0.25):
        if not callable(authorize) or ttl_seconds <= 0 or retry_seconds <= 0 or lock_timeout < 0:
            raise ValueError("invalid configuration")
        self._authorize, self._collect, self._clock = authorize, collector, clock
        self._ttl, self._retry, self._lock_timeout = ttl_seconds, retry_seconds, lock_timeout
        self._lock = Lock()
        self._snapshot = None
        self._updated = 0.0
        self._retry_at = 0.0
        self._failed = False

    def render(self, principal, channel):
        # Callers do not supply authorization results; the host must derive and
        # verify principal from its authenticated transport, not message text.
        if not isinstance(channel, str) or channel not in CHANNELS:
            return DENIED
        try:
            if self._authorize(principal, channel) is not True:
                return DENIED
        except Exception:
            return DENIED
        if not self._lock.acquire(timeout=self._lock_timeout):
            return BUSY
        try:
            now = self._clock()
            if self._failed and now < self._retry_at:
                return self._fallback()
            if self._snapshot is None or now - self._updated >= self._ttl:
                try:
                    snapshot = validate_snapshot(self._collect())
                    self._snapshot = deepcopy(snapshot)
                    self._updated = self._clock()
                    self._failed = False
                except Exception:
                    self._failed = True
                    self._retry_at = self._clock() + self._retry
                    return self._fallback()
            current = health.make_snapshot(self._snapshot["checks"])
            current["generated_at"] = self._snapshot["generated_at"]
            return health.render_fa(current)
        finally:
            self._lock.release()

    def _fallback(self):
        if self._snapshot is None:
            return UNAVAILABLE
        checks = deepcopy(self._snapshot["checks"])
        for item in checks:
            item["status"] = "stale"
        stale = health.make_snapshot(checks)
        stale["generated_at"] = self._snapshot["generated_at"]
        return STALE_NOTE + health.render_fa(stale)
