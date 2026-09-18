#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# probe_agy_subordinate_cap -- prove the subordinate cap is enforced in code, not merely requested in the lead's prompt.
"""Falsification suite for agy-lead-plan.yml's MAX_SUBORDINATES clamp.

WHAT WENT WRONG, AND WHY A PROMPT IS NOT A CAP
----------------------------------------------
Until 2026-09-18 the only thing holding an account lead to ten subordinates
was a sentence inside its own planning prompt: "TEN IS A CEILING, NOT A
TARGET". The parser underneath it did `tasks = plan.get('tasks', [])` and
then wrote one dispatch per element, however many there were. So the ceiling
was a request to a language model, and the fleet's actual fan-out was
whatever that model happened to return -- which the owner measured as
burning the AGY quota almost immediately, and which runs into a parallelism
wall well below ten regardless.

The owner's instruction: bring it down to one or two, and ideally let each
account's w01 do the work alone, so the fleet runs about eight bots total
rather than eighty-eight.

WHAT THIS PROBE HOLDS THE WORKFLOW TO
-------------------------------------
The clamp is Python embedded in a YAML `run:` block, so it is extracted from
the workflow file and executed here rather than reimplemented -- a copy in
this probe would pass forever after somebody deleted the original.

The cases that matter are the two directions: a lead that over-produces must
be TRUNCATED, and a lead that under-produces must be LEFT ALONE. A clamp
that returns a fixed number would satisfy only the first.

ASCII only. Exit 0 pass, 1 fail.
"""

import io
import json
import os
import re
import sys

MARKER = "##TBS##"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEAD = os.path.join(ROOT, ".github", "workflows", "agy-lead-plan.yml")
DISPATCH = os.path.join(ROOT, ".github", "workflows", "agy-plan-dispatch.yml")


def read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def extract_clamp(text):
    """Pull the clamp out of the workflow so this probe tests the REAL code.

    Anchored on the two lines that bracket it: the assignment it guards and
    the truncation it performs. If either is renamed this returns None and
    the probe fails rather than silently testing nothing.
    """
    start = text.find("          tasks = plan.get('tasks', [])")
    if start < 0:
        return None
    end = text.find("tasks = tasks[:effective]", start)
    if end < 0:
        return None
    end = text.find("\n", end)
    body = text[start:end]
    # Strip the 10-space YAML indentation the heredoc carries.
    lines = [l[10:] if l.startswith(" " * 10) else l for l in body.split("\n")]
    return "\n".join(lines)


def run_clamp(src, cap, n_tasks):
    """Execute the extracted clamp for one (cap, task-count) pair."""
    plan = {"tasks": [{"title": "t%d" % i, "member_id": "w%02d" % (i + 2)}
                      for i in range(n_tasks)]}
    env = dict(os.environ)
    env["MAX_SUBORDINATES"] = str(cap)
    ns = {"plan": plan, "os": type("O", (), {"environ": env})(),
          "print": lambda *a, **k: None}
    exec(src, ns)                                          # noqa: S102
    return len(ns["tasks"])


def main():
    failures = []
    lead, dispatch = read(LEAD), read(DISPATCH)

    src = extract_clamp(lead)
    if src is None:
        failures.append(
            "could not find the clamp in agy-lead-plan.yml -- either it was "
            "removed (the cap is unenforced again) or renamed (fix this probe)")
    else:
        # (cap, proposed, expected surviving)
        cases = [
            (2, 10, 2),   # the real failure: a lead that over-produces
            (2, 2, 2),    # exactly at the cap
            (2, 1, 1),    # UNDER the cap must NOT be padded up
            (2, 0, 0),    # nothing proposed stays nothing
            (1, 7, 1),    # the owner's "one is enough"
            (0, 5, 1),    # lead-only: the lead's own single task survives
            (0, 1, 1),
            (5, 3, 3),    # a higher cap still does not invent work
        ]
        for cap, proposed, expected in cases:
            try:
                got = run_clamp(src, cap, proposed)
            except Exception as exc:                        # noqa: BLE001
                failures.append("cap=%s proposed=%s raised %s: %s"
                                % (cap, proposed, type(exc).__name__, exc))
                continue
            if got != expected:
                failures.append(
                    "cap=%s, lead proposed %s task(s): %s survived, expected %s"
                    % (cap, proposed, got, expected))

        # A clamp that always returns the same number passes every truncation
        # case above. This is the check that catches it.
        if run_clamp(src, 5, 1) == run_clamp(src, 5, 3):
            failures.append(
                "the clamp returns the same count for 1 and 3 proposed tasks "
                "-- it is not clamping, it is fixing")

    # The model must be TOLD the real number, otherwise every run silently
    # discards work the lead thought it was assigning.
    if "${MAX_SUBORDINATES}" not in lead:
        failures.append("the planning prompt never names MAX_SUBORDINATES, so "
                        "the lead plans against a number nobody told it")
    if "TEN IS A CEILING" in lead:
        failures.append("the old 'TEN IS A CEILING' prompt text is still "
                        "there, contradicting the enforced cap")

    # Lead-only runs must be tagged w01, in BOTH the checklist roster loop and
    # the dispatch loop -- a disagreement between them puts a name on the
    # checklist that never gets a report, which reads as a failed worker.
    w01_rule = lead.count('TAG="${BASE_TAG}-w01"')
    if w01_rule != 2:
        failures.append(
            "the w01 tagging rule appears %d time(s), expected 2 (the "
            "checklist roster loop and the dispatch loop must agree)"
            % w01_rule)

    # The cap has to actually reach the lead; a default nobody passes is a
    # default nobody gets.
    if "max_subordinates=" not in dispatch:
        failures.append("agy-plan-dispatch.yml does not pass max_subordinates, "
                        "so every lead falls back to its own default")

    status = "fail" if failures else "pass"
    if failures:
        print("FAIL: %d finding(s):" % len(failures))
        for f in failures:
            print("  - %s" % f)
    else:
        print("PASS: the cap is enforced in code (truncates over-production, "
              "never pads under-production), the prompt carries the real "
              "number, lead-only runs tag w01 in both loops, and the "
              "dispatcher passes the value through.")
    print(MARKER + json.dumps(
        {"v": 1, "probe": "agy_subordinate_cap", "status": status,
         "data": {"failures": failures, "clamp_found": src is not None}},
        sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
