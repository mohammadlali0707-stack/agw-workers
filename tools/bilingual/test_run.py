"""Unit tests for tools.bilingual.run."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from tools.bilingual import run


class TestBilingualRun(unittest.TestCase):

    def test_normalize_id(self):
        self.assertEqual(run.normalize_id("bd9e7f06-f642-4ef4-9af5-976ced3b2c6f"), "bd9e7f06f6424ef49af5976ced3b2c6f")
        self.assertEqual(run.normalize_id("  BD9E7F06F6424EF49AF5976CED3B2C6F  "), "bd9e7f06f6424ef49af5976ced3b2c6f")

    def test_load_pages_from_config(self):
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".json") as tmp:
            json.dump({"pages": ["page-1", "page-2"]}, tmp)
            tmp_path = tmp.name

        try:
            pages = run.load_pages_from_config(tmp_path)
            self.assertEqual(pages, ["page1", "page2"])
        finally:
            os.remove(tmp_path)

    def test_load_pages_from_config_missing_file(self):
        pages = run.load_pages_from_config("/non/existent/path.json")
        self.assertEqual(pages, [])

    def test_resolve_dry_run(self):
        # CLI flag takes precedence
        self.assertTrue(run.resolve_dry_run(True))
        self.assertFalse(run.resolve_dry_run(False))

        # Env vars when CLI flag is None
        with patch.dict(os.environ, {"INPUT_DRY_RUN": "false"}):
            self.assertFalse(run.resolve_dry_run(None))

        with patch.dict(os.environ, {"INPUT_DRY_RUN": "true"}):
            self.assertTrue(run.resolve_dry_run(None))

        with patch.dict(os.environ, {"DRY_RUN": "0"}, clear=True):
            self.assertFalse(run.resolve_dry_run(None))

        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(run.resolve_dry_run(None))

    def test_run_with_mocked_sync_page(self):
        mock_results = {
            "p1": {"page": "p1", "status": "updated", "blocks": 10},
            "p2": {"page": "p2", "status": "unchanged", "blocks": 0},
            "p3": {"page": "p3", "status": "no_access", "blocks": 0},
        }

        def fake_sync(page_id, dry_run=False):
            return mock_results.get(page_id, {"page": page_id, "status": "error", "blocks": 0})

        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".json") as tmp:
            json.dump({"pages": ["p1", "p2", "p3"]}, tmp)
            tmp_config = tmp.name

        try:
            with patch("tools.bilingual.run.sync_page", side_effect=fake_sync):
                exit_code = run.run(["--config", tmp_config, "--dry-run"])
                self.assertEqual(exit_code, 0)

                with open("summary.txt", "r", encoding="utf-8") as f:
                    summary = f.read()
                self.assertIn("3 pages processed", summary)
                self.assertIn("1 updated", summary)
                self.assertIn("1 unchanged", summary)
                self.assertIn("1 no_access", summary)
        finally:
            os.remove(tmp_config)
            if os.path.exists("summary.txt"):
                os.remove("summary.txt")

    def test_run_single_page_override(self):
        called_pages = []

        def fake_sync(page_id, dry_run=False):
            called_pages.append(page_id)
            return {"page": page_id, "status": "updated", "blocks": 5}

        with patch("tools.bilingual.run.sync_page", side_effect=fake_sync):
            exit_code = run.run(["--page", "custom-page-id", "--no-dry-run"])
            self.assertEqual(exit_code, 0)
            self.assertEqual(called_pages, ["custompageid"])

        if os.path.exists("summary.txt"):
            os.remove("summary.txt")

    def test_run_handles_sync_exception_gracefully(self):
        def failing_sync(page_id, dry_run=False):
            raise RuntimeError("API timeout")

        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".json") as tmp:
            json.dump({"pages": ["err-page"]}, tmp)
            tmp_config = tmp.name

        try:
            with patch("tools.bilingual.run.sync_page", side_effect=failing_sync):
                exit_code = run.run(["--config", tmp_config])
                self.assertEqual(exit_code, 0)
        finally:
            os.remove(tmp_config)
            if os.path.exists("summary.txt"):
                os.remove("summary.txt")


if __name__ == "__main__":
    unittest.main()
