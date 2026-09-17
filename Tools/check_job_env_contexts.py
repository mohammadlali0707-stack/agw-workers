#!/usr/bin/env python3
"""Fail on a GitHub Actions context used where GitHub does not allow it.

WHY THIS EXISTS
---------------
`${{ runner.temp }}` in a job-level `env:` block is not an error you can see.
GitHub refuses to compile the workflow, so the run has zero jobs, no step log
and nothing to read -- the same signature this fleet already misdiagnosed once
as a YAML syntax error. 74f136f shipped exactly that in four workflow files on
2026-09-17; d99dbc0 fixed one of them; the other three, plus a brand-new file
written the same day, still carried it hours later. Four separate authors could
not see it by reading, which is the definition of something a gate should own.

WHAT IS AND IS NOT ALLOWED
--------------------------
Per GitHub's context-availability table, `jobs.<id>.env`, `jobs.<id>.if`,
`jobs.<id>.runs-on`, `jobs.<id>.timeout-minutes` and `jobs.<id>.concurrency`
may use github, needs, strategy, matrix, vars, secrets and inputs -- but NOT
`runner`, `steps`, `job` or `env`, all of which only exist once a step is
running. The same expressions are perfectly legal one level down, in a step's
own `env:`, which is why the fix is always "move it to the step", never
"stop using runner.temp".

Emits one machine-readable ##TBS## line, per this fleet's convention.
"""
import glob
import json
import os
import re
import sys

import yaml

# Keys under jobs.<id> that are evaluated before any step runs.
PRE_STEP_KEYS = ("env", "if", "runs-on", "timeout-minutes", "concurrency",
                 "continue-on-error", "container", "services")

# Contexts that do not exist yet at that point.
FORBIDDEN = ("runner", "steps", "job", "env", "hashFiles")

EXPR = re.compile(r"\$\{\{(.*?)\}\}", re.S)


def walk_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from walk_strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk_strings(v)


def scan_file(path):
    """Return a list of (job_id, key, context) violations."""
    try:
        doc = yaml.safe_load(open(path, encoding="utf-8"))
    except Exception as exc:                          # noqa: BLE001
        return [("<file>", "<parse>", str(exc)[:120])]
    if not isinstance(doc, dict):
        return []
    out = []
    for jid, job in (doc.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue

        # An `env:` (or `with:`, `outputs:`) key with nothing but comments
        # under it parses as null, and GitHub rejects the workflow for that
        # exactly as it rejects a bad context: zero jobs, no log. This is not
        # hypothetical -- deleting the last key from agw-worker.yml's job env
        # on 2026-09-17 left the file unparseable on all eight forks AFTER the
        # runner-context fix had supposedly cleared it, and the readback in
        # sync_agy_files_to_forks.py passed it because this checker did not
        # look. The fix is to delete the `env:` line too, not to add a filler.
        for key in ("env", "outputs", "defaults", "with"):
            if key in job and job[key] in (None, {}):
                out.append((jid, key, "<null-or-empty>"))

        for key in PRE_STEP_KEYS:
            if key not in job:
                continue
            for text in walk_strings(job[key]):
                for expr in EXPR.findall(text):
                    for ctx in FORBIDDEN:
                        if re.search(rf"(?<![\w.]){re.escape(ctx)}\s*\.", expr):
                            out.append((jid, key, ctx))
    return out


def self_test():
    """The gate must be able to fail. A checker that only ever passes is the
    thing it is supposed to catch."""
    import tempfile
    bad = ("on: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n"
           "    env:\n      P: ${{ runner.temp }}/x\n    steps:\n"
           "      - run: echo hi\n")
    # The second failure mode, and the one that actually shipped: an env: key
    # holding nothing but a comment.
    null_env = ("on: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n"
                "    env:\n      # only a comment lives here\n    steps:\n"
                "      - run: echo hi\n")
    good = ("on: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n"
            "    env:\n      P: ${{ github.run_id }}\n    steps:\n"
            "      - env:\n          Q: ${{ runner.temp }}\n        run: echo hi\n")
    with tempfile.TemporaryDirectory() as d:
        paths = {}
        for name, body in (("bad", bad), ("null_env", null_env), ("good", good)):
            paths[name] = os.path.join(d, f"{name}.yml")
            open(paths[name], "w").write(body)
        return (bool(scan_file(paths["bad"]))
                and bool(scan_file(paths["null_env"]))
                and not scan_file(paths["good"]))


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else ".github/workflows"
    discriminates = self_test()
    files = sorted(glob.glob(os.path.join(root, "*.yml")) +
                   glob.glob(os.path.join(root, "*.yaml")))
    violations = []
    for path in files:
        for jid, key, ctx in scan_file(path):
            violations.append({"file": path, "job": jid, "key": key, "context": ctx})
            print(f"::error file={path}::job '{jid}' uses the '{ctx}' context in "
                  f"'{key}', which GitHub evaluates before any step runs. The "
                  f"workflow will not start: zero jobs, no log. Move it to a "
                  f"step-level env: block, or write it to $GITHUB_ENV in a first step.")

    if not discriminates:
        print("::error::self-test failed -- this checker no longer distinguishes a "
              "bad file from a good one, so a pass from it means nothing.")

    ok = not violations and discriminates and files
    print(f"{len(files)} workflow file(s) scanned, {len(violations)} violation(s), "
          f"discriminates={discriminates}")
    print('##TBS##' + json.dumps(
        {"data": {"files": len(files), "violations": violations,
                  "discriminates": discriminates},
         "probe": "job_env_contexts", "status": "pass" if ok else "fail", "v": 1},
        sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
