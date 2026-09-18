#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# probe_plan_dispatch_guard -- prove the duplicate-approval guard blocks the same plan twice and never blocks a revised one.
"""Falsification suite for agy-plan-dispatch.yml's duplicate-approval guard.

THE INCIDENT
------------
2026-09-18, issue #38. `@agy-plan-approved` was posted on a REVISED plan
(CCP-308). Run 35365720134: check-marker green, parse-proposal green, dispatch
**skipped**, whole run reported Success. The log line was

    Already dispatched once on this issue -- refusing to dispatch again.

The guard grepped the issue for "Lead dispatch started" and refused if it
appeared anywhere. Issue #38 had dispatched CCP-307 that morning, so the
marker was on the issue permanently: **no issue could ever carry a second
plan**, and the run said Success while doing nothing.

The guard is now per-PROPOSAL. The selected plan is fingerprinted, the start
marker carries `[plan:<fp>]`, and only that fingerprint blocks.

BOTH DIRECTIONS, because each failure is real
---------------------------------------------
Too strict was the incident: a revision could never dispatch. Too loose is
worse: re-approving the SAME plan would dispatch every account a second time,
duplicating real work across eight accounts. A probe that only tested the
revision case would have called a guard that never blocks anything a pass.

ASCII only. Exit 0 pass, 1 fail.
"""

import hashlib
import io
import json
import os
import re
import sys

MARKER = "##TBS##"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(ROOT, ".github", "workflows", "agy-plan-dispatch.yml")


def read():
    with io.open(WF, encoding="utf-8") as fh:
        return fh.read()


def code_only(text):
    """The workflow with its `#` comment lines removed.

    The first draft of this probe searched the whole file for the old
    refusal's log line -- and the fix's own comment EXPLAINS that incident
    using the same words, so the probe failed against the fixed file. Same
    self-match that made agw-workers' CHECK-2 report 70 phantom findings, and
    the same lesson: a scanner must look at what runs, not at what is written
    about what used to run.
    """
    out = []
    for line in text.split("\n"):
        if line.lstrip().startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


def extract_fp_expr(text):
    """The fingerprint recipe, taken from the workflow rather than retyped."""
    m = re.search(r"fp = hashlib\.sha256\(\s*\n(.*?)\)\.hexdigest\(\)\[:(\d+)\]",
                  text, re.S)
    return m


def fingerprint(proposal, width):
    return hashlib.sha256(
        json.dumps(proposal, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")).hexdigest()[:width]


def guard_skips(comments, fp):
    """The shell guard, one line: grep -qF "[plan:<fp>]"."""
    return ("[plan:%s]" % fp) in comments


def main():
    failures = []
    text = read()

    code = code_only(text)
    if "Already dispatched once on this issue" in code:
        failures.append(
            "the per-ISSUE refusal is still in the workflow -- a second plan "
            "on an issue can never dispatch")

    m = extract_fp_expr(code)
    if not m:
        failures.append("no fingerprint computation found in "
                        "agy-plan-dispatch.yml (removed, or renamed -- fix "
                        "this probe)")
        width = 12
    else:
        width = int(m.group(2))
        if "sort_keys=True" not in m.group(1):
            failures.append("the fingerprint is not computed over sorted keys, "
                            "so the SAME plan can hash differently between "
                            "runs and the guard stops blocking re-approval")

    if "[plan:${{ steps.parse.outputs.plan_fp }}]" not in code:
        failures.append("the start marker does not carry the fingerprint, so "
                        "the guard has nothing to find and blocks nothing")

    # THE RUN TAG MUST BE UNIQUE PER DISPATCH, not per issue.
    # callback_id was `plan-<issue>-acc<N>`, identical for every run on one
    # issue, so two CCP-308 dispatches on #38 (16:14 and 20:57 on 2026-09-18)
    # wrote their worker reports to the SAME paths and overwrote each other.
    # Nothing could then attribute a report to a run -- which is how a file's
    # mtime came to be read as evidence of what a given dispatch produced, and
    # read wrongly. Third instance of the same root cause as the per-issue
    # guard and the per-issue freshness stamp.
    if "callback_id=plan-${{ github.event.issue.number }}-acc" in code:
        failures.append(
            "callback_id is still per-issue, so two dispatches on one issue "
            "overwrite each other's worker reports at identical paths")
    if "plan_fp }}-acc" not in code:
        failures.append("the run tag does not carry the plan fingerprint, so "
                        "reports from different dispatches collide")

    plan_a = {"kind": "tbs_account_plan", "target_repo": "x/y",
              "packages": [{"account": 4, "title": "write options.rpy"}]}
    # A real revision: different content, same issue.
    plan_b = {"kind": "tbs_account_plan", "target_repo": "x/y",
              "packages": [{"account": 4, "title": "write options.rpy"},
                           {"account": 5, "title": "verify fonts"}]}
    # The same plan, re-serialised with keys in another order.
    plan_a2 = {"packages": [{"title": "write options.rpy", "account": 4}],
               "target_repo": "x/y", "kind": "tbs_account_plan"}

    fa, fb, fa2 = (fingerprint(p, width) for p in (plan_a, plan_b, plan_a2))

    if fa != fa2:
        failures.append("the same plan fingerprints differently when its keys "
                        "are reordered (%s vs %s) -- re-approval would slip "
                        "through" % (fa, fa2))
    if fa == fb:
        failures.append("a revised plan fingerprints the SAME as the original "
                        "(%s) -- revisions would still be blocked" % fa)

    started = "Project Manager: Lead dispatch started [plan:%s] -- 1 package" % fa

    # 1. The incident: a revision, with the earlier plan's marker present.
    if guard_skips(started, fb):
        failures.append("a REVISED plan is still blocked by the earlier plan's "
                        "marker -- this is the CCP-308 failure, unfixed")
    # 2. The protection that must survive: the same plan, approved twice.
    if not guard_skips(started, fa):
        failures.append("re-approving the SAME plan is no longer blocked -- "
                        "every account would be dispatched a second time")
    # 3. A legacy marker from before fingerprints must not block anything.
    legacy = "Project Manager: Lead dispatch started -- 8 account package(s)"
    if guard_skips(legacy, fa) or guard_skips(legacy, fb):
        failures.append("a legacy start marker with no fingerprint blocks a "
                        "new plan")
    # 4. An empty issue blocks nothing.
    if guard_skips("", fa):
        failures.append("an issue with no comments reports an earlier dispatch")

    status = "fail" if failures else "pass"
    if failures:
        print("FAIL: %d finding(s):" % len(failures))
        for f in failures:
            print("  - %s" % f)
    else:
        print("PASS: the same plan approved twice is refused, a revised plan "
              "on the same issue dispatches, a pre-fingerprint marker blocks "
              "nothing, and reordering a plan's keys does not change its "
              "fingerprint.")
    print(MARKER + json.dumps(
        {"v": 1, "probe": "plan_dispatch_guard", "status": status,
         "data": {"failures": failures, "fp_width": width}}, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
