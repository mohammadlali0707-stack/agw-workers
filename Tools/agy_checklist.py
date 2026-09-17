#!/usr/bin/env python3
"""A live checklist on the issue: what the lead intends to do, ticked as it happens.

Owner's request, 2026-09-17: the bot should post the plan it is about to carry
out as a list, and tick each item off as that part completes -- rather than
going silent for ten minutes and then emitting a wall of output.

TWO MODES
---------
  post    --repo R --issue N --marker M --tasks FILE
          Writes the checklist comment: one unchecked box per planned task,
          plus the roster it was built from, as an HTML comment.
  refresh --repo R --issue N --marker M
          RE-DERIVES every box from the issue and rewrites the comment.

The marker carries the run's base tag, so several leads can have checklists on
one issue without colliding.

WHY REFRESH AND NOT TICK
------------------------
The first version took a tag and ticked that one line in place. A probe with a
concurrent writer showed the flaw: subordinates finish independently, so one
worker reads the body, a rival PATCHes its own tick, and the first worker's
PATCH then writes back its stale body -- silently erasing the rival's tick. Its
own read-back check could not see that, because its OWN tick was present.

So no box is ever "ticked". Each worker stamps its result comment with
`<!-- agy-task: <tag> state=done|failed -->` and then asks for a refresh, which
rebuilds the whole checklist from those stamps. The body becomes a pure
function of what is on the issue, so concurrent writers converge instead of
racing, and any update that does get lost is repaired by the next one.

A checklist is progress reporting. It never fails the run it reports on.
"""
import argparse
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"
ROSTER = re.compile(r"<!-- agy-roster: (.*?) -->", re.S)
STAMP = re.compile(r"<!-- agy-task: (\S+) state=(done|failed) -->")
MARK = {"done": "x", "failed": "~"}


def req(method, url, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", f"Bearer {token}")
    r.add_header("Accept", "application/vnd.github+json")
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=45) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"{method} {url} -> {e.code}: {detail}") from None


def all_comments(repo, issue, token):
    out, page = [], 1
    while True:
        res = req("GET", f"{API}/repos/{repo}/issues/{issue}/comments"
                         f"?per_page=100&page={page}", token)
        out.extend(res or [])
        if not res or len(res) < 100:
            return out
        page += 1


def render(marker, rows, title, state):
    """The checklist body, as a pure function of the roster and the stamps
    found on the issue. Same inputs, same bytes -- that is what makes
    concurrent refreshes converge."""
    done = sum(1 for r in rows if state.get(r["tag"]) == "done")
    lines = [
        marker,
        f"<!-- agy-roster: {json.dumps(rows, ensure_ascii=False)} -->",
        "",
        f"### {title} — {done}/{len(rows)}",
        "",
    ]
    for r in rows:
        box = MARK.get(state.get(r["tag"], ""), " ")
        lines.append(f"- [{box}] `{r['tag']}` **{r['title']}** — {r['member']}")
    lines += [
        "",
        "_هر مورد به‌محض تمام‌شدن همین‌جا به‌روز می‌شود. `~` یعنی آن زیرکار شکست خورد._",
    ]
    return "\n".join(lines)


def read_state(comments):
    """tag -> done|failed, from the stamps subordinates leave on their own
    result comments. Nothing else is trusted."""
    state = {}
    for c in comments:
        for tag, st in STAMP.findall(c.get("body") or ""):
            # A later stamp for the same tag wins: a retry that succeeds
            # should not stay crossed out.
            state[tag] = st
    return state


def cmd_post(a, token):
    rows = []
    with open(a.tasks, encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3 and parts[0].strip():
                rows.append({"tag": parts[0], "title": parts[1],
                             "member": parts[2]})
    if not rows:
        print("no tasks to list, not posting a checklist")
        return 0
    comments = all_comments(a.repo, a.issue, token)
    existing = next((c for c in comments
                     if a.marker in (c.get("body") or "")), None)
    body = render(a.marker, rows, a.title, read_state(comments))
    if existing:
        req("PATCH", f"{API}/repos/{a.repo}/issues/comments/{existing['id']}",
            token, {"body": body})
        print(f"checklist rewritten ({len(rows)} item(s))")
    else:
        req("POST", f"{API}/repos/{a.repo}/issues/{a.issue}/comments",
            token, {"body": body})
        print(f"checklist posted ({len(rows)} item(s))")
    return 0


def cmd_refresh(a, token):
    comments = all_comments(a.repo, a.issue, token)
    target = next((c for c in comments
                   if a.marker in (c.get("body") or "")), None)
    if not target:
        print(f"no checklist carrying {a.marker!r} on this issue; nothing to do")
        return 0
    m = ROSTER.search(target.get("body") or "")
    if not m:
        print("::warning::checklist has no roster payload; leaving it alone")
        return 0
    rows = json.loads(m.group(1))
    state = read_state(comments)
    title = a.title
    head = re.search(r"^### (.*?) —", target.get("body") or "", re.M)
    if head:
        title = head.group(1)
    body = render(a.marker, rows, title, state)
    if body == target.get("body"):
        print("checklist already up to date")
        return 0
    req("PATCH", f"{API}/repos/{a.repo}/issues/comments/{target['id']}",
        token, {"body": body})
    done = sum(1 for r in rows if state.get(r["tag"]) == "done")
    print(f"checklist refreshed: {done}/{len(rows)} done")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("post", "refresh"):
        s = sub.add_parser(name)
        s.add_argument("--repo", required=True)
        s.add_argument("--issue", required=True)
        s.add_argument("--marker", required=True)
        s.add_argument("--title", default="نقشه‌ی کار")
        if name == "post":
            s.add_argument("--tasks", required=True,
                           help="TSV: tag<TAB>title<TAB>member, one per line")
    a = ap.parse_args()

    token = os.environ.get("GH_TOKEN", "")
    if not token:
        print("GH_TOKEN is not set", file=sys.stderr)
        return 1
    try:
        return cmd_post(a, token) if a.cmd == "post" else cmd_refresh(a, token)
    except Exception as e:                                   # noqa: BLE001
        # A checklist is progress reporting. It must never take down the run
        # whose progress it reports.
        print(f"::warning::checklist {a.cmd} failed: {str(e)[:250]}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
