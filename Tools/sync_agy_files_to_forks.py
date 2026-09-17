#!/usr/bin/env python3
"""Push the control repo's AGY workflow files to every account's fork, and
READ EACH ONE BACK to prove the push took.

WHY A READBACK AND NOT A 200
----------------------------
The Contents API answering 200 means the commit was written, not that the file
is what you think. This fleet has now twice believed a sync had happened when
it had not: once on TBS_BRANCH, and once on 2026-09-17 when audit-fleet-sync.py
reported all 8 accounts SYNCED while every one of them carried a workflow
GitHub could not even compile -- because the marker it grepped for,
`AGENT_REPO:`, is the BROKEN form, not the fixed one. A probe that cannot fail
the way the thing actually breaks is not a probe.

So every file pushed here is fetched back and checked with the same rule
Tools/check_job_env_contexts.py enforces locally: no `runner`/`steps`/`job`/
`env` context in a job-level key GitHub evaluates before any step runs. That is
the failure this sync exists to clear, so that is what gets verified.

WHY ALL FOUR FILES AND ALL NINE ACCOUNTS
----------------------------------------
sync-agw-worker.yml pushes one file to accounts 1-8. sync-lead-plan-oneoff.yml
pushes a different single file to seven accounts -- account 0 is in neither.
The 2026-09-17 bug was in four files at once, so a per-file, partial-fleet tool
is how three of them stayed broken on every fork for hours.
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_job_env_contexts import scan_file  # noqa: E402

API = "https://api.github.com"

FILES = [
    ".github/workflows/agw-worker.yml",
    ".github/workflows/agy-lead-plan.yml",
    ".github/workflows/agy-plan-bot.yml",
    ".github/workflows/agy-final-report.yml",
    # Not a workflow, but agy-lead-plan and agw-worker now call it, and a fork
    # that has the workflow without the tool fails at the step that runs it.
    "Tools/agy_checklist.py",
]

# index -> owner login. Account 6 is the control repo itself (the source of
# this push), so it is deliberately absent.
ACCOUNTS = {
    0: "Mohammadlali",
    1: "momonakikugava-pixel",
    2: "lali94m-max",
    3: "ngocgminh5-debug",
    4: "hmmletssee7-design",
    5: "kidding602",
    7: "mohammad97okk",
    8: "moradzahra85-png",
}

# agy-plan-bot.yml and agy-final-report.yml run only on the control repo. They
# are still pushed so a fork never serves a file GitHub refuses to parse, but a
# fork that has never had them is not a failure.
CONTROL_ONLY = {".github/workflows/agy-plan-bot.yml",
                ".github/workflows/agy-final-report.yml"}


def req(method, url, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", f"Bearer {token}")
    r.add_header("Accept", "application/vnd.github+json")
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw.strip() else {}, resp.status
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode()), e.code
        except Exception:                                   # noqa: BLE001
            return {}, e.code


def sync_one(repo, token, path, local_bytes, results):
    key = f"{repo}:{path}"
    meta, code = req("GET", f"{API}/repos/{repo}/contents/{path}", token)
    sha = meta.get("sha") if code == 200 else None
    if sha is None and path in CONTROL_ONLY:
        results[key] = "ABSENT_BY_DESIGN"
        return True

    body = {"message": "fix: sync AGY workflow files (runner context out of job env)",
            "content": base64.b64encode(local_bytes).decode()}
    if sha:
        body["sha"] = sha
    _, code = req("PUT", f"{API}/repos/{repo}/contents/{path}", token, body)
    if code not in (200, 201):
        results[key] = f"PUSH_FAILED_{code}"
        return False

    # Read it back. This is the whole point.
    back, code = req("GET", f"{API}/repos/{repo}/contents/{path}", token)
    if code != 200:
        results[key] = f"READBACK_FAILED_{code}"
        return False
    got = base64.b64decode(back.get("content", "")).replace(b"\r\n", b"\n")
    if got != local_bytes.replace(b"\r\n", b"\n"):
        results[key] = "READBACK_DIFFERS"
        return False

    # The context/null-env rule is a WORKFLOW rule; running it on a .py file
    # would just report a YAML parse error and call the sync broken.
    if path.endswith((".yml", ".yaml")):
        tmp = f"/tmp/readback_{abs(hash(key))}.yml"
        with open(tmp, "wb") as fh:
            fh.write(got)
        bad = scan_file(tmp)
        os.unlink(tmp)
        if bad:
            results[key] = f"STILL_BROKEN:{bad[0][2]}_in_{bad[0][1]}"
            return False
    results[key] = "OK"
    return True


def main():
    results, failed = {}, 0
    local = {}
    for path in FILES:
        with open(path, "rb") as fh:
            local[path] = fh.read()
        if path.endswith((".yml", ".yaml")) and scan_file(path):
            print(f"::error::{path} is broken HERE; refusing to push it to 8 forks",
                  file=sys.stderr)
            return 1

    for idx, owner in sorted(ACCOUNTS.items()):
        token = os.environ.get(f"ACC{idx}_PAT", "")
        repo = f"{owner}/agw-workers"
        if not token:
            print(f"=== account {idx} ({repo}): NO_PAT, skipped")
            results[repo] = "NO_PAT"
            failed += 1
            continue
        print(f"=== account {idx} ({repo})")
        for path in FILES:
            ok = sync_one(repo, token, path, local[path], results)
            print(f"    {path}: {results[f'{repo}:{path}']}")
            if not ok:
                failed += 1

    print(f"\n{len(ACCOUNTS)} account(s), {len(FILES)} file(s), {failed} problem(s)")
    print('##TBS##' + json.dumps(
        {"data": {"accounts": len(ACCOUNTS), "files": len(FILES),
                  "failed": failed, "results": results},
         "probe": "sync_agy_files_to_forks",
         "status": "fail" if failed else "pass", "v": 1}, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
