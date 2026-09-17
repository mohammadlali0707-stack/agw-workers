#!/usr/bin/env python3
"""Offline probe for Tools/agy_checklist.py against a fake GitHub issue.

The property under test is CONVERGENCE: the checklist body must be a pure
function of the roster plus the stamps on the issue, so that two refreshes
racing cannot lose a result. The previous tick-in-place design failed exactly
this and passed its own weaker check, which is why the check changed.
"""
import importlib.util
import os
import json
import os
import sys
import tempfile

spec = importlib.util.spec_from_file_location(
    "cl", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "agy_checklist.py"))
cl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cl)

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{' -- ' + detail if detail else ''}")
    if not cond:
        fails.append(name)


class Fake:
    def __init__(self):
        self.comments = []
        self.next_id = 1
        self.rival = None          # a body written by a rival mid-flight
        self.patches = 0

    def __call__(self, method, url, token, body=None):
        if method == "GET":
            page = int(url.split("&page=")[1])
            return self.comments if page == 1 else []
        if method == "POST":
            self.comments.append({"id": self.next_id, "body": body["body"]})
            self.next_id += 1
            return self.comments[-1]
        if method == "PATCH":
            cid = int(url.rstrip("/").split("/")[-1])
            if self.rival is not None:
                self.comments.append({"id": 900, "body": self.rival})
                self.rival = None
            for c in self.comments:
                if c["id"] == cid:
                    c["body"] = body["body"]
            self.patches += 1
            return {}
        raise AssertionError(f"unexpected {method} {url}")


MARKER = "<!-- agy-checklist:plan-33-acc3 -->"


class A:
    repo, issue, marker, title = "o/r", "33", MARKER, "نقشه‌ی کار"


def stamp(fake, tag, state):
    fake.comments.append(
        {"id": fake.next_id,
         "body": f"agy output here\n\n<!-- agy-task: {tag} state={state} -->"})
    fake.next_id += 1


def fresh(n=3):
    fake = Fake()
    cl.req = fake
    tsv = tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False,
                                      encoding="utf-8")
    for i in range(2, 2 + n):
        tsv.write(f"plan-33-acc3-w{i:02d}\tTask {i}\tagw-ACC3-w{i:02d}\n")
    tsv.close()
    a = A()
    a.tasks = tsv.name
    cl.cmd_post(a, "tok")
    return fake, a


def board(fake):
    return next(c["body"] for c in fake.comments if MARKER in c["body"])


print("-- post --")
fake, a = fresh()
b = board(fake)
check("one comment posted", len(fake.comments) == 1)
check("three unchecked boxes", b.count("- [ ] ") == 3, str(b.count("- [ ] ")))
check("roster payload embedded", "<!-- agy-roster: " in b)
check("counter starts at 0/3", "— 0/3" in b, b.split("\n")[3])

print("\n-- a subordinate finishes: refresh reflects it --")
stamp(fake, "plan-33-acc3-w03", "done")
cl.cmd_refresh(a, "tok")
b = board(fake)
check("w03 ticked", "- [x] `plan-33-acc3-w03`" in b)
check("w02 still open", "- [ ] `plan-33-acc3-w02`" in b)
check("counter now 1/3", "— 1/3" in b)
check("task title survived the rewrite", "**Task 3** — agw-ACC3-w03" in b)

print("\n-- a failed subordinate is crossed, not ticked --")
stamp(fake, "plan-33-acc3-w02", "failed")
cl.cmd_refresh(a, "tok")
b = board(fake)
check("w02 crossed", "- [~] `plan-33-acc3-w02`" in b)
check("counter still 1/3 (failed is not done)", "— 1/3" in b)

print("\n-- a retry that succeeds must clear the cross --")
stamp(fake, "plan-33-acc3-w02", "done")
cl.cmd_refresh(a, "tok")
check("w02 now ticked", "- [x] `plan-33-acc3-w02`" in board(fake))

print("\n-- THE RACE: a rival result lands mid-refresh; nothing may be lost --")
fake, a = fresh()
stamp(fake, "plan-33-acc3-w02", "done")
# While we are refreshing, w04 posts its result comment.
fake.rival = "other output\n\n<!-- agy-task: plan-33-acc3-w04 state=done -->"
cl.cmd_refresh(a, "tok")
cl.cmd_refresh(a, "tok")          # the next refresh repairs any lost update
b = board(fake)
check("our w02 survived", "- [x] `plan-33-acc3-w02`" in b)
check("the rival's w04 survived", "- [x] `plan-33-acc3-w04`" in b)
check("counter 2/3", "— 2/3" in b)

print("\n-- convergence: refreshing twice more changes nothing --")
before = board(fake)
cl.cmd_refresh(a, "tok")
cl.cmd_refresh(a, "tok")
check("body is stable", board(fake) == before)

print("\n-- no checklist on the issue: must not crash or invent one --")
fake2 = Fake()
cl.req = fake2
check("refresh is a no-op", cl.cmd_refresh(a, "tok") == 0)
check("nothing created", not fake2.comments)

print("\n-- an API failure must never fail the run --")


def boom(*_a, **_k):
    raise RuntimeError("GitHub is down")


cl.req = boom
os.environ["GH_TOKEN"] = "tok"
sys.argv = ["x", "refresh", "--repo", "o/r", "--issue", "33", "--marker", MARKER]
check("exit code is 0 despite the API failing", cl.main() == 0)

print()
print('##TBS##' + json.dumps({"data": {"failed": len(fails), "names": fails},
                              "probe": "agy_checklist",
                              "status": "fail" if fails else "pass", "v": 1},
                             sort_keys=True))
sys.exit(1 if fails else 0)
