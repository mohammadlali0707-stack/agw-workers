#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail if the poller cannot see a second Notion Task Board.

A Plan tick on the Business-trial workspace must open a GitHub issue without
copying the page into the primary workspace. That only works if:

  1. load_boards() returns a secondary pair when BOTH token and db id are set
  2. a second db id without a second token is not treated as a board
  3. notion-task-poller.yml forwards NOTION_TOKEN_2 and NOTION_TASKS_DB_ID_2

ASCII only. Exit 0 pass, 1 fail.
"""
import io
import json
import os
import sys

MARKER = "##TBS##"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "Tools", "notion_task_to_issue.py")
POLLER = os.path.join(ROOT, ".github", "workflows", "notion-task-poller.yml")


def read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def main():
    failures = []
    if not os.path.exists(SCRIPT):
        failures.append("missing Tools/notion_task_to_issue.py")
    if not os.path.exists(POLLER):
        failures.append("missing .github/workflows/notion-task-poller.yml")
    if failures:
        print("FAIL: %d finding(s):" % len(failures))
        for x in failures:
            print("  - %s" % x)
        print(MARKER + json.dumps(
            {"v": 1, "probe": "notion_dual_board", "status": "fail",
             "data": {"failures": failures}}, sort_keys=True))
        return 1

    sys.path.insert(0, os.path.join(ROOT, "Tools"))
    import notion_task_to_issue as nti

    one = nti.load_boards({
        "NOTION_TOKEN": "tok-a",
        "NOTION_TASKS_DB_ID": "db-a",
    })
    if one != [("primary", "tok-a", "db-a")]:
        failures.append("primary-only load_boards returned %r" % (one,))

    two = nti.load_boards({
        "NOTION_TOKEN": "tok-a",
        "NOTION_TASKS_DB_ID": "db-a",
        "NOTION_TOKEN_2": "tok-b",
        "NOTION_TASKS_DB_ID_2": "db-b",
    })
    if two != [("primary", "tok-a", "db-a"),
               ("secondary", "tok-b", "db-b")]:
        failures.append("dual load_boards returned %r" % (two,))

    skipped = nti.load_boards({
        "NOTION_TOKEN": "tok-a",
        "NOTION_TASKS_DB_ID": "db-a",
        "NOTION_TASKS_DB_ID_2": "db-b",
    })
    if skipped != [("primary", "tok-a", "db-a")]:
        failures.append("db-id-2 without token-2 should skip secondary, got %r"
                        % (skipped,))

    empty = nti.load_boards({})
    if empty:
        failures.append("empty env should yield no boards, got %r" % (empty,))

    yml = read(POLLER)
    for needle in ("NOTION_TOKEN_2", "NOTION_TASKS_DB_ID_2"):
        if needle not in yml:
            failures.append("poller yml missing %s" % needle)

    src = read(SCRIPT)
    if "NOTION_TOKEN_2" not in src or "NOTION_TASKS_DB_ID_2" not in src:
        failures.append("notion_task_to_issue.py missing second-board env names")

    status = "fail" if failures else "pass"
    if failures:
        print("FAIL: %d finding(s):" % len(failures))
        for x in failures:
            print("  - %s" % x)
    else:
        print("PASS: poller can list a second Task Board when token+db are set, "
              "skips it when the second token is missing, and the workflow "
              "forwards both secrets.")
    print(MARKER + json.dumps(
        {"v": 1, "probe": "notion_dual_board", "status": status,
         "data": {"failures": failures, "boards_one": len(one),
                  "boards_two": len(two)}}, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
