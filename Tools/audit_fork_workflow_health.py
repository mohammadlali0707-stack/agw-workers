#!/usr/bin/env python3
"""Ask every fork whether GitHub can actually PARSE its AGY workflows.

THE SIGNAL, AND WHY IT IS THE RIGHT ONE
---------------------------------------
A workflow GitHub cannot compile has no jobs, no log and nothing to read -- but
it does have a name, and that name is the FILE PATH instead of the `name:`
inside the file, because GitHub never got far enough to read it. That is how
the owner spotted, twice in one day, that a file was still broken after it had
been declared fixed: the Actions list showed `.github/workflows/agw-worker.yml`
where it should have shown `AGW Worker`.

So this asks for exactly that. One call per account per file:
    GET /repos/{owner}/agw-workers/actions/workflows
and compares each workflow's registered `name` against its `path`. Equal means
GitHub is serving a file it could not parse.

WHY THIS EXISTS ALONGSIDE THE CONTENT AUDIT
-------------------------------------------
audit-fleet-sync.yml greps the file's TEXT, and sync_agy_files_to_forks.py
re-runs our own rule on it. Both check what we know to look for -- and both
passed while every fork was unstartable, because on 2026-09-17 the file had a
second defect nobody had thought to grep for (a job-level `env:` left holding
only comments, which parses as null). This asks GITHUB, which does not need to
be told what the defects are.

Read-only: no dispatch, no push, no side effects.
"""
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"

ACCOUNTS = {
    0: "Mohammadlali",
    1: "momonakikugava-pixel",
    2: "lali94m-max",
    3: "ngocgminh5-debug",
    4: "hmmletssee7-design",
    5: "kidding602",
    6: "mohammadlali0707-stack",
    7: "mohammad97okk",
    8: "moradzahra85-png",
}

# Files whose health we care about. agy-plan-bot/agy-final-report are
# control-repo-only, so their absence on a fork is not a finding.
WATCHED = {
    ".github/workflows/agw-worker.yml": "AGW Worker",
    ".github/workflows/agy-lead-plan.yml": None,      # name checked, not fixed
}


def get(url, token):
    r = urllib.request.Request(url, method="GET")
    r.add_header("Authorization", f"Bearer {token}")
    r.add_header("Accept", "application/vnd.github+json")
    try:
        with urllib.request.urlopen(r, timeout=45) as resp:
            return json.loads(resp.read().decode()), resp.status
    except urllib.error.HTTPError as e:
        return {}, e.code


def main():
    findings, broken, unchecked = {}, 0, 0

    for idx, owner in sorted(ACCOUNTS.items()):
        token = os.environ.get(f"ACC{idx}_PAT", "")
        repo = f"{owner}/agw-workers"
        print(f"\n=== account {idx} ({repo})")
        if not token:
            print("  NO_PAT -- cannot ask this account")
            findings[repo] = "NO_PAT"
            unchecked += 1
            continue

        data, code = get(f"{API}/repos/{repo}/actions/workflows?per_page=100", token)
        if code != 200:
            print(f"  API_{code} -- cannot list workflows")
            findings[repo] = f"API_{code}"
            unchecked += 1
            continue

        by_path = {w.get("path"): w for w in data.get("workflows", [])}
        repo_state = {}
        for path in WATCHED:
            wf = by_path.get(path)
            if not wf:
                print(f"  {path}: absent")
                repo_state[path] = "ABSENT"
                continue
            name, state = wf.get("name", ""), wf.get("state", "")
            # The tell: GitHub falls back to the path when it could not parse.
            if name == path:
                print(f"  {path}: UNPARSEABLE -- GitHub registered it by path, "
                      f"so it never read its name: (state={state})")
                repo_state[path] = "UNPARSEABLE"
                broken += 1
            else:
                print(f"  {path}: ok -- registered as {name!r} (state={state})")
                repo_state[path] = f"OK:{name}"
        findings[repo] = repo_state

    total = len(ACCOUNTS)
    print(f"\n{total} account(s) asked, {broken} unparseable file(s), "
          f"{unchecked} account(s) not reachable")
    ok = broken == 0 and unchecked == 0
    print('##TBS##' + json.dumps(
        {"data": {"accounts": total, "unparseable": broken,
                  "unreachable": unchecked, "findings": findings},
         "probe": "fork_workflow_health",
         "status": "pass" if ok else "fail", "v": 1}, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
