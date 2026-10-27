#!/usr/bin/env python3
"""Tests for shared bilingual interfaces: translate.py and notion_fa.py."""

import unittest
from unittest.mock import patch, MagicMock

from tools.bilingual.translate import protect, restore, translate
from tools.bilingual.notion_fa import sync_page


class TestTranslate(unittest.TestCase):

    def test_protect_and_restore_handles_links_code_ids_emojis(self):
        sample = (
            "Review task SDD-006 at https://notion.so/item?id=42 for @agy with /goal. "
            "Inspect tools/bilingual/translate.py with 150 items. Code: `sync_page()` "
            "and ```python\nprint('hello')\n```. Flag: 🇮🇷."
        )
        masked, keep = protect(sample)

        # Ensure protected tokens are extracted into keep
        self.assertTrue(any("SDD-006" in k for k in keep))
        self.assertTrue(any("https://notion.so/item?id=42" in k for k in keep))
        self.assertTrue(any("@agy" in k for k in keep))
        self.assertTrue(any("/goal" in k for k in keep))
        self.assertTrue(any("tools/bilingual/translate.py" in k for k in keep))
        self.assertTrue(any("150" in k for k in keep))
        self.assertTrue(any("sync_page()" in k for k in keep))

        # Restoring should yield the exact original string
        restored = restore(masked, keep)
        self.assertEqual(restored, sample)

    def test_translate_fallback_offline(self):
        # Without Gemini API keys, translate gracefully preserves text and protected tokens
        with patch.dict("os.environ", {}, clear=True):
            res = translate("Test SDD-006 title")
            self.assertIn("SDD-006", res)


class TestNotionFa(unittest.TestCase):

    @patch("tools.bilingual.notion_fa._get_page_blocks")
    @patch("tools.bilingual.notion_fa._get_block_children")
    def test_sync_page_unchanged_when_hash_matches(self, mock_children, mock_blocks):
        # Mock an existing page with a Persian toggle whose marker matches the English sha1
        english_block = {
            "id": "b1",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": "Hello world"}]}
        }
        # English text sha1
        import hashlib
        h = hashlib.sha1(b"Hello world").hexdigest()

        toggle_block = {
            "id": "toggle-1",
            "type": "toggle",
            "toggle": {"rich_text": [{"plain_text": "🇮🇷 نسخه فارسی (خودکار)"}]}
        }
        marker_child = {
            "id": "marker-1",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": f"fa-sync:{h}"}]}
        }

        mock_blocks.return_value = [toggle_block, english_block]
        mock_children.return_value = [marker_child]

        res = sync_page("page-123", dry_run=False, token="dummy-token")
        self.assertEqual(res["status"], "unchanged")


if __name__ == "__main__":
    unittest.main()
