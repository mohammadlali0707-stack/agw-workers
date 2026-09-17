#!/usr/bin/env python3
"""Read a fork's own run logs from here, with that account's PAT.

WHY THIS EXISTS
---------------
plan-34-acc3-w08 produced no report while its job went green. The fix already
shipped records that failure instead of losing it, but it does not say WHY agy
produced nothing -- and that log lives in ngocgminh5-debug/agw-workers, which
this session cannot read directly. A workflow running in the control repo can,
using the same per-account PATs the rest of the fleet already uses.

It also answers a question asked directly: did ACC3's workers actually run
under ACC3's identity? agw-worker.yml prints both halves of that decision --
"Repo owner: X -> account index: N" from its own-identity step, and "Primary
account for this run: accN" from its token-selection step. Those two lines are
the measurement; anything else is inference.

WHAT IT REPORTS, per matching run
---------------------------------
  - the two identity lines above
  - whether the agy step reported a quota/auth wall
  - whether an output file existed to commit, and whether a report was pushed
  - the last error lines, if any

Read-only: lists runs, downloads logs. No dispatch, no push, no re-run.
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"

ACCOUNTS = {
    0: "Mohammadlali", 1: "momonakikugava-pixel", 2: "lali94m-max",
    3: "ngocgminh5-debug", 4: "hmmletssee7-design", 5: "kidding602",
    6: "mohammadlali0707-stack", 7: "mohammad97okk", 8: "moradzahra85-png",
}

# What to pull out of a log. Ordered, and each is a fact the log states
# outright rather than something inferred from its absence.
SIGNALS = [
    # Which task this run actually was. Without it, "no log mentions w08" is
    # ambiguous between "w08 never ran" and "w08 ran under another tag".
    ("run_tag", re.compile(r'RUN_TAG="(plan-[^"]+)"')),
    ("callback", re.compile(r"callback_id=(\S+)")),
    ("own_identity", re.compile(r"Repo owner: (\S+) -> account index: (\d+)")),
    ("token_account", re.compile(r"Primary account for this run: (acc\d+)")),
    ("quota", re.compile(r"(AGY_QUOTA|quota reached|Individual quota)", re.I)),
    ("auth", re.compile(r"(AGY_AUTH|not authenticated|401 Unauthorized)", re.I)),
    ("timeout", re.compile(r"(AGY_TIMEOUT|timeout 1700|rc=124)")),
    ("empty_output", re.compile(r"(AGY_EMPTY|no agy output)", re.I)),
    ("output_committed", re.compile(r"agy: record raw output for (\S+)")),
    ("fallback", re.compile(r"(falling back|random fallback|attempt 2)", re.I)),
]


def req(url, token, raw=False):
    r = urllib.request.Request(url, method="GET")
    r.add_header("Authorization", f"Bearer {token}")
    r.add_header("Accept", "application/vnd.github+json")
    try:
        with urllib.request.urlopen(r, timeout=90) as resp:
            body = resp.read()
            return (body.decode("utf-8", "replace") if raw
                    else json.loads(body.decode())), resp.status
    except urllib.error.HTTPError as e:
        return ("" if raw else {}), e.code


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, required=True)
    ap.add_argument("--workflow", default="agw-worker.yml")
    ap.add_argument("--match", default="",
                    help="only report runs whose log contains this (e.g. a task tag)")
    ap.add_argument("--limit", type=int, default=12)
    a = ap.parse_args()

    owner = ACCOUNTS.get(a.account)
    token = os.environ.get(f"ACC{a.account}_PAT", "")
    if not owner:
        print(f"::error::unknown account {a.account}", file=sys.stderr)
        return 1
    if not token:
        print(f"::error::ACC{a.account}_PAT is not set", file=sys.stderr)
        return 1

    repo = f"{owner}/agw-workers"
    runs, code = req(f"{API}/repos/{repo}/actions/workflows/{a.workflow}/runs"
                     f"?per_page={a.limit}", token)
    if code != 200:
        print(f"::error::cannot list runs for {repo}: HTTP {code}", file=sys.stderr)
        return 1

    findings, examined, matched = [], 0, 0
    for run in runs.get("workflow_runs", []):
        jobs, code = req(f"{API}/repos/{repo}/actions/runs/{run['id']}/jobs", token)
        if code != 200:
            continue
        for job in jobs.get("jobs", []):
            log, code = req(f"{API}/repos/{repo}/actions/jobs/{job['id']}/logs",
                            token, raw=True)
            examined += 1
            if code != 200 or not log:
                continue
            if a.match and a.match not in log:
                continue
            matched += 1
            hit = {"run": run["id"], "job": job["id"],
                   "conclusion": job.get("conclusion"),
                   "started": job.get("started_at"),
                   "url": job.get("html_url")}
            for name, rx in SIGNALS:
                m = rx.search(log)
                hit[name] = (m.group(0)[:90] if m else None)
            errs = [l.split(" ", 1)[-1].strip()
                    for l in log.splitlines() if "##[error]" in l]
            hit["errors"] = errs[-3:]
            findings.append(hit)

            print(f"\n=== run {run['id']} job {job['id']} ({job.get('conclusion')})")
            print(f"    {job.get('html_url')}")
            for name, _ in SIGNALS:
                if hit[name]:
                    print(f"    {name:18s} {hit[name]}")
            for e in hit["errors"]:
                print(f"    error              {e[:110]}")

    print(f"\n{examined} job log(s) examined, {matched} matched "
          f"{a.match!r} in {repo}")
    print('##TBS##' + json.dumps(
        {"data": {"repo": repo, "examined": examined, "matched": matched,
                  "findings": findings},
         "probe": "fork_run_logs",
         "status": "pass" if matched else "fail", "v": 1}, sort_keys=True))
    return 0 if matched else 1


if __name__ == "__main__":
    sys.exit(main())
