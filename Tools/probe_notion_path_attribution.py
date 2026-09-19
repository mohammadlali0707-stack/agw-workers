#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# probe_notion_path_attribution -- measure which leg of Notion -> {n8n, poller} -> GitHub actually delivers, and how long a ticked task really waits.
"""Answer two questions about the Notion -> GitHub path with evidence, not memory.

THE QUESTION THE OWNER KEEPS ASKING
-----------------------------------
"Did the Notion -> n8n -> GitHub path get fixed?" Every answer so far has been
part measurement and part inference. This makes it a measurement.

Both legs write the SAME artefact -- a GitHub issue carrying
`<!-- notion-page-id: <uuid> -->` -- so the issue alone cannot say which one
made it. What CAN say is timing: `notion-task-poller.yml`'s run history is
public, and the poller creates the issue within its own run. So for each
issue carrying a real Notion UUID:

    a poller run started shortly before it  ->  attributable to the poller
    no poller run anywhere near it          ->  something else delivered it,
                                                and n8n is the only candidate

"Shortly before" is a window, and a window is an assumption. It is stated as a
constant below and reported in the digest so the reader can judge it, rather
than buried.

WHAT THIS PROBE CANNOT DO, SAID PLAINLY
----------------------------------------
It cannot see inside n8n. A finding of "no issue is attributable to n8n" means
n8n has not produced a GitHub issue from a real Notion page -- it does NOT
mean n8n is down, misconfigured, or absent. It also cannot distinguish "n8n
never fired" from "n8n fired and lost the race to the poller", though the
poller's own log lines (`created`, `skipped_no_project`) narrow that.

THE SECOND MEASUREMENT: WHAT THE SCHEDULE ACTUALLY DOES
--------------------------------------------------------
`cron: '*/10 * * * *'` claims a ten-minute poll. GitHub deprioritises
`schedule` under load, so the interval a task really waits is an empirical
question. This reports the real gaps between consecutive poller runs. A fleet
that believes it polls every ten minutes while it polls every four hours will
mis-diagnose every "why hasn't my task started" question it ever gets.

Read-only: lists workflow runs and issues. Creates nothing.

ASCII only. Exit 0 pass, 1 fail.
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request

MARKER = "##TBS##"
API = "https://api.github.com"
REPO = "mohammadlali0707-stack/agw-workers"

# A poller run that starts within this many seconds BEFORE an issue is the
# candidate that created it. The poller creates issues in its first seconds
# (measured: run 01:15:33 -> issue 01:15:43, a 10s gap), so this is generous
# by two orders of magnitude and still far below the real inter-run gap.
ATTRIBUTION_WINDOW_S = 600

# A real Notion page id, not a hand-made test string like `e2e-test-...`.
UUID_RE = re.compile(
    r"notion-page-id:\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12})", re.I)


def get(url, token):
    req = urllib.request.Request(url, method="GET")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/vnd.github+json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode()), r.status
    except urllib.error.HTTPError as e:
        return {}, e.code
    except urllib.error.URLError as e:
        return {}, "URLERROR:%s" % e.reason


def ts(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--window", type=int, default=ATTRIBUTION_WINDOW_S)
    a = ap.parse_args()
    token = os.environ.get("GH_TOKEN", "")

    failures, notes = [], []

    runs, code = get("%s/repos/%s/actions/workflows/notion-task-poller.yml"
                     "/runs?per_page=100" % (API, a.repo), token)
    if code != 200:
        print("::error::cannot list poller runs: HTTP %s" % code,
              file=sys.stderr)
        return 1
    starts = sorted(ts(r["created_at"]) for r in runs.get("workflow_runs", []))
    if not starts:
        failures.append("the poller has no run history at all, so nothing "
                        "about this path can be attributed")

    issues, code = get("%s/repos/%s/issues?state=all&per_page=100"
                       % (API, a.repo), token)
    if code != 200:
        print("::error::cannot list issues: HTTP %s" % code, file=sys.stderr)
        return 1

    by_poller, by_other, synthetic = [], [], []
    for i in issues:
        body = i.get("body") or ""
        if "notion-page-id" not in body:
            continue
        m = UUID_RE.search(body)
        if not m:
            # A marker that is not a real UUID: a hand-made test payload.
            synthetic.append(i["number"])
            continue
        made = ts(i["created_at"])
        near = [s for s in starts
                if 0 <= (made - s).total_seconds() <= a.window]
        (by_poller if near else by_other).append(
            {"issue": i["number"], "at": i["created_at"],
             "gap_s": int((made - near[-1]).total_seconds()) if near else None})

    # The real poll interval, which is the claim `cron: '*/10'` makes.
    gaps = [int((b - a_).total_seconds() / 60)
            for a_, b in zip(starts, starts[1:])]
    worst = max(gaps) if gaps else None
    median = sorted(gaps)[len(gaps) // 2] if gaps else None

    print("poller runs seen: %d" % len(starts))
    print("issues with a REAL Notion page id: %d poller-attributed, "
          "%d not attributable to a poller run" % (len(by_poller),
                                                   len(by_other)))
    print("issues with a synthetic/test marker: %d %s"
          % (len(synthetic), synthetic[:8]))
    if gaps:
        print("real gap between poller runs: median %d min, worst %d min "
              "(cron claims 10)" % (median, worst))

    # Findings. None of these is a pass/fail of the probe itself -- the probe
    # fails only if it could not measure. Judgement is left to the digest.
    if not by_poller and not by_other:
        notes.append("NO issue carries a real Notion page id: neither leg has "
                     "ever turned a real Notion page into a GitHub issue")
    if by_poller and not by_other:
        notes.append("every real-Notion issue lines up with a poller run: the "
                     "poller is delivering and n8n has produced none")
    if by_other:
        notes.append("%d issue(s) carry a real Notion id with no poller run "
                     "within %ds -- something other than the poller delivered "
                     "them: %s" % (len(by_other), a.window,
                                   [x["issue"] for x in by_other]))
    if median is not None and median > 30:
        notes.append("the schedule does NOT poll every 10 minutes: median gap "
                     "is %d minutes, worst %d. A ticked task waits hours."
                     % (median, worst))

    for n in notes:
        print("  * " + n)

    # The probe fails when it could not measure, never merely because the
    # answer is unwelcome -- "measured zero" and "failed to measure" are
    # different answers, and only the second is this probe's fault.
    ok = bool(starts) and not failures
    for f in failures:
        print("FAIL: " + f, file=sys.stderr)
    print(MARKER + json.dumps(
        {"v": 1, "probe": "notion_path_attribution",
         "status": "pass" if ok else "fail",
         "data": {"poller_runs": len(starts),
                  "attributed_to_poller": len(by_poller),
                  "attributed_to_other": len(by_other),
                  "synthetic_markers": len(synthetic),
                  "gap_minutes_median": median,
                  "gap_minutes_worst": worst,
                  "attribution_window_s": a.window,
                  "notes": notes, "failures": failures}},
        sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
