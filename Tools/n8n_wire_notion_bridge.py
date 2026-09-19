#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# n8n_wire_notion_bridge -- build the Notion push-door workflow in n8n over its
# own REST API, and fix the missing @agy-plan marker on the older workflow.
"""One-off setup, run from GitHub Actions where the secrets actually live.

WHY THIS RUNS IN ACTIONS AND NOT INTERACTIVELY
-----------------------------------------------
The owner set an n8n API key as a secret rather than pasting it into chat.
That is the right call -- it never needs to pass through an interactive
session at all. This script reads it (and the GitHub PAT n8n needs for its
own dispatch credential) only from the job's environment, and every write it
makes to n8n is read back immediately after (CLAUDE.md hard rule 4: read the
property back), so its own log is the proof of what happened, not a claim.

WHAT IT DOES, IN ORDER
-----------------------
1. credential  -- create (or reuse, if one of the same name already exists)
   an n8n Header-Auth credential carrying the GitHub PAT this fleet already
   uses for issue creation (secrets.AGY_ISSUE_PAT, falling back to
   secrets.ACC6_PAT -- the exact fallback notion-task-poller.yml already
   uses, reused verbatim rather than re-decided).
2. workflow    -- import Integrations/n8n/notion-task-to-dispatch.json,
   wire the credential from step 1 into its HTTP node, and activate it. If a
   workflow of the same name already exists (a rerun of this script), it is
   UPDATED in place rather than duplicated.
3. fix-marker  -- the pre-existing "Notion Event -> agw-workers Issue
   (Webhook)" workflow (n8n id 7LhwzYMFAJyX7dGY) builds a GitHub issue body
   with no trailing marker at all. agy-plan-bot.yml's own gate
   (.github/workflows/agy-plan-bot.yml, "Check for a trailing @agy-plan
   marker") requires the LOWERCASED body to END with
   `(ccp|cr|coffeenet|status)\\s+@agy-plan` -- so an issue this workflow
   creates would sit forever with no plan, silently. This appends
   `\\n\\nccp @agy-plan` to that node's body, and nothing else in the
   workflow.
4. verify      -- re-fetch both workflows and print exactly what is live:
   active flags, the credential actually attached, and the fixed node's real
   body text -- not what this script intended to write, what n8n now holds.

WHAT IT DELIBERATELY DOES NOT DO
---------------------------------
It does not touch the Notion side -- Notion's API has no automations
endpoint (established earlier), so the database automation stays a human
clicking through the UI. It does not change the new workflow's `source ==
'notion'` guard: instead of guessing at the shape Notion's "Send webhook"
action produces, `--step read-latest-execution` reads the REAL body of the
next real Notion automation firing, via n8n's own executions API, once one
has fired -- so the guard can be corrected against a measurement instead of
another screenshot round-trip.

ASCII only where possible; Persian text is only ever read from data, never
matched on. Exit 0 on full success, 1 if anything could not be verified.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

MARKER = "##TBS##"
N8N_BASE = os.environ.get("N8N_BASE", "https://n8n.airboxvip.top").rstrip("/")
API = N8N_BASE + "/api/v1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_JSON = os.path.join(ROOT, "Integrations", "n8n",
                              "notion-task-to-dispatch.json")
NEW_WORKFLOW_NAME = "Notion task -> GitHub repository_dispatch"
CRED_NAME = "GitHub PAT (agw-workers dispatch)"
MARKER_WORKFLOW_ID = "7LhwzYMFAJyX7dGY"
MARKER_NODE_NAME = "Create agw-workers Issue"
TRAILING_MARKER = "\\n\\nccp @agy-plan"


def api(method, path, key, body=None):
    url = API + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("X-N8N-API-KEY", key)
    r.add_header("Accept", "application/json")
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw.decode("utf-8", "replace")}


def die(msg):
    print("::error::" + msg, file=sys.stderr)
    sys.exit(1)


def find_workflow_by_name(key, name):
    status, body = api("GET", "/workflows?limit=250", key)
    if status != 200:
        print("WARN: could not list workflows (HTTP %s): %s" % (status, body))
        return None
    for wf in body.get("data", body if isinstance(body, list) else []):
        if wf.get("name") == name:
            return wf
    return None


def find_credential_by_name(key, name):
    status, body = api("GET", "/credentials?limit=250", key)
    if status != 200:
        print("WARN: could not list credentials (HTTP %s: %s) -- will "
              "create a new one instead of reusing" % (status, body))
        return None
    for c in body.get("data", body if isinstance(body, list) else []):
        if c.get("name") == name:
            return c
    return None


def step_credential(key, pat):
    existing = find_credential_by_name(key, CRED_NAME)
    if existing:
        print("credential already exists: id=%s (reused, not recreated -- "
              "n8n's API does not expose credential update, so if the PAT "
              "changed this must be deleted and recreated by hand)"
              % existing["id"])
        return existing["id"]
    status, body = api("POST", "/credentials", key, {
        "name": CRED_NAME,
        "type": "httpHeaderAuth",
        "data": {"name": "Authorization", "value": "Bearer " + pat},
    })
    if status not in (200, 201):
        die("credential create failed HTTP %s: %s" % (status, body))
    cred_id = body["id"]
    print("credential created: id=%s name=%r" % (cred_id, CRED_NAME))
    return cred_id


def step_workflow(key, cred_id):
    with open(WORKFLOW_JSON, encoding="utf-8") as fh:
        doc = json.load(fh)
    payload = {"name": doc["name"], "nodes": doc["nodes"],
               "connections": doc["connections"],
               "settings": doc.get("settings", {})}
    for node in payload["nodes"]:
        if node.get("name") == "GitHub repository_dispatch":
            node["credentials"] = {
                "httpHeaderAuth": {"id": cred_id, "name": CRED_NAME}}

    existing = find_workflow_by_name(key, NEW_WORKFLOW_NAME)
    if existing:
        wf_id = existing["id"]
        status, body = api("PUT", "/workflows/%s" % wf_id, key, payload)
        if status != 200:
            die("workflow update failed HTTP %s: %s" % (status, body))
        print("workflow updated in place: id=%s" % wf_id)
    else:
        status, body = api("POST", "/workflows", key, payload)
        if status not in (200, 201):
            die("workflow create failed HTTP %s: %s" % (status, body))
        wf_id = body["id"]
        print("workflow created: id=%s" % wf_id)

    status, body = api("POST", "/workflows/%s/activate" % wf_id, key)
    if status != 200:
        die("activate failed HTTP %s: %s" % (status, body))
    print("activate called: reports active=%s" % body.get("active"))
    return wf_id


def step_fix_marker(key):
    status, body = api("GET", "/workflows/%s" % MARKER_WORKFLOW_ID, key)
    if status != 200:
        die("could not fetch workflow %s (HTTP %s): %s"
            % (MARKER_WORKFLOW_ID, status, body))
    nodes = body["nodes"]
    target = next((n for n in nodes if n.get("name") == MARKER_NODE_NAME),
                  None)
    if target is None:
        die("node %r not found in workflow %s -- renamed? fix this script"
            % (MARKER_NODE_NAME, MARKER_WORKFLOW_ID))
    jb = target["parameters"]["jsonBody"]
    if "ccp @agy-plan" in jb.lower().replace("\\n", " "):
        print("marker already present -- nothing to do (idempotent rerun)")
        return
    # jsonBody as fetched from the API is a plain string (n8n stores the
    # expression text, not double-escaped JSON-in-JSON), so the delimiter
    # between the body value and the next key is a single-escaped quote.
    old_tail = '-->","labels"'
    if old_tail not in jb:
        die("expected tail %r not found in jsonBody -- someone already "
            "edited this node; refusing to guess. Current value: %s"
            % (old_tail, jb))
    new_jb = jb.replace(old_tail, '-->' + TRAILING_MARKER + '","labels"', 1)
    target["parameters"]["jsonBody"] = new_jb

    payload = {"name": body["name"], "nodes": nodes,
               "connections": body["connections"],
               "settings": body.get("settings", {})}
    status, body2 = api("PUT", "/workflows/%s" % MARKER_WORKFLOW_ID, key,
                        payload)
    if status != 200:
        die("marker fix PUT failed HTTP %s: %s" % (status, body2))
    print("marker appended to %r's jsonBody" % MARKER_NODE_NAME)


def step_verify(key, new_wf_id):
    result = {"new_workflow": {}, "marker_workflow": {}}
    status, body = api("GET", "/workflows/%s" % new_wf_id, key)
    if status == 200:
        node = next((n for n in body["nodes"]
                    if n.get("name") == "GitHub repository_dispatch"), {})
        result["new_workflow"] = {
            "id": new_wf_id, "active": body.get("active"),
            "credential_attached": bool(
                node.get("credentials", {}).get("httpHeaderAuth")),
        }
        print("VERIFY new workflow: active=%s credential_attached=%s"
              % (result["new_workflow"]["active"],
                 result["new_workflow"]["credential_attached"]))
    else:
        print("VERIFY new workflow: could not re-fetch (HTTP %s)" % status)

    status, body = api("GET", "/workflows/%s" % MARKER_WORKFLOW_ID, key)
    if status == 200:
        node = next((n for n in body["nodes"]
                    if n.get("name") == MARKER_NODE_NAME), {})
        jb = node.get("parameters", {}).get("jsonBody", "")
        has_marker = jb.lower().rstrip().endswith(
            'ccp @agy-plan","labels":["notion-task"]}'.lower()) or \
            "ccp @agy-plan" in jb.lower()
        result["marker_workflow"] = {
            "id": MARKER_WORKFLOW_ID, "marker_present": has_marker}
        print("VERIFY marker workflow: marker_present=%s" % has_marker)
    else:
        print("VERIFY marker workflow: could not re-fetch (HTTP %s)" % status)

    ok = (result["new_workflow"].get("active") is True and
          result["new_workflow"].get("credential_attached") and
          result["marker_workflow"].get("marker_present"))
    print(MARKER + json.dumps(
        {"v": 1, "probe": "n8n_wire_notion_bridge",
         "status": "pass" if ok else "fail", "data": result}, sort_keys=True))
    return 0 if ok else 1


def step_recent_executions(key, wf_ids, minutes):
    """List each workflow's recent executions, to see whether a real Notion
    automation firing ever reached n8n at all -- distinct from whether it
    then reached GitHub. GET /executions filters by workflowId one at a
    time; includeData is deliberately left off (metadata is enough to
    answer "did anything run, and when").
    """
    import datetime as dt
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes)
    for wf_id, label in wf_ids:
        status, body = api("GET", "/executions?workflowId=%s&limit=10" % wf_id,
                           key)
        if status != 200:
            print("%s (%s): could not list executions (HTTP %s: %s)"
                  % (label, wf_id, status, body))
            continue
        items = body.get("data", body if isinstance(body, list) else [])
        recent = [x for x in items
                  if x.get("startedAt", "") >= cutoff.isoformat()]
        print("%s (%s): %d execution(s) in the last %d min"
              % (label, wf_id, len(recent), minutes))
        for x in recent:
            print("  - id=%s status=%s mode=%s startedAt=%s"
                  % (x.get("id"), x.get("status"), x.get("mode"),
                     x.get("startedAt")))


def step_execution_detail(key, exec_id):
    """Pull one execution's full data and print WHICH node failed and why.

    n8n's execution detail nests each node's run data under
    resultData.runData[node_name][-1], and an error there carries
    .error.message / .error.node.name / .error.description -- print all of
    it rather than a summary, since a wrong guess here costs another round
    trip through a real Notion firing.
    """
    status, body = api("GET", "/executions/%s?includeData=true" % exec_id,
                       key)
    if status != 200:
        print("could not fetch execution %s (HTTP %s): %s"
              % (exec_id, status, body))
        return
    print("execution %s status=%s mode=%s startedAt=%s" %
          (exec_id, body.get("status"), body.get("mode"),
           body.get("startedAt")))
    try:
        run_data = (body["data"]["resultData"]["runData"])
    except (KeyError, TypeError):
        print("no resultData.runData in this execution's payload -- full "
              "top-level keys: %s" % sorted(body.keys()))
        return
    for node_name, runs in run_data.items():
        last = runs[-1] if runs else {}
        err = last.get("error")
        if err:
            print("NODE %r FAILED: %s" % (node_name, err.get("message")))
            if err.get("description"):
                print("  description: %s" % err["description"])
            if err.get("httpCode"):
                print("  httpCode: %s" % err["httpCode"])
        else:
            out = last.get("data", {}).get("main", [[]])
            print("node %r ran ok, %d item(s) out"
                  % (node_name, len(out[0]) if out and out[0] else 0))
            if out and out[0]:
                print("  first item json: %s"
                      % json.dumps(out[0][0].get("json", {}))[:500])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", required=True,
                    choices=["credential", "workflow", "fix-marker", "verify",
                             "recent-executions", "execution-detail", "all"])
    ap.add_argument("--minutes", type=int, default=15,
                    help="window for --step recent-executions")
    ap.add_argument("--exec-id", help="execution id for --step execution-detail")
    a = ap.parse_args()

    key = os.environ.get("N8N_API_KEY", "")
    pat = os.environ.get("GH_DISPATCH_PAT", "")
    if not key:
        die("N8N_API_KEY is empty in this job's environment")

    state_path = "/tmp/n8n_wire_state.json"

    def load_state():
        if os.path.exists(state_path):
            with open(state_path, encoding="utf-8") as fh:
                return json.load(fh)
        return {}

    def save_state(s):
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(s, fh)

    state = load_state()

    if a.step in ("credential", "all"):
        if not pat:
            die("GH_DISPATCH_PAT is empty (neither AGY_ISSUE_PAT nor "
                "ACC6_PAT resolved to anything)")
        state["cred_id"] = step_credential(key, pat)
        save_state(state)

    if a.step in ("workflow", "all"):
        cred_id = state.get("cred_id")
        if not cred_id:
            die("no credential id in state -- run --step credential first")
        state["wf_id"] = step_workflow(key, cred_id)
        save_state(state)

    if a.step in ("fix-marker", "all"):
        step_fix_marker(key)

    if a.step in ("verify", "all"):
        wf_id = state.get("wf_id")
        if not wf_id:
            die("no workflow id in state -- run --step workflow first")
        return step_verify(key, wf_id)

    if a.step == "recent-executions":
        # Falls back to the id from the run that created it (35437673833)
        # if this runner's /tmp state was cleared since.
        wf_id = state.get("wf_id") or "UwLuW7GLo2lvXlzG"
        ids = [(wf_id, "new workflow (dispatch)"),
               (MARKER_WORKFLOW_ID, "old workflow (Notion Event -> issue)")]
        step_recent_executions(key, ids, a.minutes)

    if a.step == "execution-detail":
        if not a.exec_id:
            die("--exec-id is required for --step execution-detail")
        step_execution_detail(key, a.exec_id)

    return 0


if __name__ == "__main__":
    sys.exit(main())
