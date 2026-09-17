#!/usr/bin/env python3
"""Fail when a NEW untrusted expression is interpolated into a `run:` body.

WHAT THIS REPLACES, AND WHY
---------------------------
Gate 1's CHECK-1 forbade double quotes inside single-quoted `printf` content.
Its rationale, from the original CR/Tools/check_workflow_safety.py, was real:

    run: |
      PROMPT="${{ inputs.prompt }}"

GitHub pastes that expression into the SCRIPT TEXT before bash runs, so a quote
in the value ends the string -- a parse error at best, injection at worst.

But agw-worker.yml stopped doing that. It passes the value through `env:`
(PROMPT_INPUT: ${{ inputs.prompt }}) and reads `$PROMPT_INPUT`, where the value
is data and quotes are inert. So CHECK-1 was guarding a path that no longer
exists, while failing the build over double quotes in deliberately-JSON printf
templates in agy-plan-bot.yml -- correct code it had no business rejecting.

THE REAL RULE
-------------
Flag `${{ inputs.* }}` and `${{ github.event.* }}` inside a `run:` body. That
is the injection vector itself, not a proxy for it, and the fix is always the
same: move it to a step-level `env:` and reference the variable.

WHY A BASELINE INSTEAD OF A HARD FAIL
-------------------------------------
There are 66 of these already, across 12 files. Turning that into a red build
today would replace one ignored gate with another -- which is exactly the
lesson CHECK-2 just taught: a gate that cries wolf on existing code gets
ignored, and then it is not protecting anything.

So the known set is recorded in injection_baseline.json and reported as
informational, and only something NEW fails. The count cannot quietly grow, a
fix shrinks the baseline, and the backlog can be paid down deliberately rather
than in one blocking lump. Run with --update to re-record after fixing some.
"""
import argparse
import glob
import json
import os
import re
import sys

import yaml

UNTRUSTED = re.compile(r"\$\{\{\s*(inputs\.[A-Za-z0-9_.-]+|github\.event\.[A-Za-z0-9_.-]+)")
HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE = os.path.join(HERE, "injection_baseline.json")


def scan(root=".github/workflows"):
    """-> {"<file>::<expression>": count}. Keyed by WHAT and WHERE, not by line,
    so re-indenting a file does not look like a new finding."""
    found = {}
    for path in sorted(glob.glob(os.path.join(root, "*.yml")) +
                       glob.glob(os.path.join(root, "*.yaml"))):
        try:
            doc = yaml.safe_load(open(path, encoding="utf-8"))
        except Exception:                                    # noqa: BLE001
            continue                                         # CHECK-3 owns this
        if not isinstance(doc, dict):
            continue
        for job in (doc.get("jobs") or {}).values():
            if not isinstance(job, dict):
                continue
            for step in job.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                run = step.get("run")
                if not isinstance(run, str):
                    continue
                for m in UNTRUSTED.finditer(run):
                    key = f"{os.path.basename(path)}::{m.group(1)}"
                    found[key] = found.get(key, 0) + 1
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=".github/workflows")
    ap.add_argument("--update", action="store_true",
                    help="re-record the baseline after fixing some")
    args = ap.parse_args()

    found = scan(args.root)
    if args.update:
        with open(BASELINE, "w", encoding="utf-8") as fh:
            json.dump(found, fh, indent=2, sort_keys=True)
            fh.write("\n")
        print(f"baseline re-recorded: {len(found)} known location(s)")
        return 0

    try:
        base = json.load(open(BASELINE, encoding="utf-8"))
    except (OSError, ValueError):
        print("::error::injection_baseline.json is missing or unreadable. "
              "Regenerate it with: python3 Tools/check_run_injection.py --update",
              file=sys.stderr)
        return 1

    new = {k: v for k, v in found.items() if k not in base}
    grown = {k: (base[k], v) for k, v in found.items()
             if k in base and v > base[k]}
    fixed = sorted(set(base) - set(found))

    for k in sorted(new):
        f, _, expr = k.partition("::")
        print(f"::error file=.github/workflows/{f}::NEW untrusted expression in a "
              f"run: body -- {expr}. GitHub pastes this into the script text before "
              f"bash runs it. Pass it through a step-level env: and use the variable "
              f"instead.")
    for k, (was, now) in sorted(grown.items()):
        print(f"::error::{k} went from {was} to {now} occurrence(s)")
    if fixed:
        print(f"{len(fixed)} baseline entr(y/ies) no longer present -- run "
              f"--update to shrink the baseline: {', '.join(fixed[:4])}"
              + (" ..." if len(fixed) > 4 else ""))

    total = sum(found.values())
    ok = not new and not grown
    print(f"{len(found)} location(s), {total} occurrence(s); "
          f"{len(new)} new, {len(grown)} grown, {len(fixed)} fixed")
    print('##TBS##' + json.dumps(
        {"data": {"locations": len(found), "occurrences": total,
                  "new": sorted(new), "grown": sorted(grown),
                  "fixed": len(fixed)},
         "probe": "run_injection", "status": "pass" if ok else "fail", "v": 1},
        sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
