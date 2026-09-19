#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# probe_notion_event_chain -- fail when the three ends of the Notion -> n8n -> GitHub chain stop agreeing about the strings that hold them together.
"""Falsification suite for the event-driven Notion path.

WHAT BREAKS, AND WHY NOTHING REPORTS IT
---------------------------------------
The chain is:

    Notion automation  --POST-->  n8n webhook  --POST-->  GitHub dispatch
                                                          -> notion-task-poller.yml

Three systems, held together by two string literals that live in different
places and are never compared by anything at run time:

  * the webhook PATH (`notion-task`) -- written in Notion's automation UI, in
    `Integrations/n8n/notion-task-to-dispatch.json`, and in the README a human
    follows to configure the first of those.
  * the dispatch EVENT TYPE (`notion-task`) -- written in the n8n workflow's
    HTTP node, and in `notion-task-poller.yml`'s `on.repository_dispatch.types`.

Rename either on one side and every system stays green. n8n reports a
successful POST. GitHub accepts the dispatch and returns 204 for an event type
no workflow subscribes to -- it does NOT error on an unmatched event_type.
The workflow simply never runs. The fleet then waits on the cron, whose real
median gap is 188 minutes (measured 2026-09-19,
`Tools/probe_notion_path_attribution.py`), and the symptom is "Notion is slow
today" rather than "the chain is severed".

This probe is the only thing in the repository that reads all three ends and
compares them.

WHY THE README IS ONE OF THE ENDS
---------------------------------
The Notion automation cannot be created, read, or verified from here --
Notion's API has no automations endpoint, so that leg exists only as clicks a
human performs by following `Integrations/n8n/README.md`. The README is
therefore not documentation about the chain; it IS the specification of one of
its links. A README pointing at a URL the n8n workflow does not serve produces
a human-configured automation that delivers to nothing, so it is checked like
code.

BOTH DIRECTIONS
---------------
Checking the real files can only ever report "they agree". That is also what a
checker with a typo in every pattern reports. So each rule is additionally run
against a deliberately severed copy of the same files, and the probe fails if
a severed chain is called intact.

ASCII only. Exit 0 pass, 1 fail.
"""

import io
import json
import os
import re
import sys

MARKER = "##TBS##"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
N8N = os.path.join(ROOT, "Integrations", "n8n", "notion-task-to-dispatch.json")
README = os.path.join(ROOT, "Integrations", "n8n", "README.md")
POLLER = os.path.join(ROOT, ".github", "workflows", "notion-task-poller.yml")

# The repository the dispatch must be aimed at. Hard-coded on purpose: the
# whole point is to catch a URL edited to somewhere else.
EXPECT_REPO = "mohammadlali0707-stack/agw-workers"


def read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def n8n_facts(text):
    """What the n8n workflow actually does, read from the workflow itself."""
    f = {"parse_error": None, "path": None, "url": None, "event_type": None,
         "guard": None, "authed": None, "refuses": False}
    try:
        doc = json.loads(text)
    except ValueError as exc:
        f["parse_error"] = str(exc)
        return f
    for node in doc.get("nodes", []):
        t, p = node.get("type", ""), node.get("parameters", {})
        if t.endswith(".webhook"):
            f["path"] = p.get("path")
        elif t.endswith(".if"):
            conds = (p.get("conditions") or {}).get("conditions") or []
            if conds:
                f["guard"] = conds[0].get("rightValue")
        elif t.endswith(".httpRequest"):
            f["url"] = p.get("url")
            f["authed"] = (p.get("authentication") == "genericCredentialType"
                           and p.get("genericAuthType") == "httpHeaderAuth")
            m = re.search(r"event_type:\s*'([^']+)'", p.get("jsonBody", ""))
            f["event_type"] = m.group(1) if m else None
        elif t.endswith(".set"):
            f["refuses"] = True
    # The IF node's FALSE branch must go somewhere that is not GitHub. A guard
    # wired to the same node on both outputs admits everything while looking
    # like it filters.
    outs = (doc.get("connections", {}).get("Is it really from Notion?", {})
            .get("main", []))
    names = [[c.get("node") for c in branch] for branch in outs]
    f["branches_differ"] = len(names) == 2 and names[0] != names[1]
    return f


