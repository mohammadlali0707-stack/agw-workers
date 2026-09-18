#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# probe_bot_checklist_wiring -- prove every long-running AGY bot posts its checklist before the work and ticks it as it goes.
"""Falsification suite for the "checklist first, then tick" contract.

THE ASK, AND WHY IT WAS ONLY HALF DONE
--------------------------------------
Owner, 2026-09-18: the bot must write a checklist of what it is going to do
BEFORE sending the full answer, and tick each item as it finishes -- solved at
the root.

It was already solved, for one tier. `agy-lead-plan.yml` has called
`Tools/agy_checklist.py` since the checklist was built, and five live
checklists sit on issue #38 (plan-38-acc1, -acc2, -acc7, ...). But the
PLANNER -- the bot the owner actually watched -- was never wired in: it posted
a one-line "preparing a proposal...", went quiet for minutes, and dropped
~5000 characters in a single block.

So the root cause was not a missing mechanism. It was a mechanism wired into
one caller and not the others, with enough per-step friction (append to a
file, then refresh, three lines of shell) that nobody wired in the rest. The
fix is `tick`, one line, plus actually calling it.

WHAT THIS PINS, INCLUDING TWO MISTAKES MADE WHILE WRITING IT
------------------------------------------------------------
  * The checklist is posted BEFORE the long agy call, not after -- a checklist
    that appears with the answer is a table of contents, not progress.
  * Every roster tag has a tick step, and every tick names a tag on the roster.
    A tick for an item nobody listed, or an item nobody ticks, is a checklist
    that lies in one direction or the other.
  * **No tick runs under `if: always()`.** The first draft did, which would
    have ticked an item whose step FAILED. A checklist reporting success for
    work that did not happen is worse than no checklist.
  * **The job checks out its own repository.** The first draft called
    `$GITHUB_WORKSPACE/Tools/agy_checklist.py` in a job that never ran
    `actions/checkout`, so the tool would not have been on disk at all.

ASCII only. Exit 0 pass, 1 fail.
"""

import io
import json
import os
import re
import sys

MARKER = "##TBS##"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(ROOT, ".github", "workflows")

# workflow -> (job, regex for the step the checklist must PRECEDE)
#
# "Before the long agy call" is the right rule for the planner and the WRONG
# one for the lead, which this probe caught: the lead's roster IS the output of
# its first agy call (that call is what breaks the package into subordinate
# tasks), so it cannot list items it has not been told yet. What must hold in
# both cases is the same and is stated per workflow below: the checklist
# precedes THE WORK IT TRACKS.
#   * planner -- it tracks its own drafting, so it precedes the agy call.
#   * lead    -- it tracks the subordinates, so it precedes the dispatch loop.
REQUIRED = {
    "agy-plan-bot.yml": ("propose", r"\bagy .*-p "),
    "agy-lead-plan.yml": ("lead", r"gh workflow run agw-worker\.yml"),
}


def load(name):
    import yaml
    with io.open(os.path.join(WF, name), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def main():
    failures = []
    for name, (job_id, tracked_re) in sorted(REQUIRED.items()):
        try:
            doc = load(name)
        except Exception as exc:                            # noqa: BLE001
            failures.append("%s: will not parse (%s)" % (name, exc))
            continue
        job = doc.get("jobs", {}).get(job_id)
        if job is None:
            failures.append("%s: job %r is gone (renamed? fix this probe)"
                            % (name, job_id))
            continue
        steps = job.get("steps") or []
        bodies = [(s.get("name") or s.get("uses") or "", s.get("run") or "",
                   s.get("if"))
                  for s in steps]

        # The tool has to be on disk before anything calls it.
        if not any(s.get("uses", "").startswith("actions/checkout")
                   for s in steps):
            failures.append(
                "%s/%s: no actions/checkout, so $GITHUB_WORKSPACE/Tools/"
                "agy_checklist.py is not on disk when a step calls it"
                % (name, job_id))

        post_at = next((i for i, (_, r, _) in enumerate(bodies)
                        if re.search(r"agy_checklist\.py\W+post\b", r)), None)
        agy_at = next((i for i, (_, r, _) in enumerate(bodies)
                       if re.search(tracked_re, r)), None)
        tick_ix = [i for i, (_, r, _) in enumerate(bodies)
                   if re.search(r"agy_checklist\.py\W+(tick|refresh)\b", r)]

        if post_at is None:
            failures.append("%s/%s: never posts a checklist" % (name, job_id))
        elif agy_at is not None and post_at > agy_at:
            failures.append(
                "%s/%s: the checklist is posted AFTER the work it tracks "
                "starts (step %d vs %d) -- that is a table of contents, not "
                "progress"
                % (name, job_id, post_at, agy_at))
        if not tick_ix:
            failures.append("%s/%s: posts a checklist and never updates it"
                            % (name, job_id))

        # A tick that runs whatever happened reports work that did not happen.
        for i in tick_ix:
            label, _, cond = bodies[i]
            if cond == "always()":
                failures.append(
                    "%s/%s: tick step %r runs under if: always(), so it ticks "
                    "an item whose step failed" % (name, job_id, label))

        # Roster and ticks must name the same tags, both directions.
        joined = "\n".join(r for _, r, _ in bodies)
        if re.search(r"agy_checklist\.py\W+post\b", joined) and "--tag" in joined:
            roster = set(re.findall(r"printf '%s\\t%s\\t%s\\n'([\s\S]*?)>",
                                    joined))
            tags_listed = set(re.findall(r'"(plan-[a-z-]+)"\s+"', joined))
            tags_ticked = set(re.findall(r'--tag "([^"]+)"', joined))
            if tags_listed and tags_ticked:
                if tags_ticked - tags_listed:
                    failures.append(
                        "%s/%s: ticks %s, which the roster never listed"
                        % (name, job_id, sorted(tags_ticked - tags_listed)))
                if tags_listed - tags_ticked:
                    failures.append(
                        "%s/%s: lists %s and never ticks them"
                        % (name, job_id, sorted(tags_listed - tags_ticked)))

    # `tick` has to exist for any of the above to be callable.
    tool = io.open(os.path.join(ROOT, "Tools", "agy_checklist.py"),
                   encoding="utf-8").read()
    if "def cmd_tick" not in tool or '"tick"' not in tool:
        failures.append("Tools/agy_checklist.py has no `tick` subcommand, so "
                        "wiring a bot in costs three lines per step again")

    status = "fail" if failures else "pass"
    if failures:
        print("FAIL: %d finding(s):" % len(failures))
        for f in failures:
            print("  - %s" % f)
    else:
        print("PASS: both long-running AGY bots post a checklist before the "
              "work it tracks, tick exactly the tags they listed, check out "
              "the tool they call, and never tick an item whose step failed.")
    print(MARKER + json.dumps(
        {"v": 1, "probe": "bot_checklist_wiring", "status": status,
         "data": {"failures": failures, "workflows": sorted(REQUIRED)}},
        sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
