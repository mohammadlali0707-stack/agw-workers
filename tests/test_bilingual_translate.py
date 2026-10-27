#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_bilingual_translate.py.

Unit tests for tools/bilingual/translate.py (Issue #108).
All tests use stubbed HTTP (no network access).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, call, patch
import urllib.error
import urllib.parse

# Ensure repository root is on sys.path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools.bilingual.translate import (
    _CACHE,
    _call_gemini_translate,
    _call_google_translate,
    _chunk_text,
    _get_gemini_keys,
    _get_next_gemini_key,
    clear_cache,
    get_cache,
    protect,
    reset_gemini_key_index,
    restore,
    translate,
)


class MockHTTPResponse:
    """Mock context-manager response for urllib.request.urlopen."""

    def __init__(self, data: str | bytes, status: int = 200):
        if isinstance(data, str):
            self._data = data.encode("utf-8")
        else:
            self._data = data
        self.status = status

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class TestBilingualTranslate(unittest.TestCase):
    """Test suite for translator library, placeholder masking, and fallbacks."""

    def setUp(self):
        clear_cache()
        reset_gemini_key_index()
        # Ensure fast tests without sleeping
        os.environ["TRANSLATE_BACKOFF"] = "0.0"

    def tearDown(self):
        clear_cache()
        reset_gemini_key_index()

    # -----------------------------------------------------------------------
    # 1. Protect & Restore Unit Tests
    # -----------------------------------------------------------------------
    def test_protect_replaces_all_target_tokens(self):
        sample = (
            "Hello @Mohammad and @agy! Check SDD-006 at https://example.com/api?user=1. "
            "Run /sync_page or /start on tools/bilingual/translate.py or /etc/hosts. "
            "Send reports to team@leaddesk.ai. "
            "Keep hq_job and GEMINI_API_KEY_1 unchanged. "
            "Here is `inline_code = 123` and block:\n"
            "```python\ndef test():\n    return True\n```\n"
        )
        masked, keep = protect(sample)

        # Placeholders must follow ⟦0⟧, ⟦1⟧...
        for i in range(len(keep)):
            self.assertIn(f"⟦{i}⟧", masked)

        # Verify all target categories are in keep
        self.assertIn("@Mohammad", keep)
        self.assertIn("@agy", keep)
        self.assertIn("SDD-006", keep)
        self.assertIn("https://example.com/api?user=1", keep)
        self.assertIn("/sync_page", keep)
        self.assertIn("/start", keep)
        self.assertIn("tools/bilingual/translate.py", keep)
        self.assertIn("/etc/hosts", keep)
        self.assertIn("team@leaddesk.ai", keep)
        self.assertIn("hq_job", keep)
        self.assertIn("GEMINI_API_KEY_1", keep)
        self.assertIn("`inline_code = 123`", keep)

        # Restore must reproduce the exact original text
        restored = restore(masked, keep)
        self.assertEqual(restored, sample)

    def test_restore_tolerates_spaces_and_eastern_digits(self):
        keep = ["SDD-006", "https://example.com", "@Mohammad", "hq_var"]
        # Standard placeholder, spaced placeholder '⟦ 1 ⟧', Persian digit '⟦ ۲ ⟧', spaced Arabic '⟦  ۳  ⟧'
        masked_variations = (
            "Task ⟦0⟧ at ⟦ 1 ⟧ assigned to ⟦ ۲ ⟧ with ⟦  ۳  ⟧."
        )
        restored = restore(masked_variations, keep)
        expected = "Task SDD-006 at https://example.com assigned to @Mohammad with hq_var."
        self.assertEqual(restored, expected)

    def test_restore_supports_dict_keep(self):
        keep_dict = {0: "SDD-006", 1: "@Mohammad"}
        masked = "Assigned ⟦0⟧ to ⟦1⟧"
        restored = restore(masked, keep_dict)
        self.assertEqual(restored, "Assigned SDD-006 to @Mohammad")

    def test_protect_empty_string(self):
        masked, keep = protect("")
        self.assertEqual(masked, "")
        self.assertEqual(keep, [])
        self.assertEqual(restore("", []), "")

    # -----------------------------------------------------------------------
    # 2. Text Chunking Tests
    # -----------------------------------------------------------------------
    def test_long_text_is_chunked_preserving_line_breaks(self):
        line = "This is a detailed sentence about LeadDesk AI features.\n"
        # 100 repetitions is ~5600 chars > 4000 max_chars
        long_text = line * 100
        chunks = _chunk_text(long_text, max_chars=4000)

        self.assertGreater(len(chunks), 1)
        for i, chunk in enumerate(chunks):
            self.assertLessEqual(
                len(chunk),
                4000,
                f"Chunk {i} exceeded 4000 chars: {len(chunk)}",
            )

        # Reconstructed chunks must equal original text, preserving line breaks
        self.assertEqual("".join(chunks), long_text)

    @patch("urllib.request.urlopen")
    def test_translate_chunks_long_text_and_preserves_placeholders(self, mock_urlopen):
        # Create a text > 4000 chars with line breaks and protected tokens
        para = "Section SDD-006: Details at https://example.com/doc with @Mohammad and /start.\n"
        long_text = para * 120  # ~9500 chars (masked ~6400 chars > 4000)
        self.assertGreater(len(long_text), 4000)

        def mock_translate_call(req, *args, **kwargs):
            form_data = urllib.parse.parse_qs(req.data.decode("utf-8"))
            q_text = form_data["q"][0]
            # Mock Persian translation by adding prefix and keeping placeholders intact
            mock_persian = "بخش: " + q_text
            resp_json = json.dumps([[[mock_persian, q_text, None, None, 1]]])
            return MockHTTPResponse(resp_json)

        mock_urlopen.side_effect = mock_translate_call

        result = translate(long_text, target="fa")

        # Must have made at least 2 chunk calls
        self.assertGreaterEqual(mock_urlopen.call_count, 2)
        # All tokens restored in output
        self.assertIn("SDD-006", result)
        self.assertIn("https://example.com/doc", result)
        self.assertIn("@Mohammad", result)
        self.assertIn("/start", result)
        self.assertIn("\n", result)

    # -----------------------------------------------------------------------
    # 3. Google Translation Success & Placeholders Surviving
    # -----------------------------------------------------------------------
    @patch("urllib.request.urlopen")
    def test_google_translate_success_and_placeholders_survive(self, mock_urlopen):
        # Input with special tokens
        text = "Hello @Mohammad, please check SDD-006 at https://leaddesk.ai."

        # Google returns response with placeholders ⟦0⟧, ⟦1⟧, ⟦2⟧ intact
        google_json = json.dumps(
            [
                [
                    ["سلام ⟦0⟧، لطفاً ⟦1⟧ را در ⟦2⟧ بررسی کنید.", "Hello ⟦0⟧, please check ⟦1⟧ at ⟦2⟧.", None, None, 1]
                ]
            ]
        )
        mock_urlopen.return_value = MockHTTPResponse(google_json)

        result = translate(text, target="fa")

        # Verify Google endpoint was called with POST and form field 'q'
        self.assertEqual(mock_urlopen.call_count, 1)
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.get_method(), "POST")
        self.assertIn("translate.googleapis.com", req.full_url)
        self.assertIn("tl=fa", req.full_url)
        form_data = urllib.parse.parse_qs(req.data.decode("utf-8"))
        self.assertIn("q", form_data)

        # Verify placeholders survived and were restored to original tokens
        self.assertIn("@Mohammad", result)
        self.assertIn("SDD-006", result)
        self.assertIn("https://leaddesk.ai", result)
        self.assertEqual(result, "سلام @Mohammad، لطفاً SDD-006 را در https://leaddesk.ai بررسی کنید.")

    @patch("urllib.request.urlopen")
    def test_google_translate_multi_segment_keeps_line_breaks(self, mock_urlopen):
        text = "First paragraph line 1.\nSecond paragraph line 2."
        # Google returns segments joined across res[0][i][0]
        google_json = json.dumps(
            [
                [
                    ["پاراگراف اول سطر ۱.\n", "First paragraph line 1.\n", None, None, 1],
                    ["پاراگراف دوم سطر ۲.", "Second paragraph line 2.", None, None, 1],
                ]
            ]
        )
        mock_urlopen.return_value = MockHTTPResponse(google_json)

        result = translate(text, target="fa")
        self.assertIn("\n", result)
        self.assertEqual(result, "پاراگراف اول سطر ۱.\nپاراگراف دوم سطر ۲.")

    # -----------------------------------------------------------------------
    # 4. Fallback Engine (Gemini) When Google Fails
    # -----------------------------------------------------------------------
    @patch.dict(
        os.environ,
        {
            "GEMINI_API_KEY_1": "test-key-1",
            "GEMINI_API_KEY_2": "test-key-2",
        },
        clear=False,
    )
    @patch("urllib.request.urlopen")
    def test_fallback_to_gemini_when_google_fails(self, mock_urlopen):
        text = "Welcome to LeadDesk AI."

        def urlopen_side_effect(req, *args, **kwargs):
            if "translate.googleapis.com" in req.full_url:
                # Primary Google engine fails
                raise urllib.error.URLError("503 Service Unavailable")
            elif "generativelanguage.googleapis.com" in req.full_url:
                # Gemini endpoint succeeds
                gemini_resp = {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {"text": "به هوش مصنوعی LeadDesk خوش آمدید."}
                                ]
                            }
                        }
                    ]
                }
                return MockHTTPResponse(json.dumps(gemini_resp))
            raise ValueError(f"Unexpected URL: {req.full_url}")

        mock_urlopen.side_effect = urlopen_side_effect

        result = translate(text, target="fa", max_retries=1, backoff=0.0)

        # Result should be from Gemini fallback
        self.assertEqual(result, "به هوش مصنوعی LeadDesk خوش آمدید.")

        # Ensure both Google and Gemini endpoints were hit
        urls_called = [call[0][0].full_url for call in mock_urlopen.call_args_list]
        self.assertTrue(any("translate.googleapis.com" in u for u in urls_called))
        self.assertTrue(any("generativelanguage.googleapis.com" in u for u in urls_called))

    # -----------------------------------------------------------------------
    # 5. Gemini Key Rotation Tests
    # -----------------------------------------------------------------------
    @patch.dict(
        os.environ,
        {
            "GEMINI_API_KEY_1": "key-alpha",
            "GEMINI_API_KEY_2": "key-beta",
            "GEMINI_API_KEY_3": "key-gamma",
        },
        clear=False,
    )
    def test_gemini_key_rotation(self):
        reset_gemini_key_index()
        keys_found = _get_gemini_keys()
        self.assertEqual(keys_found, ["key-alpha", "key-beta", "key-gamma"])

        k1 = _get_next_gemini_key()
        k2 = _get_next_gemini_key()
        k3 = _get_next_gemini_key()
        k4 = _get_next_gemini_key()

        self.assertEqual(k1, "key-alpha")
        self.assertEqual(k2, "key-beta")
        self.assertEqual(k3, "key-gamma")
        self.assertEqual(k4, "key-alpha")  # Rotates back to start

    # -----------------------------------------------------------------------
    # 6. Retry & Raise On Failure
    # -----------------------------------------------------------------------
    @patch.dict(os.environ, {"GEMINI_API_KEY_1": "test-key-1"}, clear=False)
    @patch("urllib.request.urlopen")
    def test_retry_and_raise_on_total_failure(self, mock_urlopen):
        # Both Google and Gemini fail persistently
        mock_urlopen.side_effect = urllib.error.URLError("Network unreachable")

        with self.assertRaises(RuntimeError) as ctx:
            translate("This will fail.", max_retries=2, backoff=0.0)

        self.assertIn("Translation failed on both engines after retries", str(ctx.exception))
        # 1 initial + 2 retries = 3 attempts on Google, plus 3 attempts on Gemini = 6 calls
        self.assertEqual(mock_urlopen.call_count, 6)

    # -----------------------------------------------------------------------
    # 7. In-run SHA1 Cache Tests
    # -----------------------------------------------------------------------
    @patch("urllib.request.urlopen")
    def test_in_run_sha1_cache(self, mock_urlopen):
        text = "Cache test string."
        expected_sha1 = hashlib.sha1(text.encode("utf-8")).hexdigest()

        google_json = json.dumps([[["رشته آزمایشی حافظه پنهان.", text, None, None, 1]]])
        mock_urlopen.return_value = MockHTTPResponse(google_json)

        # 1st call: cache miss, calls urlopen
        res1 = translate(text, target="fa")
        self.assertEqual(res1, "رشته آزمایشی حافظه پنهان.")
        self.assertEqual(mock_urlopen.call_count, 1)

        # Verify entry in _CACHE by sha1
        cache = get_cache()
        self.assertIn(expected_sha1, cache)
        self.assertEqual(cache[expected_sha1], "رشته آزمایشی حافظه پنهان.")

        # 2nd call: cache hit, no additional HTTP call
        res2 = translate(text, target="fa")
        self.assertEqual(res2, "رشته آزمایشی حافظه پنهان.")
        self.assertEqual(mock_urlopen.call_count, 1)

    # -----------------------------------------------------------------------
    # 8. Empty / Whitespace Inputs
    # -----------------------------------------------------------------------
    def test_empty_string_returns_empty_without_http(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            self.assertEqual(translate(""), "")
            self.assertEqual(mock_urlopen.call_count, 0)


if __name__ == "__main__":
    unittest.main()
