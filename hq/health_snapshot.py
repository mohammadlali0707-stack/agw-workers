"""Read-only, local HQ health snapshot. No HTTP server and no credentials.

This is a foundation, not a deployed dashboard or a reachability guarantee.
Only process state, disk and memory are checked; model and agent health are
explicitly unknown until a separately authenticated adapter supplies them.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SERVICES = ("hq-service", "cluster-chat", "gemini-proxy", "arvan-flash-bot",
            "bale-flash-bot", "nginx", "docker", "lead-sources.timer")
STATUSES = {"healthy", "degraded", "unknown", "stale"}


def utc_now():
    return datetime.now(timezone.utc)


def stamp(dt):
    if dt.tzinfo is None:
        raise ValueError("Timezone required")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def observation(name, status, source, checked_at, detail=""):
    if status not in STATUSES:
        raise ValueError("invalid status")
    return {"name": name, "status": status, "source": source,
            "checked_at": stamp(checked_at), "detail": detail}


def probe_service(name, now, runner=subprocess.run):
    # Separate arguments, no shell. No command output or exception details leak.
    try:
        r = runner(["systemctl", "is-active", name], capture_output=True,
                   text=True, timeout=2, check=False)
        state = r.stdout.strip()
        if state == "active" and r.returncode == 0:
            status, detail = "healthy", "فرایند فعال است؛ عملکرد انتهابه‌انتها بررسی نشده"
        elif state in ("inactive", "failed", "deactivating"):
            status, detail = "degraded", "سرویس فعال نیست"
        else:
            status, detail = "unknown", "وضعیت سرویس قابل تأیید نیست"
    except (OSError, subprocess.TimeoutExpired):
        status, detail = "unknown", "بررسی سرویس ناموفق یا زمان‌بر بود"
    return observation(name, status, "systemd", now, detail)


def probe_disk(now, disk_usage=shutil.disk_usage):
    try:
        d = disk_usage("/")
        if d.total <= 0:
            raise ValueError("invalid total")
        used = round(100 * d.used / d.total, 1)
        return observation("disk", "degraded" if used >= 90 else "healthy",
                           "filesystem", now, f"فضای استفاده‌شده: {used}%")
    except (OSError, ValueError):
        return observation("disk", "unknown", "filesystem", now, "اطلاعات دیسک در دسترس نیست")


def probe_memory(now, read_text=None):
    try:
        text = read_text() if read_text else Path("/proc/meminfo").read_text()
        values = {}
        for line in text.splitlines():
            key, _, rest = line.partition(":")
            if key in ("MemTotal", "MemAvailable"):
                values[key] = int(rest.split()[0])
        total, available = values["MemTotal"], values["MemAvailable"]
        if total <= 0 or not 0 <= available <= total:
            raise ValueError("invalid memory")
        percent = round(available * 100 / total, 1)
        return observation("memory", "degraded" if percent < 10 else "healthy",
                           "proc-meminfo", now, f"حافظه در دسترس: {percent}%")
    except (OSError, ValueError, KeyError, IndexError):
        return observation("memory", "unknown", "proc-meminfo", now, "اطلاعات حافظه در دسترس نیست")


def make_snapshot(items, now=None, stale_after_seconds=180):
    now = now or utc_now()
    if now.tzinfo is None or stale_after_seconds < 0:
        raise ValueError("timezone and non-negative freshness required")
    clean = []
    for item in items:
        if item.get("status") not in STATUSES:
            raise ValueError("invalid status")
        copy = dict(item)
        try:
            checked = datetime.fromisoformat(copy["checked_at"].replace("Z", "+00:00"))
            if checked.tzinfo is None:
                raise ValueError("timezone required")
            age = (now - checked).total_seconds()
            if age > stale_after_seconds or age < -60:
                copy["status"] = "stale" if age > stale_after_seconds else "unknown"
        except (KeyError, ValueError, TypeError, AttributeError):
            copy["status"] = "unknown"
        clean.append(copy)
    states = {x["status"] for x in clean}
    overall = ("degraded" if "degraded" in states else
               "unknown" if not clean or states & {"unknown", "stale"} else "healthy")
    return {"generated_at": stamp(now), "overall": overall, "checks": clean}


def collect(now=None):
    now = now or utc_now()
    items = [probe_service(name, now) for name in SERVICES]
    items += [probe_disk(now), probe_memory(now),
              observation("gemini-model", "unknown", "not-configured", now,
                          "پاسخ‌گویی مدل هنوز بررسی نمی‌شود"),
              observation("n8n-workflows", "unknown", "not-configured", now,
                          "اجرای گردش‌کارها هنوز بررسی نمی‌شود")]
    return make_snapshot(items, now)


def render_fa(snapshot):
    labels = {"healthy": "سالم", "degraded": "نیازمند توجه", "unknown": "نامشخص", "stale": "داده قدیمی"}
    lines = ["وضعیت HQ (فقط‌خواندنی)", f"جمع‌بندی: {labels[snapshot['overall']]}",
             f"زمان UTC: {snapshot['generated_at']}"]
    for item in snapshot["checks"]:
        lines.append(f"• {item['name']}: {labels[item['status']]} — {item['detail']}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(json.dumps(collect(), ensure_ascii=False, indent=2))
