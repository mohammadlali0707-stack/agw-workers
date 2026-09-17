#!/usr/bin/env python3
"""Turn a ticked Notion Task Board row into a GitHub issue, without n8n.

Runs on a schedule from .github/workflows/notion-task-poller.yml. Polls the
Task Board for rows with Plan checked and no Github Issue URL yet, opens the
issue in agw-workers with the marker agy-plan-bot expects, and writes the
issue URL back into the row.

WHY THIS EXISTS ALONGSIDE n8n
-----------------------------
Measured 2026-09-17: four Notion tasks with Plan ticked (one with Project set
from creation) produced zero GitHub issues, and NO issue in the repo's whole
history carries a real Notion page UUID -- every one holds a synthetic id
(`test-rmrf-051835`, `e2e-test-...`). The Notion -> n8n leg has apparently
never delivered a real event; what was verified was everything downstream of
it, fed by hand-made payloads. This path needs no webhook registration, logs
into Actions where it can be read, and runs on infrastructure already proven
today.

THE SHARED LOCK, AND ITS LIMIT
------------------------------
The owner chose to keep n8n running in parallel. Both sides treat a non-empty
`Github Issue URL` as "already handled", so whichever gets there first wins
and the other skips. That is a check-then-act race, not a real lock: if n8n
and this poller read the same empty field within the same instant, both will
open an issue. The window is small and the cost is a duplicate issue, not a
corrupted state -- but it is real, and the honest fix if it ever bites is to
retire one of the two paths rather than to add a second guess here.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

NOTION_VERSION = "2022-06-28"
NOTION_API = "https://api.notion.com/v1"
GITHUB_API = "https://api.github.com"

# Notion Project select value -> the tag agy-plan-bot's marker check expects.
#
# These four tags are not a convention I chose: they are the literal alternation
# in agy-plan-bot.yml's own check-marker step,
#   grep -oE '(ccp|cr|coffeenet|status)[[:space:]]+@agy-plan$'
# and the same four in its resolve step's case. A tag outside that set does not
# fail -- it falls through to `*)`, which resolves to Claud-Cloud-Project. An
# earlier draft of this file mapped AirboxVIP to "airbox", which would have
# silently planned every Coffeenet task against CCP instead. Read the target's
# real matcher before inventing a value for it.
#
# Keys are matched case-insensitively with surrounding whitespace stripped, so
# the Notion select can read "CCP", "ccp" or "Control Room" interchangeably.
PROJECT_TAG = {
    "ccp": "ccp",
    "claud cloud project": "ccp",
    "claud-cloud-project": "ccp",
    "cr": "cr",
    "control room": "cr",
    "control-room": "cr",
    "coffeenet": "coffeenet",
    "airboxvip": "coffeenet",
    "airboxvip_coffeenet": "coffeenet",
    "status": "status",
    "status-dashboard": "status",
    "status dashboard": "status",
}

# agy-plan-bot's `propose` job runs only when the issue's author_association is
# OWNER or COLLABORATOR, or its login is 'Mohammadlali'. GITHUB_TOKEN authors as
# github-actions[bot], which is neither -- an issue it opens gets the label, the
# marker and no plan at all, failing silently. Every issue that DID drive this
# pipeline (agw-workers #25-#30, measured 2026-09-17) was authored by
# mohammadlali0707-stack, so the poller checks who its token is before it writes.
EXPECTED_AUTHOR = os.environ.get("ISSUE_AUTHOR", "mohammadlali0707-stack")

_last = [0.0]


def _req(method, url, token, body=None, notion=True, _tries=0):
    gap = time.time() - _last[0]
    if gap < 0.34:
        time.sleep(0.34 - gap)
    _last[0] = time.time()
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if notion:
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Notion-Version", NOTION_VERSION)
    else:
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        if e.code == 429 and _tries < 5:
            time.sleep(float(e.headers.get("Retry-After", 2)))
            return _req(method, url, token, body, notion, _tries + 1)
        raise RuntimeError(f"{method} {url} -> {e.code}: {detail[:400]}") from None


def plain(rich):
    return "".join(x.get("plain_text", "") for x in rich or [])


def page_text(token, page_id):
    """The task's body, flattened. Skips nested children: a task description is
    expected to be a few paragraphs, and recursing risks pulling in far more
    than belongs in an issue body."""
    out, cursor = [], None
    while True:
        url = f"{NOTION_API}/blocks/{page_id}/children?page_size=100"
        if cursor:
            url += f"&start_cursor={cursor}"
        res = _req("GET", url, token)
        for b in res.get("results", []):
            t = b.get("type")
            payload = b.get(t, {})
            if isinstance(payload, dict) and "rich_text" in payload:
                line = plain(payload["rich_text"])
                if line.strip():
                    out.append(line)
        if not res.get("has_more"):
            break
        cursor = res["next_cursor"]
    return "\n\n".join(out)


def check_identity(gtoken):
    """Return the login this token authors as, or raise. See EXPECTED_AUTHOR:
    the wrong identity does not error anywhere -- it just produces issues no
    plan ever answers."""
    me = _req("GET", f"{GITHUB_API}/user", gtoken, notion=False)
    return me.get("login", "")


def find_candidates(token, db_id):
    body = {
        "filter": {"and": [
            {"property": "Plan", "checkbox": {"equals": True}},
            {"property": "Github Issue URL", "select": {"is_empty": True}},
        ]},
        "page_size": 25,
    }
    res = _req("POST", f"{NOTION_API}/databases/{db_id}/query", token, body)
    return res.get("results", [])


def main():
    ntoken = os.environ.get("NOTION_TOKEN", "")
    db_id = os.environ.get("NOTION_TASKS_DB_ID", "")
    gtoken = os.environ.get("GH_TOKEN", "")
    repo = os.environ.get("ISSUE_REPO", "mohammadlali0707-stack/agw-workers")
    dry = os.environ.get("DRY_RUN", "") == "1"

    if not ntoken or not db_id or (not gtoken and not dry):
        print("NOTION_TOKEN, NOTION_TASKS_DB_ID and GH_TOKEN must be set",
              file=sys.stderr)
        return 1

    rows = find_candidates(ntoken, db_id)
    stats = {"found": len(rows), "created": 0, "skipped_no_project": 0, "failed": 0}
    print(f"{len(rows)} task(s) with Plan ticked and no issue yet")

    if rows and not dry:
        who = check_identity(gtoken)
        if who != EXPECTED_AUTHOR:
            print(f"::error::this token authors as {who!r}, not {EXPECTED_AUTHOR!r}; "
                  "agy-plan-bot would ignore every issue it opens. Refusing to "
                  "create any.", file=sys.stderr)
            print('##TBS##' + json.dumps(
                {"data": {"author": who, "expected": EXPECTED_AUTHOR},
                 "probe": "notion_task_to_issue", "status": "fail", "v": 1},
                sort_keys=True))
            return 1
        print(f"token authors as {who}, which agy-plan-bot accepts")

    for row in rows:
        pid = row["id"]
        props = row.get("properties", {})
        name = plain(props.get("Name", {}).get("title", [])) or "(untitled task)"
        project = (props.get("Project", {}).get("select") or {}).get("name", "")
        tag = PROJECT_TAG.get(project.strip().lower(), "")
        if not tag:
            # Without a tag the trailing marker would read " @agy-plan" and the
            # bot could not route it. Say so rather than opening a broken issue.
            print(f"  SKIP '{name}': Project={project!r} maps to no tag "
                  f"(known: {', '.join(sorted(set(PROJECT_TAG.values())))})")
            stats["skipped_no_project"] += 1
            continue

        desc = page_text(ntoken, pid) if not dry else "(dry run: body not fetched)"
        body = (
            f"{desc}\n\n"
            f"<!-- notion-page-id: {pid} -->\n\n"
            f"{tag} @agy-plan"
        )
        print(f"  -> issue for '{name}' (project={project}, tag={tag})")
        if dry:
            print("     DRY RUN, not creating. Body would end:",
                  repr(body[-40:]))
            continue

        try:
            issue = _req("POST", f"{GITHUB_API}/repos/{repo}/issues", gtoken,
                         {"title": name, "body": body, "labels": ["notion-task"]},
                         notion=False)
            url = issue["html_url"]
            # Write back immediately: this field is the lock n8n also honours,
            # so the gap between creating and recording it is the race window.
            _req("PATCH", f"{NOTION_API}/pages/{pid}", ntoken,
                 {"properties": {"Github Issue URL": {"select": {"name": url}}}})
            print(f"     created {url} and recorded it on the task")
            stats["created"] += 1
        except Exception as e:                        # noqa: BLE001
            stats["failed"] += 1
            print(f"     FAILED: {str(e)[:300]}", file=sys.stderr)

    status = "fail" if stats["failed"] else "pass"
    print('##TBS##' + json.dumps(
        {"data": stats, "probe": "notion_task_to_issue", "status": status, "v": 1},
        sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
