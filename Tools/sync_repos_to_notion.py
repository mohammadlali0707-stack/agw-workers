#!/usr/bin/env python3
"""Mirror the five fleet repositories into the Notion "Repo Mirror" database.

One Notion row per file, so Notion search and Notion AI can read the fleet's
own code and docs. Written to be run by .github/workflows/sync-repos-to-notion.yml
-- on every push to main (only the changed files) and on demand (--full).

WHY A DATABASE AND NOT LOOSE PAGES
----------------------------------
The owner asked for everything, code included: ~2000 files. As loose pages
that would bury the docs -- a search for "MAP" would compete with every
Python file. As database rows it stays filterable by Repo and Kind, so
"just the docs of just this repo" is one filter away.

RATE LIMIT
----------
Notion's API allows roughly 3 requests/second and answers 429 above that.
Every call goes through _req(), which paces itself and honours Retry-After
rather than assuming the limit is never hit. A full sync of ~2000 files is
therefore tens of minutes -- expected, not a hang.

CHANGE DETECTION
----------------
Each row stores the file's git blob SHA. A file whose SHA still matches is
skipped without rewriting its content, so a push that touches three files
costs three writes, not two thousand. The full sync pages the whole database
into memory ONCE up front for the same reason -- per-file lookups would
triple the call count.
"""
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

NOTION_VERSION = "2022-06-28"
API = "https://api.notion.com/v1"

REPOS_ROOT = os.environ.get("REPOS_ROOT", "/tmp/fleet")
REPOS = [
    "agw-workers",
    "Control-Room",
    "Claud-Cloud-Project",
    "AirboxVIP_Coffeenet",
    "status-dashboard",
]

# A Notion rich_text item caps at 2000 characters and an append call at 100
# blocks, so content is chunked to fit both. Files past MAX_BYTES are stored
# truncated with an explicit marker -- a silently half-mirrored file would be
# worse than one that says it was cut.
CHUNK = 1900
MAX_BLOCKS = 45
MAX_BYTES = 200_000

EXT_KIND = {
    "md": "doc", "txt": "doc", "rst": "doc",
    "py": "code", "sh": "code", "js": "code", "ts": "code", "mjs": "code",
    "json": "config", "ini": "config", "cfg": "config", "toml": "config",
    "yml": "config", "yaml": "config",
}
EXT_LANG = {
    "py": "python", "sh": "bash", "js": "javascript", "ts": "typescript",
    "json": "json", "yml": "yaml", "yaml": "yaml", "md": "markdown",
    "html": "html", "css": "css", "sql": "sql",
}

_last_call = [0.0]


def _req(method, url, token, body=None, _tries=0):
    """One Notion API call, paced and retried. Never silently swallows a failure."""
    gap = time.time() - _last_call[0]
    if gap < 0.34:                      # ~3 req/s
        time.sleep(0.34 - gap)
    _last_call[0] = time.time()

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Notion-Version", NOTION_VERSION)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        if e.code == 429 and _tries < 5:
            wait = float(e.headers.get("Retry-After", 2))
            time.sleep(wait)
            return _req(method, url, token, body, _tries + 1)
        if e.code in (502, 503, 504) and _tries < 3:
            time.sleep(2 ** _tries)
            return _req(method, url, token, body, _tries + 1)
        raise RuntimeError(f"{method} {url} -> {e.code}: {raw[:400]}") from None


def blob_sha(path):
    """The same hash `git hash-object` prints, computed in-process.

    Shelling out was correct but cost one subprocess per file, and a full sync
    walks ~2000 of them. It also tied change detection to the file living
    inside a git work tree, which a future caller need not guarantee.
    Tools/probe_blob_sha.py checks this against real `git hash-object` output.
    """
    try:
        raw = open(path, "rb").read()
    except OSError:
        return ""
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(raw))
    h.update(raw)
    return h.hexdigest()


def classify(rel_path, ext):
    if "/.github/workflows/" in f"/{rel_path}":
        return "workflow"
    return EXT_KIND.get(ext, "other")


def read_text(path):
    """Return (text, truncated) or (None, False) when the file is not text."""
    try:
        raw = open(path, "rb").read()
    except OSError:
        return None, False
    truncated = False
    if len(raw) > MAX_BYTES:
        raw = raw[:MAX_BYTES]
        truncated = True
    if b"\0" in raw:
        return None, False
    try:
        return raw.decode("utf-8"), truncated
    except UnicodeDecodeError:
        return None, False


def content_blocks(text, ext, truncated):
    lang = EXT_LANG.get(ext, "plain text")
    blocks = []
    for i in range(0, len(text), CHUNK):
        if len(blocks) >= MAX_BLOCKS:
            truncated = True
            break
        blocks.append({
            "object": "block", "type": "code",
            "code": {
                "language": lang,
                "rich_text": [{"type": "text",
                               "text": {"content": text[i:i + CHUNK]}}],
            },
        })
    if truncated:
        blocks.append({
            "object": "block", "type": "callout",
            "callout": {
                "icon": {"emoji": "✂️"},
                "rich_text": [{"type": "text", "text": {"content":
                    "Truncated by the mirror. Read the whole file on GitHub -- "
                    "this row exists so Notion search can find it, not to be "
                    "the source of truth."}}],
            },
        })
    return blocks


