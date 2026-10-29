#!/usr/bin/env python3
"""Notion database synchronization for Bilingual Notion (Part 4).

Databases:
1. Knowledge Notebook: 405befc7ddfb814abb94f1fa42ce5c21 (sync_page toggle enabled)
2. Decision Log:       1cba1fea3ee849478a2bb132a6301ea3
3. Task Board:          3dcbefc7ddfb80e18e26ced6033af37f

Requirements:
- Check and add 'عنوان فارسی' (rich_text) property if missing (created once).
- For rows edited since last run, translate title to Persian and fill 'عنوان فارسی'.
- English titles never change.
- Knowledge Notebook rows only: run sync_page(row_id) for Persian toggle.
- Max 150 rows per run.
- Summary to agent bus topic 'bilingual'.
- Store last run timestamp in workflow cache / state file (NOT a commit).
"""

import argparse
from datetime import datetime, timezone
import json
import os
import sys
import time
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools.bilingual.translate import translate
from tools.bilingual.notion_fa import sync_page

NOTION_VERSION = "2022-06-28"
NOTION_API = "https://api.notion.com/v1"
PROPERTY_PERSIAN_TITLE = "عنوان فارسی"
MAX_ROWS_DEFAULT = 150

DATABASES = [
    {
        "id": "405befc7ddfb814abb94f1fa42ce5c21",
        "name": "Knowledge Notebook",
        "sync_page_toggle": True,
    },
    {
        "id": "1cba1fea3ee849478a2bb132a6301ea3",
        "name": "Decision Log",
        "sync_page_toggle": False,
    },
    {
        "id": "3dcbefc7ddfb80e18e26ced6033af37f",
        "name": "Task Board",
        "sync_page_toggle": False,
    },
]

_last_req = [0.0]


def _rate_limit():
    gap = time.time() - _last_req[0]
    if gap < 0.34:
        time.sleep(0.34 - gap)
    _last_req[0] = time.time()


class NotionClient:
    """Production Notion API client with rate limiting and retry logic."""

    def __init__(self, token: str = None):
        self.token = token or os.environ.get("NOTION_PAT", "")

    def _req(self, method: str, path: str, body: dict = None, _tries: int = 0) -> dict:
        if not self.token:
            raise RuntimeError("NOTION_PAT is required for Notion API requests.")
        _rate_limit()
        url = f"{NOTION_API}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Notion-Version", NOTION_VERSION)
        req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and _tries < 5:
                wait = float(e.headers.get("Retry-After", 2))
                time.sleep(wait)
                return self._req(method, path, body, _tries + 1)
            if e.code in (500, 502, 503, 504) and _tries < 3:
                time.sleep(2 ** _tries)
                return self._req(method, path, body, _tries + 1)
            raw = e.read().decode("utf-8", "replace")
            raise RuntimeError(f"Notion API {method} {path} -> {e.code}: {raw[:300]}") from None
        except (TimeoutError, ConnectionError, urllib.error.URLError) as e:   # transient network slowness: retry with backoff
            if _tries < 4:
                time.sleep(3 * 2 ** _tries)
                return self._req(method, path, body, _tries + 1)
            raise RuntimeError(f"Notion API {method} {path} -> network error after retries: {type(e).__name__}") from None

    def get_database(self, db_id: str) -> dict:
        norm_id = db_id.replace("-", "")
        return self._req("GET", f"/databases/{norm_id}")

    def update_database(self, db_id: str, properties: dict) -> dict:
        norm_id = db_id.replace("-", "")
        return self._req("PATCH", f"/databases/{norm_id}", body={"properties": properties})

    def query_database(self, db_id: str, filter_dict: dict = None, start_cursor: str = None, page_size: int = 100) -> dict:
        norm_id = db_id.replace("-", "")
        body = {"page_size": min(page_size, 100)}
        if filter_dict:
            body["filter"] = filter_dict
        if start_cursor:
            body["start_cursor"] = start_cursor
        return self._req("POST", f"/databases/{norm_id}/query", body=body)

    def update_page(self, page_id: str, properties: dict) -> dict:
        norm_id = page_id.replace("-", "")
        return self._req("PATCH", f"/pages/{norm_id}", body={"properties": properties})


def ensure_persian_title_property(client, db_id: str) -> bool:
    """Ensure 'عنوان فارسی' rich_text property exists on the database schema.
    
    Returns True if property was added, False if it already existed.
    """
    db = client.get_database(db_id)
    properties = db.get("properties", {})
    if PROPERTY_PERSIAN_TITLE in properties:
        return False

    client.update_database(db_id, properties={
        PROPERTY_PERSIAN_TITLE: {
            "rich_text": {}
        }
    })
    return True


def extract_title(row: dict) -> tuple[str, str]:
    """Extract (property_name, english_title_text) for the title property."""
    properties = row.get("properties", {})
    for name, prop in properties.items():
        if prop.get("type") == "title":
            title_parts = prop.get("title", [])
            text = "".join(t.get("plain_text", "") for t in title_parts).strip()
            return name, text
    return "Name", ""