def poller_types(text):
    """The event types notion-task-poller.yml subscribes to."""
    import yaml
    doc = yaml.safe_load(text)
    raw = doc.get("on", doc.get(True)) or {}
    rd = raw.get("repository_dispatch") if isinstance(raw, dict) else None
    types = (rd or {}).get("types") if isinstance(rd, dict) else None
    job = (doc.get("jobs") or {}).get("poll") or {}
    return set(types or []), " ".join((job.get("if") or "").split())


def readme_urls(text):
    """EVERY webhook URL the README names, not the first one.

    The first draft of this took `re.search` and read one match. The README
    names the URL in the diagram, in the numbered Notion steps, and in the
    verification curl -- so a rename applied to one of them left the others
    intact and this probe read whichever came first and called the chain
    sound. Its own mutation case caught that.

    A README carrying two DIFFERENT webhook URLs is itself the defect: a human
    following it configures Notion from whichever one they happened to read.
    So all of them are returned and disagreement is a finding.
    """
    return re.findall(r"https://[A-Za-z0-9.\-]+/webhook[A-Za-z0-9\-_]*/"
                      r"([A-Za-z0-9\-_]+)", text), \
        re.findall(r"https://[A-Za-z0-9.\-]+/webhook[A-Za-z0-9\-_]*/"
                   r"[A-Za-z0-9\-_]+", text)


def check(n8n_text, poller_text, readme_text):
    """Every rule, over supplied text, so a severed copy can be checked too."""
    bad = []
    f = n8n_facts(n8n_text)
    if f["parse_error"]:
        return ["the n8n workflow is not valid JSON, so n8n cannot import it: "
                + f["parse_error"]]
    types, job_if = poller_types(poller_text)
    doc_paths, doc_urls = readme_urls(readme_text)

    if not types:
        bad.append("notion-task-poller.yml no longer subscribes to "
                   "repository_dispatch at all -- the only push door is shut "
                   "and every task waits for the cron (median 188 min)")
    elif f["event_type"] not in types:
        bad.append("n8n dispatches event_type %r but the poller subscribes to "
                   "%s -- GitHub returns 204 for an unmatched type, so this "
                   "severs the chain without one error anywhere"
                   % (f["event_type"], sorted(types)))

    if job_if and "github.event_name" in job_if and \
            "repository_dispatch" not in job_if:
        bad.append("the poller's job `if:` discriminates on event_name and "
                   "never admits repository_dispatch -- it would fire and then "
                   "refuse itself, green and silent")

    if not doc_paths:
        bad.append("the README documents no webhook URL, so the human "
                   "configuring Notion has nothing to copy")
    else:
        if len(set(doc_urls)) > 1:
            bad.append("the README names %d DIFFERENT webhook URLs (%s) -- a "
                       "human configures Notion from whichever one they read"
                       % (len(set(doc_urls)), sorted(set(doc_urls))))
        wrong = sorted(p for p in set(doc_paths) if p != f["path"])
        if wrong:
            bad.append("the README tells a human to POST to .../%s but n8n "
                       "serves /%s -- the Notion automation would be "
                       "configured against a path that answers nothing"
                       % ("|".join(wrong), f["path"]))
    test_urls = sorted(u for u in doc_urls if "/webhook-test/" in u)
    if test_urls:
        bad.append("the README points at n8n's TEST url (%s), which answers "
                   "only while the editor is open listening"
                   % ", ".join(test_urls))

    if not f["url"] or EXPECT_REPO not in (f["url"] or ""):
        bad.append("the n8n HTTP node does not dispatch to %s (url=%r)"
                   % (EXPECT_REPO, f["url"]))
    if not f["authed"]:
        bad.append("the n8n HTTP node carries no header credential -- GitHub "
                   "answers 401 and n8n records a failed execution nobody "
                   "reads")
    if f["guard"] != "automation":
        # Was 'notion' until this was measured against a real firing
        # (execution 7101): Notion's own webhook envelope already puts an
        # OBJECT at `source` (`{type:'automation', automation_id, ...}`),
        # which collided with this repo's invented string-valued `source`
        # and made every real firing fail type coercion -- three real
        # attempts, then Notion auto-paused the automation. 'automation' is
        # the authentic signal Notion itself sends, not another guess.
        bad.append("the IF node no longer requires source.type == "
                   "'automation' (rightValue=%r) -- the webhook is public, "
                   "so anything that finds the URL can make the fleet "
                   "dispatch" % (f["guard"],))
    if not f["refuses"] or not f["branches_differ"]:
        bad.append("the guard's false branch is not wired to a dead end, so "
                   "it filters nothing while looking like it does")
    return bad


