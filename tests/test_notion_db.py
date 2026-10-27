#!/usr/bin/env python3
"""Tests for Bilingual Notion database synchronization (Part 4).

Verifies:
1. 'عنوان فارسی' property is created once if missing.
2. Only changed rows are touched (based on last_edited_time >= since).
3. English titles never change.
4. Knowledge Notebook rows trigger sync_page(row_id); other databases do not.
5. Max 150 rows limit is strictly enforced.
6. Summary is sent to agent bus topic 'bilingual'.
7. Last run timestamp is persisted to state file.
"""

import copy
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from tools.bilingual import notion_db
from tools.bilingual.notion_db import (
    DATABASES,
    PROPERTY_PERSIAN_TITLE,
    sync_databases,
    ensure_persian_title_property,
    extract_title,
    load_last_run,
    save_last_run,
)


class FakeNotionClient:
    """In-memory stub for Notion API without network calls."""

    def __init__(self):
        self.databases = {}
        self.pages = {}
        self.update_database_calls = []
        self.update_page_calls = []
        self.query_database_calls = []
        self.get_database_calls = []

        # Initialize default databases
        for db in DATABASES:
            db_id = db["id"].replace("-", "")
            self.databases[db_id] = {
                "id": db_id,
                "title": [{"plain_text": db["name"]}],
                "properties": {
                    "Name": {"type": "title", "title": {}}
                }
            }

    def get_database(self, db_id: str) -> dict:
        norm_id = db_id.replace("-", "")
        self.get_database_calls.append(norm_id)
        if norm_id not in self.databases:
            self.databases[norm_id] = {
                "id": norm_id,
                "properties": {"Name": {"type": "title", "title": {}}}
            }
        return copy.deepcopy(self.databases[norm_id])

    def update_database(self, db_id: str, properties: dict) -> dict:
        norm_id = db_id.replace("-", "")
        self.update_database_calls.append({"db_id": norm_id, "properties": copy.deepcopy(properties)})
        if norm_id not in self.databases:
            self.databases[norm_id] = {"id": norm_id, "properties": {}}
        self.databases[norm_id]["properties"].update(copy.deepcopy(properties))
        return copy.deepcopy(self.databases[norm_id])

    def add_page(self, db_id: str, page_id: str, title: str, last_edited_time: str, extra_props: dict = None):
        norm_db_id = db_id.replace("-", "")
        props = {
            "Name": {
                "type": "title",
                "title": [{"plain_text": title}]
            }
        }
        if extra_props:
            props.update(extra_props)

        self.pages[page_id] = {
            "id": page_id,
            "parent": {"database_id": norm_db_id},
            "last_edited_time": last_edited_time,
            "properties": props,
        }

    def query_database(self, db_id: str, filter_dict: dict = None, start_cursor: str = None, page_size: int = 100) -> dict:
        norm_db_id = db_id.replace("-", "")
        self.query_database_calls.append({
            "db_id": norm_db_id,
            "filter": copy.deepcopy(filter_dict),
            "start_cursor": start_cursor,
            "page_size": page_size,
        })

        # Match rows belonging to db_id
        matching_rows = [
            p for p in self.pages.values()
            if p["parent"]["database_id"] == norm_db_id
        ]

        # Apply filter if provided
        if filter_dict and "last_edited_time" in filter_dict:
            since = filter_dict["last_edited_time"].get("on_or_after", "")
            if since:
                matching_rows = [p for p in matching_rows if p["last_edited_time"] >= since]

        # Sort chronologically by last_edited_time
        matching_rows.sort(key=lambda x: x["last_edited_time"])

        # Simple pagination
        start_idx = int(start_cursor) if start_cursor else 0
        end_idx = start_idx + page_size
        page_results = matching_rows[start_idx:end_idx]
        has_more = end_idx < len(matching_rows)
        next_cursor = str(end_idx) if has_more else None

        return {
            "results": [copy.deepcopy(r) for r in page_results],
            "has_more": has_more,
            "next_cursor": next_cursor,
        }

    def update_page(self, page_id: str, properties: dict) -> dict:
        self.update_page_calls.append({"page_id": page_id, "properties": copy.deepcopy(properties)})
        if page_id in self.pages:
            self.pages[page_id]["properties"].update(copy.deepcopy(properties))
            return copy.deepcopy(self.pages[page_id])
        return {"id": page_id, "properties": copy.deepcopy(properties)}


