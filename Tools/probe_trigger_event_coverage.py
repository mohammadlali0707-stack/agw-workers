#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# probe_trigger_event_coverage -- fail a workflow that subscribes to an event and then refuses it at the first gate.
"""Falsification suite for "every event in `on:` can reach the work".

THE INCIDENT
------------
2026-09-19. The Notion poller created issue #42 from a real Notion page, body
correctly ending in `ccp @agy-plan`. `agy-plan-bot.yml` fired 3 seconds later
on the `issues` event and reported **skipped**.

Its `on:` declares `issues: [opened]` and `issue_comment`. Its `check` step
already read `comment.body || issue.body`. Its `propose` job already branched
on both event names. But the `check-marker` job's `if:` required
`github.event_name == 'issue_comment'` -- so an issue OPENED could never get
past the first gate. Every other layer had been taught about both events; the
one line in front of them had not.

The whole Notion -> GitHub -> plan chain stopped there, with a green run and
a skipped job. Nothing failed. Nothing said why.

THE RULE, STATED GENERALLY
--------------------------
A workflow that subscribes to an event and then refuses it at the gate is
lying about what it handles. So: for each event in `on:`, the entry gate's
`if:` must admit that event -- either by naming it, or by not discriminating
on `github.event_name` at all.

This is deliberately a COVERAGE check, not a correctness one. It cannot tell
whether the body test is right; it tells you the event can reach it. That is
exactly the failure above, where every body test was already correct.

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

# Only the trigger workflows: those whose FIRST job exists to decide whether
# an event is for them. A build workflow with one `push` trigger has nothing
# to cover.
WATCHED = {
    "agy-plan-bot.yml": "check-marker",
    "agy-issue-bot.yml": "check-marker",
    "agw-dispatch.yml": "check-marker",
    "agy-plan-dispatch.yml": "check-marker",
}

# Events that carry a body a gate might test, and the context each one uses.
BODY_CONTEXT = {
    "issues": "github.event.issue",
    "issue_comment": "github.event.comment",
}


def load(name):
    import yaml
    with io.open(os.path.join(WF, name), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def on_events(doc):
    # PyYAML turns the bare key `on:` into the boolean True.
    raw = doc.get("on", doc.get(True))
    if isinstance(raw, dict):
        return set(raw)
    if isinstance(raw, list):
        return set(raw)
    return {raw} if raw else set()


def main():
    failures = []
    for name, job_id in sorted(WATCHED.items()):
        path = os.path.join(WF, name)
        if not os.path.exists(path):
            failures.append("%s: not found (renamed? fix this probe)" % name)
            continue
        try:
            doc = load(name)
        except Exception as exc:                            # noqa: BLE001
            failures.append("%s: will not parse (%s)" % (name, exc))
            continue

        events = on_events(doc) & set(BODY_CONTEXT)
        job = doc.get("jobs", {}).get(job_id)
        if job is None:
            failures.append("%s: gate job %r is gone (fix this probe)"
                            % (name, job_id))
            continue
        cond = " ".join((job.get("if") or "").split())
        if not cond:
            continue          # no gate: every event reaches the work already

        # A gate that never mentions event_name does not discriminate, so it
        # admits everything -- that is fine and needs no per-event clause.
        if "github.event_name" not in cond:
            continue

        for ev in sorted(events):
            named = ("'%s'" % ev) in cond or ('"%s"' % ev) in cond
            if not named:
                failures.append(
                    "%s: `on:` subscribes to %r but %s's `if:` never admits it "
                    "-- the workflow fires and then refuses itself, green and "
                    "silent" % (name, ev, job_id))
                continue
            # Named, but does it test the right body for that event? A clause
            # that admits `issues` while only ever reading comment.body is the
            # same bug wearing a disguise.
            ctx = BODY_CONTEXT[ev]
            if ctx not in cond:
                failures.append(
                    "%s: admits %r but its `if:` never reads %s.body, so the "
                    "event is let in and then judged on a field it does not "
                    "have" % (name, ev, ctx))

    status = "fail" if failures else "pass"
    if failures:
        print("FAIL: %d finding(s):" % len(failures))
        for f in failures:
            print("  - %s" % f)
    else:
        print("PASS: every trigger workflow's entry gate admits each event its "
              "`on:` subscribes to, and reads that event's own body.")
    print(MARKER + json.dumps(
        {"v": 1, "probe": "trigger_event_coverage", "status": status,
         "data": {"failures": failures, "workflows": sorted(WATCHED)}},
        sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
