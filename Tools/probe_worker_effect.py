#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# probe_worker_effect -- prove a worker that wrote nothing to the repo cannot look identical to one that did.
"""Falsification suite for agw-worker.yml's "did this worker change anything"
contract.

THE INCIDENT THIS EXISTS FOR
----------------------------
CCP-307 ("Scaffold first Ren'Py slice", issue #38; report
Claud-Cloud-Project#44). Accounts 4 and 5 reported building `gui.rpy`,
`screens.rpy` and 31 GUI assets, with pixel-level RTL validation. Measured
afterwards on the repository: **zero .rpy files**, and `game/` holding nine
tracked files -- seven `.gitkeep` placeholders and two fonts.

The work was real; it was written into the agent CLI's own scratch directory
(`/home/runner/.gemini/antigravity-cli/scratch/renpy_project/`, as report #44's
own file links show) instead of the checkout. Three things let that pass:

  1. Nothing TOLD the agent where to write. The step `cd`s into the checkout,
     but a working directory is not an instruction, and the CLI has a scratch
     directory of its own that it prefers.
  2. The push step pushes only what is COMMITTED inside the checkout. It never
     staged the agent's files, so an uncommitted edit was dropped in silence.
  3. Nothing ever asked whether the worker changed a file. `git push` with
     nothing to push exits 0, so "built 31 assets in scratch" and "built 31
     assets in the repo" produced the same green job and the same report.

(3) is the one that matters: it is CLAUDE.md's stencil again -- the check had
no way to fail. The earlier w08 fix covered EMPTY STDOUT; this failure has
plenty of stdout and an empty diff.

WHAT IS PINNED HERE
-------------------
The three mechanisms, read out of the workflow: the prompt preamble, the
rescue-commit of uncommitted work, and the commits/files measurement that gets
stamped into the report file. Plus the two silent-`cd` swallows, because a
`cd` that fails into `|| true` is the same bug one layer down.

ASCII only. Exit 0 pass, 1 fail.
"""

import io
import json
import os
import re
import subprocess
import sys
import tempfile

MARKER = "##TBS##"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(ROOT, ".github", "workflows", "agw-worker.yml")


def read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def git(cwd, *args):
    return subprocess.run(("git",) + args, cwd=cwd, capture_output=True,
                          text=True)


def measure(repo, base):
    """The workflow's own two measurements, run against a real repository.

    Reimplementing the git plumbing is safe in a way reimplementing the clamp
    would not be: these are one-line git commands whose CORRECTNESS is what is
    in question, not their wording. The probe asserts they distinguish the two
    cases the incident could not.
    """
    commits = git(repo, "rev-list", "--count", "%s..HEAD" % base).stdout.strip()
    files = git(repo, "diff", "--name-only", "%s..HEAD" % base).stdout
    return int(commits or 0), len([l for l in files.splitlines() if l.strip()])


def scenario(kind):
    """Build a repo in one of the three states a worker can leave behind."""
    tmp = tempfile.mkdtemp()
    git(tmp, "init", "-q")
    git(tmp, "config", "user.email", "probe@example.invalid")
    git(tmp, "config", "user.name", "probe")
    with io.open(os.path.join(tmp, "README.md"), "w") as fh:
        fh.write("base\n")
    git(tmp, "add", "-A")
    git(tmp, "commit", "-q", "-m", "base")
    base = git(tmp, "rev-parse", "HEAD").stdout.strip()

    if kind == "committed":
        with io.open(os.path.join(tmp, "script.rpy"), "w") as fh:
            fh.write('label start:\n    "hello"\n')
        git(tmp, "add", "-A")
        git(tmp, "commit", "-q", "-m", "agent work")
    elif kind == "uncommitted":
        # The agent wrote into the checkout but never committed.
        with io.open(os.path.join(tmp, "script.rpy"), "w") as fh:
            fh.write('label start:\n    "hello"\n')
    elif kind == "scratch":
        # The CCP-307 failure: the agent wrote somewhere else entirely.
        other = tempfile.mkdtemp()
        with io.open(os.path.join(other, "script.rpy"), "w") as fh:
            fh.write('label start:\n    "hello"\n')
    return tmp, base