def load_last_run(since_file: str | None) -> str | None:
    """Load ISO timestamp of last successful run from state file."""
    if not since_file or not os.path.exists(since_file):
        return None
    try:
        with open(since_file, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return None
            try:
                data = json.loads(content)
                if isinstance(data, dict):
                    return data.get("last_run")
            except json.JSONDecodeError:
                return content
    except Exception as e:
        print(f"Warning: could not read since_file {since_file}: {e}", file=sys.stderr)
    return None


def save_last_run(since_file: str | None, timestamp: str):
    """Save ISO timestamp to state file."""
    if not since_file:
        return
    try:
        os.makedirs(os.path.dirname(os.path.abspath(since_file)), exist_ok=True)
        with open(since_file, "w", encoding="utf-8") as f:
            json.dumps({"last_run": timestamp})
            f.write(json.dumps({"last_run": timestamp}, indent=2) + "\n")
    except Exception as e:
        print(f"Warning: could not save since_file {since_file}: {e}", file=sys.stderr)


def post_summary_to_agent_bus(summary_text: str, refs: str = "") -> bool:
    """Post summary report to agent bus topic 'bilingual'."""
    try:
        from tools import agent_bus
        agent_bus.post(topic="bilingual", kind="done", text=summary_text, refs=refs)
        return True
    except Exception as e:
        print(f"Agent bus post notice: {e}", file=sys.stderr)
        return False


def sync_databases(
    client=None,
    since: str = None,
    since_file: str = None,
    max_rows: int = MAX_ROWS_DEFAULT,
    dry_run: bool = False,
    post_bus: bool = True,
    token: str = None,
) -> dict:
    """Sync Notion databases: add Persian title property, translate titles, and sync notebook pages."""
    if client is None:
        client = NotionClient(token=token)

    since_timestamp = since or load_last_run(since_file)
    current_run_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    stats = {
        "total_rows_processed": 0,
        "since": since_timestamp,
        "completed_at": current_run_timestamp,
        "databases": {},
    }

    if not getattr(client, "token", None) and dry_run:
        print("Notice: NOTION_PAT not set, skipping remote queries in dry-run mode.", file=sys.stderr)
        return stats

    remaining_rows_budget = max_rows

    for db_cfg in DATABASES:
        db_id = db_cfg["id"]
        db_name = db_cfg["name"]
        sync_pages = db_cfg["sync_page_toggle"]

        db_stat = {
            "name": db_name,
            "property_created": False,
            "rows_updated": 0,
            "pages_synced": 0,
        }
        stats["databases"][db_id] = db_stat

        # 1. Ensure 'عنوان فارسی' property exists
        if not dry_run:
            prop_added = ensure_persian_title_property(client, db_id)
            db_stat["property_created"] = prop_added

        if remaining_rows_budget <= 0:
            continue

        # 2. Query rows edited on or after since_timestamp
        filter_dict = None
        if since_timestamp:
            filter_dict = {
                "timestamp": "last_edited_time",
                "last_edited_time": {
                    "on_or_after": since_timestamp
                }
            }

        cursor = None
        while remaining_rows_budget > 0:
            query_res = client.query_database(
                db_id,
                filter_dict=filter_dict,
                start_cursor=cursor,
                page_size=min(remaining_rows_budget, 100),
            )
            rows = query_res.get("results", [])
            if not rows:
                break

            for row in rows:
                if remaining_rows_budget <= 0:
                    break

                row_id = row["id"]
                _, english_title = extract_title(row)

                if english_title:
                    persian_title = translate(english_title, target="fa")
                else:
                    persian_title = ""

                # Update row with Persian title property only -- English title is untouched
                if not dry_run:
                    client.update_page(row_id, properties={
                        PROPERTY_PERSIAN_TITLE: {
                            "rich_text": [
                                {
                                    "type": "text",
                                    "text": {"content": persian_title[:2000]}
                                }
                            ]
                        }
                    })

                db_stat["rows_updated"] += 1
                stats["total_rows_processed"] += 1
                remaining_rows_budget -= 1

                # 3. Knowledge Notebook rows only: run sync_page(row_id)
                if sync_pages:
                    sync_res = sync_page(row_id, dry_run=dry_run)
                    if sync_res.get("status") in ("updated", "unchanged"):
                        db_stat["pages_synced"] += 1

            if not query_res.get("has_more") or remaining_rows_budget <= 0:
                break
            cursor = query_res.get("next_cursor")

    # Save last run timestamp upon successful run
    if not dry_run and since_file:
        save_last_run(since_file, current_run_timestamp)

    # 4. Summary to agent bus topic 'bilingual'
    summary = (
        f"Bilingual Notion DB sync: {stats['total_rows_processed']} rows processed "
        f"(max {max_rows}). Knowledge Notebook: {stats['databases'][DATABASES[0]['id']]['rows_updated']} rows, "
        f"{stats['databases'][DATABASES[0]['id']]['pages_synced']} pages. "
        f"Decision Log: {stats['databases'][DATABASES[1]['id']]['rows_updated']} rows. "
        f"Task Board: {stats['databases'][DATABASES[2]['id']]['rows_updated']} rows."
    )
    stats["summary"] = summary

    if post_bus and not dry_run:
        post_summary_to_agent_bus(summary)

    return stats


def main():
    parser = argparse.ArgumentParser(description="Sync Notion databases for bilingual translation.")
    parser.add_argument("--since-file", default=None, help="Path to file storing last run timestamp")
    parser.add_argument("--since", default=None, help="ISO timestamp to filter rows (on_or_after)")
    parser.add_argument("--max-rows", type=int, default=MAX_ROWS_DEFAULT, help="Max rows to process (default: 150)")
    parser.add_argument("--dry-run", action="store_true", help="Run without writing modifications")
    parser.add_argument("--no-agent-bus", action="store_true", help="Skip posting summary to agent bus")
    parser.add_argument("--token", default=None, help="Notion PAT (defaults to NOTION_PAT env var)")
    args = parser.parse_args()

    result = sync_databases(
        since=args.since,
        since_file=args.since_file,
        max_rows=args.max_rows,
        dry_run=args.dry_run,
        post_bus=not args.no_agent_bus,
        token=args.token,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