def load_existing(token, db_id):
    """Path -> {"id", "sha"} for every row already in the mirror, in one pass."""
    out, cursor = {}, None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        res = _req("POST", f"{API}/databases/{db_id}/query", token, body)
        for row in res.get("results", []):
            props = row.get("properties", {})
            title = props.get("Path", {}).get("title", [])
            path = title[0]["plain_text"] if title else None
            sha_rt = props.get("Blob SHA", {}).get("rich_text", [])
            if path:
                out[path] = {
                    "id": row["id"],
                    "sha": sha_rt[0]["plain_text"] if sha_rt else "",
                }
        if not res.get("has_more"):
            return out
        cursor = res["next_cursor"]


def clear_children(token, page_id):
    """Delete a page's blocks. Notion has no 'replace content' -- appending to
    an existing page would stack the old and new file side by side."""
    cursor = None
    ids = []
    while True:
        url = f"{API}/blocks/{page_id}/children?page_size=100"
        if cursor:
            url += f"&start_cursor={cursor}"
        res = _req("GET", url, token)
        ids.extend(b["id"] for b in res.get("results", []))
        if not res.get("has_more"):
            break
        cursor = res["next_cursor"]
    for bid in ids:
        _req("DELETE", f"{API}/blocks/{bid}", token)


def sync_file(token, db_id, existing, repo, rel, abs_path, stats):
    ext = rel.rsplit(".", 1)[-1].lower() if "." in rel.rsplit("/", 1)[-1] else ""
    key = f"{repo}/{rel}"
    sha = blob_sha(abs_path)
    prior = existing.get(key)
    if prior and prior["sha"] == sha and sha:
        stats["skipped"] += 1
        return

    text, truncated = read_text(abs_path)
    if text is None:
        stats["binary"] += 1
        return

    props = {
        "Path": {"title": [{"text": {"content": key[:2000]}}]},
        "Repo": {"select": {"name": repo}},
        "Kind": {"select": {"name": classify(rel, ext)}},
        "Ext": {"rich_text": [{"text": {"content": ext[:100]}}]},
        "Bytes": {"number": os.path.getsize(abs_path)},
        "Blob SHA": {"rich_text": [{"text": {"content": sha}}]},
        "Synced": {"date": {"start": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                   time.gmtime())}},
    }
    blocks = content_blocks(text, ext, truncated)

    if prior:
        _req("PATCH", f"{API}/pages/{prior['id']}", token, {"properties": props})
        clear_children(token, prior["id"])
        for i in range(0, len(blocks), 100):
            _req("PATCH", f"{API}/blocks/{prior['id']}/children", token,
                 {"children": blocks[i:i + 100]})
        stats["updated"] += 1
    else:
        page = _req("POST", f"{API}/pages", token, {
            "parent": {"database_id": db_id},
            "properties": props,
            "children": blocks[:100],
        })
        for i in range(100, len(blocks), 100):
            _req("PATCH", f"{API}/blocks/{page['id']}/children", token,
                 {"children": blocks[i:i + 100]})
        existing[key] = {"id": page["id"], "sha": sha}
        stats["created"] += 1


def walk(repo):
    root = os.path.join(REPOS_ROOT, repo)
    if not os.path.isdir(root):
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "node_modules", "__pycache__", ".venv")]
        for fn in filenames:
            ap = os.path.join(dirpath, fn)
            yield os.path.relpath(ap, root), ap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true",
                    help="walk every file in every repo (slow: tens of minutes)")
    ap.add_argument("--changed", default="",
                    help="newline-separated '<repo>/<path>' list, for push syncs")
    ap.add_argument("--dry-run", action="store_true",
                    help="classify and count without calling Notion at all")
    args = ap.parse_args()

    token = os.environ.get("NOTION_TOKEN", "")
    db_id = os.environ.get("NOTION_DB_ID", "")
    if not args.dry_run and (not token or not db_id):
        print("NOTION_TOKEN and NOTION_DB_ID must both be set", file=sys.stderr)
        return 1

    stats = {"created": 0, "updated": 0, "skipped": 0, "binary": 0, "failed": 0}

    if args.dry_run:
        for repo in REPOS:
            for rel, ap_ in walk(repo):
                ext = rel.rsplit(".", 1)[-1].lower() if "." in rel.rsplit("/", 1)[-1] else ""
                text, _ = read_text(ap_)
                stats["binary" if text is None else "created"] += 1
        print(json.dumps(stats))
        print('##TBS##' + json.dumps(
            {"data": stats, "probe": "sync_repos_to_notion", "status": "pass", "v": 1},
            sort_keys=True))
        return 0

    existing = load_existing(token, db_id)
    print(f"mirror currently holds {len(existing)} row(s)")

    if args.full:
        targets = [(r, rel, ap_) for r in REPOS for rel, ap_ in walk(r)]
    else:
        targets = []
        for line in args.changed.splitlines():
            line = line.strip()
            if not line or "/" not in line:
                continue
            repo, rel = line.split("/", 1)
            ap_ = os.path.join(REPOS_ROOT, repo, rel)
            if os.path.isfile(ap_):
                targets.append((repo, rel, ap_))
    print(f"{len(targets)} file(s) to consider")

    for n, (repo, rel, ap_) in enumerate(targets, 1):
        try:
            sync_file(token, db_id, existing, repo, rel, ap_, stats)
        except Exception as e:                       # noqa: BLE001
            # One unmappable file must not abandon the other 1999.
            stats["failed"] += 1
            print(f"  FAILED {repo}/{rel}: {str(e)[:200]}", file=sys.stderr)
        if n % 100 == 0:
            print(f"  {n}/{len(targets)} ... {stats}")

    print(json.dumps(stats))
    status = "fail" if stats["failed"] else "pass"
    print('##TBS##' + json.dumps(
        {"data": stats, "probe": "sync_repos_to_notion", "status": status, "v": 1},
        sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