class TestNotionDb(unittest.TestCase):

    def setUp(self):
        self.fake_client = FakeNotionClient()
        self.test_dir = tempfile.mkdtemp(prefix="notion_test_")

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_property_created_once_when_missing(self):
        """Property 'عنوان فارسی' is created when missing, and only created once."""
        db_id = DATABASES[0]["id"]
        norm_db_id = db_id.replace("-", "")

        # Verify initial state: property does not exist
        self.assertNotIn(PROPERTY_PERSIAN_TITLE, self.fake_client.databases[norm_db_id]["properties"])

        # Run 1: Should create property
        res1 = sync_databases(client=self.fake_client, post_bus=False)
        self.assertTrue(res1["databases"][db_id]["property_created"])
        self.assertIn(PROPERTY_PERSIAN_TITLE, self.fake_client.databases[norm_db_id]["properties"])
        self.assertEqual(len(self.fake_client.update_database_calls), 3)  # One for each database

        # Run 2: Property already exists, update_database should not be called again
        self.fake_client.update_database_calls.clear()
        res2 = sync_databases(client=self.fake_client, post_bus=False)
        self.assertFalse(res2["databases"][db_id]["property_created"])
        self.assertEqual(len(self.fake_client.update_database_calls), 0)

    def test_only_changed_rows_are_touched(self):
        """Only rows with last_edited_time >= since are updated."""
        kn_id = DATABASES[0]["id"]

        # Add 3 pages: 1 old, 2 new
        self.fake_client.add_page(kn_id, "row-old", "Old Note", "2026-10-25T12:00:00Z")
        self.fake_client.add_page(kn_id, "row-new-1", "New Note 1", "2026-10-27T01:00:00Z")
        self.fake_client.add_page(kn_id, "row-new-2", "New Note 2", "2026-10-27T03:00:00Z")

        with patch("tools.bilingual.notion_db.sync_page") as mock_sync_page:
            mock_sync_page.return_value = {"page": "x", "status": "updated", "blocks": 1}
            stats = sync_databases(
                client=self.fake_client,
                since="2026-10-27T00:00:00Z",
                post_bus=False
            )

        updated_ids = [call["page_id"] for call in self.fake_client.update_page_calls]
        self.assertNotIn("row-old", updated_ids)
        self.assertIn("row-new-1", updated_ids)
        self.assertIn("row-new-2", updated_ids)
        self.assertEqual(stats["total_rows_processed"], 2)

    def test_english_titles_never_change(self):
        """Row update payload never modifies or removes the original English title."""
        tb_id = DATABASES[2]["id"]
        original_title = "Implement Feature SDD-006"
        self.fake_client.add_page(tb_id, "task-1", original_title, "2026-10-27T02:00:00Z")

        sync_databases(client=self.fake_client, post_bus=False)

        # Check update_page calls
        self.assertEqual(len(self.fake_client.update_page_calls), 1)
        update_call = self.fake_client.update_page_calls[0]
        update_props = update_call["properties"]

        # 1. English title property 'Name' must NOT be in the update properties
        self.assertNotIn("Name", update_props)
        # 2. Only 'عنوان فارسی' should be updated
        self.assertIn(PROPERTY_PERSIAN_TITLE, update_props)

        # 3. Check actual page in fake client: English title is unaltered
        page = self.fake_client.pages["task-1"]
        english_parts = page["properties"]["Name"]["title"]
        self.assertEqual(english_parts[0]["plain_text"], original_title)

    def test_knowledge_notebook_triggers_sync_page_only(self):
        """Only Knowledge Notebook rows invoke sync_page(row_id)."""
        kn_id = DATABASES[0]["id"]
        dl_id = DATABASES[1]["id"]
        tb_id = DATABASES[2]["id"]

        self.fake_client.add_page(kn_id, "kn-row", "Knowledge Entry", "2026-10-27T01:00:00Z")
        self.fake_client.add_page(dl_id, "dl-row", "Decision Entry", "2026-10-27T01:00:00Z")
        self.fake_client.add_page(tb_id, "tb-row", "Task Entry", "2026-10-27T01:00:00Z")

        with patch("tools.bilingual.notion_db.sync_page") as mock_sync_page:
            mock_sync_page.return_value = {"page": "x", "status": "updated", "blocks": 2}
            sync_databases(client=self.fake_client, post_bus=False)

            # sync_page should only be called once, for 'kn-row'
            mock_sync_page.assert_called_once_with("kn-row", dry_run=False)

    def test_max_150_rows_limit(self):
        """Execution stops once 150 rows are processed across databases."""
        kn_id = DATABASES[0]["id"]
        dl_id = DATABASES[1]["id"]
        tb_id = DATABASES[2]["id"]

        # Populate each DB with 70 rows (total 210)
        for i in range(70):
            self.fake_client.add_page(kn_id, f"kn-{i}", f"KN Note {i}", f"2026-10-27T01:{i:02d}:00Z")
            self.fake_client.add_page(dl_id, f"dl-{i}", f"DL Note {i}", f"2026-10-27T02:{i:02d}:00Z")
            self.fake_client.add_page(tb_id, f"tb-{i}", f"TB Note {i}", f"2026-10-27T03:{i:02d}:00Z")

        with patch("tools.bilingual.notion_db.sync_page") as mock_sync_page:
            mock_sync_page.return_value = {"page": "x", "status": "updated", "blocks": 1}
            stats = sync_databases(client=self.fake_client, max_rows=150, post_bus=False)

        self.assertEqual(len(self.fake_client.update_page_calls), 150)
        self.assertEqual(stats["total_rows_processed"], 150)

    def test_timestamp_storage_in_since_file(self):
        """Last run timestamp is loaded and saved to since_file without git commits."""
        state_file = os.path.join(self.test_dir, "cache", "last_sync.json")
        self.assertFalse(os.path.exists(state_file))

        # First run: state file doesn't exist, will be created
        kn_id = DATABASES[0]["id"]
        self.fake_client.add_page(kn_id, "kn-1", "First Note", "2026-10-27T01:00:00Z")

        with patch("tools.bilingual.notion_db.sync_page") as mock_sync:
            mock_sync.return_value = {"page": "kn-1", "status": "updated", "blocks": 1}
            sync_databases(client=self.fake_client, since_file=state_file, post_bus=False)

        self.assertTrue(os.path.exists(state_file))
        loaded_ts = load_last_run(state_file)
        self.assertIsNotNone(loaded_ts)
        self.assertTrue(loaded_ts.startswith("2026-10-27"))

        # Verify reading loaded_ts back works
        saved_again = "2026-10-27T12:34:56Z"
        save_last_run(state_file, saved_again)
        self.assertEqual(load_last_run(state_file), saved_again)

    def test_agent_bus_summary_posted(self):
        """Summary is posted to agent bus topic 'bilingual' with kind 'done'."""
        kn_id = DATABASES[0]["id"]
        self.fake_client.add_page(kn_id, "kn-1", "Doc Title", "2026-10-27T01:00:00Z")

        with patch("tools.agent_bus.post") as mock_bus_post, \
             patch("tools.bilingual.notion_db.sync_page") as mock_sync:
            mock_sync.return_value = {"page": "kn-1", "status": "updated", "blocks": 1}
            sync_databases(client=self.fake_client, post_bus=True)

            mock_bus_post.assert_called_once()
            call_kwargs = mock_bus_post.call_args[1] if mock_bus_post.call_args[1] else {}
            call_args = mock_bus_post.call_args[0]
            # Verify topic is bilingual and kind is done
            topic = call_kwargs.get("topic", call_args[0] if len(call_args) > 0 else "")
            kind = call_kwargs.get("kind", call_args[1] if len(call_args) > 1 else "")
            self.assertEqual(topic, "bilingual")
            self.assertEqual(kind, "done")


if __name__ == "__main__":
    unittest.main()