def sever(text, old, new):
    return text.replace(old, new, 1)


def main():
    for p in (N8N, README, POLLER):
        if not os.path.exists(p):
            print("::error::missing %s" % p, file=sys.stderr)
            print(MARKER + json.dumps(
                {"v": 1, "probe": "notion_event_chain", "status": "fail",
                 "data": {"failures": ["missing %s" % os.path.relpath(p, ROOT)]}},
                sort_keys=True))
            return 1

    n8n_text, poller_text, readme_text = read(N8N), read(POLLER), read(README)
    failures = check(n8n_text, poller_text, readme_text)

    # DISCRIMINATION. Each mutation severs the chain in one real way; a checker
    # that reports the live files intact and ALSO reports these intact is not
    # measuring anything.
    blind = []
    cases = [
        ("the poller's subscribed event type renamed",
         (n8n_text, sever(poller_text, "types: [notion-task]",
                          "types: [notion-task-v2]"), readme_text)),
        ("the webhook path renamed in n8n but not in the README",
         (sever(n8n_text, '"path": "notion-task"', '"path": "notion-task-2"'),
          poller_text, readme_text)),
        ("the GitHub credential dropped from the HTTP node",
         (sever(n8n_text, '"authentication": "genericCredentialType"',
                '"authentication": "none"'), poller_text, readme_text)),
        ("the dispatch aimed at another repository",
         (sever(n8n_text, EXPECT_REPO, "someone-else/other-repo"),
          poller_text, readme_text)),
        ("the source guard opened up",
         (sever(n8n_text, '"rightValue": "automation"', '"rightValue": ""'),
          poller_text, readme_text)),
        ("the README pointing at the test url",
         (n8n_text, poller_text,
          sever(readme_text, "n8n.airboxvip.top/webhook/notion-task",
                "n8n.airboxvip.top/webhook-test/notion-task"))),
    ]
    for label, args in cases:
        if not check(*args):
            blind.append("a severed chain was reported intact: %s" % label)

    failures += blind
    status = "fail" if failures else "pass"
    if failures:
        print("FAIL: %d finding(s):" % len(failures))
        for x in failures:
            print("  - %s" % x)
    else:
        print("PASS: n8n's webhook path matches the README a human configures "
              "Notion from, its dispatch event type matches what the poller "
              "subscribes to, the poller admits that event, the call is "
              "authenticated and aimed at %s, and the public webhook still "
              "refuses anything whose source.type isn't 'automation'. All "
              "%d severed "
              "variants were detected." % (EXPECT_REPO, len(cases)))
    print(MARKER + json.dumps(
        {"v": 1, "probe": "notion_event_chain", "status": status,
         "data": {"failures": failures, "severed_cases": len(cases),
                  "undetected": len(blind)}}, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
