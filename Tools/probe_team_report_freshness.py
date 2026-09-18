#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# probe_team_report_freshness -- prove a team report from an earlier run cannot be summarised as this run's work.
"""Falsification suite for the team-report staleness check.

THE INCIDENT THIS EXISTS FOR
----------------------------
CCP-307 ("Scaffold first Ren'Py slice", issue #38; report
Claud-Cloud-Project#44). Measured on the repository afterwards, by the date
each account's team report was last written:

    acc0 09-18   acc1 09-18   acc2 09-18   acc3 09-18   acc7 09-18
    acc4 09-15   acc5 09-15   acc8 09-15

Accounts 4, 5 and 8 produced NOTHING for issue #38. But
`Reports/agy/team_reports/acc<N>.md` is a fixed path overwritten per run, so
their files from the issue #13/#14 cycle three days earlier were still on
disk, and `agy-final-report.yml` tested `os.path.isfile(path)` and nothing
else. It handed those three-day-old reports to the summariser as this
cycle's work.

That is why the fleet report describes account 4 building `gui.rpy`,
`screens.rpy` and 31 GUI assets, and account 5 doing pixel-level RTL
validation, in a cycle where the repository ended with **zero .rpy files**.
The report was not lying; it was fed stale input by a check that could not
tell "reported" from "reported three days ago about something else".

WHAT IS PINNED HERE
-------------------
The extracted staleness filter is executed against synthetic report sets, in
both directions: a report stamped with THIS run must be summarised, and one
stamped with another run must not. An unstamped file must be treated as
stale, because every stale file seen in the real incident was unstamped --
and a filter that waves those through would have caught nothing at all.

ASCII only. Exit 0 pass, 1 fail.
"""

import io
import json
import os
import re
import sys
import tempfile

MARKER = "##TBS##"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FINAL = os.path.join(ROOT, ".github", "workflows", "agy-final-report.yml")
LEAD = os.path.join(ROOT, ".github", "workflows", "agy-lead-plan.yml")


def read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def extract_filter(text):
    """Pull the classifier out of the workflow, so this tests the REAL code."""
    start = text.find("          accounts = json.loads(os.environ['ALL_ACCOUNTS'])")
    if start < 0:
        return None
    end = text.find("          PYEOF", start)
    if end < 0:
        return None
    body = text[start:end]
    lines = [l[10:] if l.startswith(" " * 10) else l for l in body.split("\n")]
    return "\n".join(lines)


def run_filter(src, reports, issue):
    """Execute the extracted classifier over a synthetic team_reports dir."""
    tmp = tempfile.mkdtemp()
    d = os.path.join(tmp, "Reports", "agy", "team_reports")
    os.makedirs(d)
    for acc, body in reports.items():
        if body is None:
            continue
        with io.open(os.path.join(d, "acc%s.md" % acc), "w",
                     encoding="utf-8") as fh:
            fh.write(body)
    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        env = dict(os.environ)
        env["ALL_ACCOUNTS"] = json.dumps(sorted(reports))
        env["ISSUE_NUMBER"] = issue
        ns = {"os": type("O", (), {"environ": env, "path": os.path,
                                   "getcwd": os.getcwd})(),
              "json": json, "re": re, "print": lambda *a, **k: None,
              "open": io.open}
        exec(src, ns)                                      # noqa: S102
        out = {}
        for name in ("all_reports", "missing", "stale"):
            p = "/tmp/%s.txt" % name
            with io.open(p, encoding="utf-8") as fh:
                out[name] = fh.read()
        return out
    finally:
        os.chdir(cwd)


def stamp(issue, tag="plan-38-acc4", when="2026-09-18T13:00:00Z", acc="4"):
    return ("<!-- agy-team-report: tag=%s issue=%s account=%s written=%s -->\n"
            "\nThis account built gui.rpy and 31 GUI assets.\n"
            % (tag, issue, acc, when))


def main():
    failures = []
    final, lead = read(FINAL), read(LEAD)

    if "agy-team-report:" not in lead:
        failures.append("agy-lead-plan.yml no longer stamps the team report "
                        "with the run that wrote it -- nothing downstream can "
                        "tell fresh from stale")

    src = extract_filter(final)
    if src is None:
        failures.append("could not extract the team-report filter from "
                        "agy-final-report.yml (removed, or renamed -- fix this "
                        "probe)")
    else:
        # The exact shape of the real incident: two fresh, one stale, one
        # absent.
        got = run_filter(src, {
            "0": stamp("38", "plan-38-acc0", acc="0"),
            "3": stamp("38", "plan-38-acc3", acc="3"),
            "4": stamp("14", "plan-14-acc4", "2026-09-15T06:13:00Z", "4"),
            "5": None,
        }, issue="38")

        if "Account 0" not in got["all_reports"] or \
                "Account 3" not in got["all_reports"]:
            failures.append("a team report stamped with THIS run was not "
                            "passed to the summariser")
        if "Account 4" in got["all_reports"]:
            failures.append("the stale account-4 report (issue 14) was handed "
                            "to the summariser as this run's work -- this is "
                            "the CCP-307 failure, unfixed")
        if "4" not in got["stale"]:
            failures.append("the stale report was not REPORTED as stale; "
                            "dropping it silently is the same blindness in the "
                            "other direction")
        if got["missing"].strip() != "5":
            failures.append("an absent report was not listed as missing "
                            "(got %r)" % got["missing"])

        # Unstamped must be stale: every file in the real incident was
        # unstamped, so a filter that trusts them catches nothing.
        got = run_filter(src, {"4": "No stamp at all.\nWe did great work.\n"},
                         issue="38")
        if "Account 4" in got["all_reports"]:
            failures.append("an UNSTAMPED team report was treated as fresh -- "
                            "every stale file in the real incident was "
                            "unstamped, so this filter would have caught none "
                            "of them")

        # And the other direction, so this is not just a filter that rejects
        # everything: the same report, stamped for this run, must get through.
        got = run_filter(src, {"4": stamp("38")}, issue="38")
        if "Account 4" not in got["all_reports"]:
            failures.append("a correctly stamped report was rejected -- a "
                            "filter that drops everything passes every "
                            "one-sided test and reports nothing forever")

    status = "fail" if failures else "pass"
    if failures:
        print("FAIL: %d finding(s):" % len(failures))
        for f in failures:
            print("  - %s" % f)
    else:
        print("PASS: the lead stamps each team report with its run; a report "
              "from an earlier issue, and an unstamped one, are both withheld "
              "from the summariser and named as stale, while a report from "
              "this run still gets through.")
    print(MARKER + json.dumps(
        {"v": 1, "probe": "team_report_freshness", "status": status,
         "data": {"failures": failures, "filter_found": src is not None}},
        sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