def rescue(repo):
    """What the workflow's rescue block does, exercised for real."""
    dirty = git(repo, "status", "--porcelain").stdout.strip()
    if dirty:
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "agy: work left uncommitted")
    return bool(dirty)


def main():
    failures = []
    wf = read(WF)

    # --- the three mechanisms must be present in the workflow -------------
    checks = [
        ("prompt names the checkout",
         "WHERE TO PUT FILES" in wf),
        # Anchored on a phrase unique to the preamble. The first draft of this
        # check looked for "silently lost", which ALREADY appeared at line 453
        # in an unrelated comment about push retries -- so it passed against
        # the pre-fix workflow and was the one check here that could not fail.
        # Found by running this probe against the old file and counting which
        # checks fired: 7 of 8, not 8 of 8.
        ("prompt warns that outside work is lost",
         "NOTHING outside the checkout is collected" in wf),
        ("prompt asks for a commit",
         "git add + git commit inside the checkout" in wf),
        ("baseline commit is recorded before agy runs",
         "AGY_BASE_COMMIT=" in wf),
        ("uncommitted work is rescued, not dropped",
         "git status --porcelain" in wf and "left uncommitted" in wf),
        ("the effect measurement exists",
         "AGENT_COMMITS=" in wf and "AGENT_FILES=" in wf),
        ("the verdict reaches the report file",
         "agw_worker_effect" in wf),
        ("a no-effect worker is announced",
         "WORKER CHANGED NO FILE IN THE REPOSITORY." in wf),
    ]
    for label, ok in checks:
        if not ok:
            failures.append("workflow no longer guarantees: %s" % label)

    # A `cd` into the checkout that can fail silently is the same class of bug
    # the prompt preamble warns the agent about.
    swallowed = len(re.findall(r'cd "\$\{AGENT_REPO\}" 2>/dev/null \|\| true', wf))
    if swallowed:
        failures.append(
            "%d silent `cd \"${AGENT_REPO}\" || true` left -- a failed cd runs "
            "the agent in the wrong directory and reports success" % swallowed)

    # --- and the measurement must actually discriminate -------------------
    # This is the half that matters. A check present in the file but unable to
    # tell the three cases apart is the stencil all over again.
    seen = {}
    for kind in ("committed", "uncommitted", "scratch"):
        repo, base = scenario(kind)
        rescued = rescue(repo)
        commits, files = measure(repo, base)
        seen[kind] = (commits, files, rescued)

    if seen["committed"][0] < 1 or seen["committed"][1] < 1:
        failures.append("a worker that COMMITTED work measured as %r -- the "
                        "measurement misses real work" % (seen["committed"],))
    if seen["uncommitted"][0] < 1:
        failures.append("a worker that wrote into the checkout without "
                        "committing measured as %r -- the rescue did not fire, "
                        "so that work is still dropped" % (seen["uncommitted"],))
    if not seen["uncommitted"][2]:
        failures.append("the rescue reported nothing to rescue when the "
                        "checkout was dirty")
    if seen["scratch"][0] != 0 or seen["scratch"][1] != 0:
        failures.append("a worker that wrote OUTSIDE the checkout measured as "
                        "%r, not (0, 0) -- then the check cannot detect the "
                        "CCP-307 failure at all" % (seen["scratch"][:2],))
    # The whole point: the two outcomes must differ.
    if seen["committed"][:2] == seen["scratch"][:2]:
        failures.append("work-in-repo and work-in-scratch measure IDENTICALLY "
                        "-- this is exactly the blindness that lost the "
                        "Ren'Py slice")

    status = "fail" if failures else "pass"
    if failures:
        print("FAIL: %d finding(s):" % len(failures))
        for f in failures:
            print("  - %s" % f)
    else:
        print("PASS: the agent is told where to write, uncommitted work is "
              "rescued rather than dropped, and a worker that wrote outside "
              "the checkout measures (0 commits, 0 files) where a real one "
              "does not -- the two are no longer the same green job.")
    print(MARKER + json.dumps(
        {"v": 1, "probe": "worker_effect", "status": status,
         "data": {"failures": failures,
                  "measured": {k: list(v) for k, v in seen.items()}}},
        sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
