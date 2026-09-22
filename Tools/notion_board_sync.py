#!/usr/bin/env python3
"""Mirror new Task Board rows between the two Notion workspaces.

Notion does not sync pages across workspaces. This script is the copy step:
when a row appears on one board, pair it with the same Name on the other
board, or create a twin there. It runs from notion-task-poller.yml BEFORE
the issue opener so a page written in either account shows up in the other.

WHAT IS COPIED
--------------
Name, Project, body paragraphs, and Github Issue URL if already set.
Twin page ID is written on both rows so the next run does not copy again.

WHAT IS NEVER COPIED
--------------------
Plan stays false on a newly created twin. Copying a Plan tick would make
both rows candidates and open two GitHub issues. Tick Plan on one of the
two pages; the issue opener writes Github Issue URL onto both twins.

Fleet inbox rows are dual-written by n8n with the same Name. Those are
PAIRED by Name, not created a third and fourth time.

Edits after creation are not live-synced. Ask if that is needed later.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

NOTION_VERSION = "2022-06-28"
NOTION_API = "https://api.notion.com/v1"
TWIN_PROP = "Twin page ID"
CREATE_WINDOW = timedelta(hours=48)
_last = [0.0]


def _req(method, url, token, body=None, _tries=0):
    gap = time.time() - _last[0]
    if gap < 0.34:
        time.sleep(0.34 - gap)
    _last[0] = time.time()
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Notion-Version", NOTION_VERSION)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        if e.code == 429 and _tries < 5:
            time.sleep(float(e.headers.get("Retry-After", 2)))
            return _req(method, url, token, body, _tries + 1)
        raise RuntimeError(f"{method} {url} -> {e.code}: {detail[:400]}") from None


def plain(rich):
    return "".join(x.get("plain_text", "") for x in rich or [])


def load_boards(env):
    boards = []
    t1 = (env.get("NOTION_TOKEN") or "").strip()
    d1 = (env.get("NOTION_TASKS_DB_ID") or "").strip()
    t2 = (env.get("NOTION_TOKEN_2") or "").strip()
    d2 = (env.get("NOTION_TASKS_DB_ID_2") or "").strip()
    if t1 and d1:
        boards.append(("primary", t1, d1))
    if t2 and d2:
        boards.append(("secondary", t2, d2))
    return boards


def ensure_twin_prop(token, db_id):
    meta = _req("GET", f"{NOTION_API}/databases/{db_id}", token)
    props = meta.get("properties") or {}
    if TWIN_PROP in props:
        return
    _req("PATCH", f"{NOTION_API}/databases/{db_id}", token,
         {"properties": {TWIN_PROP: {"rich_text": {}}}})
    print(f"  added {TWIN_PROP!r} on {db_id}")


def query_all(token, db_id):
    rows, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        res = _req("POST", f"{NOTION_API}/databases/{db_id}/query", token, body)
        rows.extend(res.get("results", []))
        if not res.get("has_more"):
            break
        cursor = res["next_cursor"]
    return rows


def page_name(row):
    return plain((row.get("properties") or {}).get("Name", {}).get("title", [])).strip()


def twin_of(row):
    return plain((row.get("properties") or {}).get(TWIN_PROP, {}).get("rich_text", [])).strip()


def project_of(row):
    sel = ((row.get("properties") or {}).get("Project", {}).get("select") or {})
    return sel.get("name") or ""


def issue_url_of(row):
    sel = ((row.get("properties") or {}).get("Github Issue URL", {}).get("select") or {})
    return sel.get("name") or ""


def created_dt(row):
    raw = row.get("created_time") or ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def page_text(token, page_id):
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


def children_from_text(text):
    blocks = []
    for para in (text or "").split("\n\n"):
        para = para.strip()
        if not para:
            continue
        while para:
            chunk, para = para[:1900], para[1900:]
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": chunk}}]
                },
            })
            if len(blocks) >= 100:
                return blocks
    return blocks


def set_twin(token, page_id, twin_id):
    _req("PATCH", f"{NOTION_API}/pages/{page_id}", token, {
        "properties": {
            TWIN_PROP: {"rich_text": [{"type": "text", "text": {"content": twin_id}}]}
        }
    })


def set_issue_url(token, page_id, url):
    if not url:
        return
    _req("PATCH", f"{NOTION_API}/pages/{page_id}", token, {
        "properties": {"Github Issue URL": {"select": {"name": url}}}
    })


def find_by_twin(rows, source_id):
    for row in rows:
        if twin_of(row) == source_id:
            return row
    return None


def unique_unmatched_by_name(rows):
    buckets = {}
    for row in rows:
        if twin_of(row):
            continue
        name = page_name(row)
        if not name:
            continue
        buckets.setdefault(name, []).append(row)
    return {name: group[0] for name, group in buckets.items() if len(group) == 1}


def pair_rows(src_token, src_row, dst_token, dst_row, dry):
    sid, did = src_row["id"], dst_row["id"]
    print(f"  PAIR {sid} <-> {did} ({page_name(src_row)!r})")
    if dry:
        return "paired_dry"
    set_twin(src_token, sid, did)
    set_twin(dst_token, did, sid)
    src_url, dst_url = issue_url_of(src_row), issue_url_of(dst_row)
    if src_url and not dst_url:
        set_issue_url(dst_token, did, src_url)
    elif dst_url and not src_url:
        set_issue_url(src_token, sid, dst_url)
    return "paired"


def create_twin(src_token, src_row, dst_token, dst_db, dry):
    sid = src_row["id"]
    name = page_name(src_row)
    project = project_of(src_row)
    issue_url = issue_url_of(src_row)
    print(f"  CREATE twin of {sid} {name!r} project={project!r} Plan=false")
    if dry:
        return "created_dry"
    props = {
        "Name": {"title": [{"type": "text", "text": {"content": name[:2000]}}]},
        "Plan": {"checkbox": False},
        TWIN_PROP: {"rich_text": [{"type": "text", "text": {"content": sid}}]},
    }
    if project:
        props["Project"] = {"select": {"name": project}}
    if issue_url:
        props["Github Issue URL"] = {"select": {"name": issue_url}}
    body = {
        "parent": {"database_id": dst_db},
        "properties": props,
    }
    text = page_text(src_token, sid)
    kids = children_from_text(text)
    if kids:
        body["children"] = kids
    created = _req("POST", f"{NOTION_API}/pages", dst_token, body)
    new_id = created["id"]
    set_twin(src_token, sid, new_id)
    print(f"     twin {new_id}")
    return "created"


def recent_enough(row, now):
    dt = created_dt(row)
    if dt is None:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt) <= CREATE_WINDOW


def main():
    env = os.environ
    dry = env.get("DRY_RUN", "") == "1"
    boards = load_boards(env)
    stats = {"paired": 0, "created": 0, "skipped": 0, "failed": 0}
    if len(boards) < 2:
        print("only one Task Board configured; nothing to mirror")
        print("##TBS##" + json.dumps(
            {"data": stats, "probe": "notion_board_sync", "status": "skip", "v": 1},
            sort_keys=True))
        return 0

    now = datetime.now(timezone.utc)
    labeled = []
    for label, token, db_id in boards:
        ensure_twin_prop(token, db_id)
        rows = query_all(token, db_id)
        print(f"{len(rows)} row(s) on {label}")
        labeled.append((label, token, db_id, rows))

    (a_label, a_token, a_db, a_rows) = labeled[0]
    (b_label, b_token, b_db, b_rows) = labeled[1]
    a_unmatched = unique_unmatched_by_name(a_rows)
    b_unmatched = unique_unmatched_by_name(b_rows)
    seen = set()

    for name, a_row in a_unmatched.items():
        b_row = b_unmatched.get(name)
        if not b_row:
            continue
        try:
            pair_rows(a_token, a_row, b_token, b_row, dry)
            stats["paired"] += 1
            seen.add(a_row["id"])
            seen.add(b_row["id"])
        except Exception as e:  # noqa: BLE001
            stats["failed"] += 1
            print(f"     FAILED pair {name!r}: {str(e)[:300]}", file=sys.stderr)

    sides = [
        (a_token, a_rows, b_token, b_db, b_rows),
        (b_token, b_rows, a_token, a_db, a_rows),
    ]
    for src_token, src_rows, dst_token, dst_db, dst_rows in sides:
        for src_row in src_rows:
            sid = src_row["id"]
            if sid in seen or twin_of(src_row):
                continue
            if find_by_twin(dst_rows, sid):
                stats["skipped"] += 1
                continue
            name = page_name(src_row)
            if not name:
                stats["skipped"] += 1
                continue
            if not recent_enough(src_row, now):
                stats["skipped"] += 1
                continue
            try:
                create_twin(src_token, src_row, dst_token, dst_db, dry)
                stats["created"] += 1
                seen.add(sid)
            except Exception as e:  # noqa: BLE001
                stats["failed"] += 1
                print(f"     FAILED create {name!r}: {str(e)[:300]}", file=sys.stderr)

    status = "fail" if stats["failed"] else "pass"
    print("##TBS##" + json.dumps(
        {"data": stats, "probe": "notion_board_sync", "status": status, "v": 1},
        sort_keys=True))
    return 0 if not stats["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
