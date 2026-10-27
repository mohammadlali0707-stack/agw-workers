#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/bilingual/translate.py.

Bilingual translator library for LeadDesk AI (Mohammad, 2026-10-27).
Issue #108: [bilingual 1/4] Translator library tools/bilingual/translate.py + tests

Features:
1. protect() / restore() with placeholders ⟦0⟧, ⟦1⟧... preserving URLs, code,
   /commands, @handles, emails, file paths, IDs like SDD-006, and words with underscores.
   restore() tolerates whitespace variations like ⟦ 0 ⟧ and Eastern Arabic/Persian digits.
2. translate() primary engine: Google single endpoint (POST form field 'q', max 4000 chars,
   join '[0][i][0]'), keeping line breaks.
   Fallback engine: Gemini API (GEMINI_API_KEY_1..4 rotating keys, model gemini-2.5-flash
   or newer, prompt: natural business Persian, output only the translation).
   Retry 2x with backoff; raises on failure.
3. In-run SHA1 cache.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from typing import Any, Mapping, Sequence
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger("bilingual.translate")

# ---------------------------------------------------------------------------
# In-run cache (dict indexed by sha1)
# ---------------------------------------------------------------------------
_CACHE: dict[str, str] = {}


def clear_cache() -> None:
    """Clear the in-run translation cache."""
    _CACHE.clear()


def get_cache() -> dict[str, str]:
    """Return reference to in-run translation cache."""
    return _CACHE


# ---------------------------------------------------------------------------
# Token Protection & Restoration
# ---------------------------------------------------------------------------
_PROTECT_PATTERNS = [
    # 1. Code blocks and inline code
    r"(?P<code>```[\s\S]*?```|`[^`\n]+`|<code>[\s\S]*?</code>)",
    # 2. URLs (strip trailing sentence punctuation)
    r'(?P<url>https?://[^\s<>"]+?(?=[.,!?:;)]?(?:\s|$)))',
    # 3. Emails
    r"(?P<email>[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.[a-zA-Z]{2,})",
    # 4. File paths: absolute /..., relative ./ ../, home ~/, multi-segment paths, or known extensions
    r"(?P<path>(?:(?:~|\.{1,2}|/)[a-zA-Z0-9_.-]*(?:/[a-zA-Z0-9_.-]+)+|[a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)+)(?<![.,!?:;])|\b[a-zA-Z0-9_-]+\.(?:py|json|ya?ml|md|txt|sh|ts|js|html|css|tgz|tar|gz|zip|png|jpe?g|gif|svg|pdf|lock|toml)\b)",
    # 5. /commands (e.g. /start, /sync_page)
    r"(?P<cmd>(?<![a-zA-Z0-9_])/[a-zA-Z0-9_-]+(?![a-zA-Z0-9_/-]))",
    # 6. @handles (e.g. @Mohammad, @agy)
    r"(?P<handle>(?<![a-zA-Z0-9_])@[a-zA-Z0-9_]+)",
    # 7. IDs like SDD-006, TASK-123
    r"(?P<id>\b[A-Za-z0-9]+-\d+\b|\b[A-Z]{2,}-[A-Z0-9-]+\b)",
    # 8. Words with underscores (e.g. hq_job, GEMINI_API_KEY_1, sync_page)
    r"(?P<underscore>(?<![a-zA-Z0-9])(?=[a-zA-Z0-9_]*[a-zA-Z0-9])[a-zA-Z0-9]*_[a-zA-Z0-9_]*(?![a-zA-Z0-9]))",
]

_COMBINED_PROTECT_RE = re.compile("|".join(_PROTECT_PATTERNS))

# Translation table to convert Persian and Arabic-Indic digits to standard ASCII digits
_EASTERN_DIGITS_TRANS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

# Match placeholder brackets ⟦...⟧ tolerating spaces and Eastern digits
_RESTORE_RE = re.compile(r"⟦\s*([0-9۰-۹٠-٩]+)\s*⟧")


def protect(text: str) -> tuple[str, list[str]]:
    """Replace protected tokens with placeholders '⟦0⟧, ⟦1⟧...'.

    Protected tokens include URLs, code, /commands, @handles, emails,
    file paths, IDs like SDD-006, and words with underscores.

    Args:
        text: The source string.

    Returns:
        A tuple of (masked_text, keep_list).
    """
    if not text:
        return "", []

    keep: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        idx = len(keep)
        keep.append(match.group(0))
        return f"⟦{idx}⟧"

    masked = _COMBINED_PROTECT_RE.sub(_replace, text)
    return masked, keep


def restore(masked: str, keep: Sequence[str] | Mapping[int | str, str]) -> str:
    """Restore placeholders '⟦0⟧, ⟦1⟧...' with their original protected tokens.

    Tolerates whitespace inside brackets (e.g. '⟦ 0 ⟧') and Eastern
    Arabic/Persian numerals (e.g. '⟦ ۰ ⟧').

    Args:
        masked: The string containing placeholders.
        keep: A sequence (list/tuple) or mapping (dict) containing preserved tokens.

    Returns:
        The string with original tokens restored.
    """
    if not masked:
        return masked

    def _replace(match: re.Match[str]) -> str:
        raw_digits = match.group(1).translate(_EASTERN_DIGITS_TRANS)
        try:
            idx = int(raw_digits)
        except ValueError:
            return match.group(0)

        if isinstance(keep, (list, tuple)):
            if 0 <= idx < len(keep):
                return keep[idx]
        elif isinstance(keep, Mapping):
            if idx in keep:
                return keep[idx]
            if str(idx) in keep:
                return keep[str(idx)]

        return match.group(0)

    return _RESTORE_RE.sub(_replace, masked)


# ---------------------------------------------------------------------------
# Text Chunking (max 4000 characters, preserves line breaks)
# ---------------------------------------------------------------------------
def _chunk_text(text: str, max_chars: int = 4000) -> list[str]:
    """Chunk text into segments of at most max_chars, keeping line breaks.

    Splits on newline boundaries where possible, or sentence boundaries if a
    single line exceeds max_chars.
    """
    if len(text) <= max_chars:
        return [text]

    lines = text.splitlines(keepends=True)
    chunks: list[str] = []
    current_chunk: list[str] = []
    current_len = 0

    for line in lines:
        if len(line) > max_chars:
            if current_chunk:
                chunks.append("".join(current_chunk))
                current_chunk = []
                current_len = 0

            # Split oversized line on sentence boundaries
            sub_parts = re.split(r"([.?!]+\s+)", line)
            sub_chunk = ""
            for part in sub_parts:
                if len(sub_chunk) + len(part) <= max_chars:
                    sub_chunk += part
                else:
                    if sub_chunk:
                        chunks.append(sub_chunk)
                        sub_chunk = ""
                    if len(part) <= max_chars:
                        sub_chunk = part
                    else:
                        # Slice if individual token exceeds max_chars
                        for i in range(0, len(part), max_chars):
                            chunks.append(part[i : i + max_chars])
            if sub_chunk:
                chunks.append(sub_chunk)
        elif current_len + len(line) <= max_chars:
            current_chunk.append(line)
            current_len += len(line)
        else:
            chunks.append("".join(current_chunk))
            current_chunk = [line]
            current_len = len(line)

    if current_chunk:
        chunks.append("".join(current_chunk))

    return chunks


# ---------------------------------------------------------------------------
# Google Single Endpoint Primary Engine
# ---------------------------------------------------------------------------
GOOGLE_TRANSLATE_URL = (
    "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl={tl}&dt=t"
)


def _call_google_translate(text: str, target: str = "fa", timeout: float = 20.0) -> str:
    """Call Google single translate endpoint via POST form field 'q'.

    Response format: JSON where parsed[0] contains segments [translated, original, ...].
    Segments are joined via res[0][i][0] to keep line breaks.
    """
    url = GOOGLE_TRANSLATE_URL.format(tl=urllib.parse.quote(target))
    data = urllib.parse.urlencode({"q": text}).encode("utf-8")
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko)",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept": "*/*",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")

    parsed = json.loads(body)
    if not parsed or not isinstance(parsed, list) or not parsed[0]:
        raise ValueError(f"Unexpected Google Translate response format: {body[:200]}")

    # Join [0][i][0] across all segments
    pieces = []
    for item in parsed[0]:
        if item and len(item) > 0 and item[0] is not None:
            pieces.append(str(item[0]))

    return "".join(pieces)


# ---------------------------------------------------------------------------
# Gemini Fallback Engine with Key Rotation
# ---------------------------------------------------------------------------
_gemini_key_index: int = 0


def reset_gemini_key_index() -> None:
    """Reset the Gemini API key rotation index to 0."""
    global _gemini_key_index
    _gemini_key_index = 0


def _get_gemini_keys() -> list[str]:
    """Retrieve non-empty Gemini API keys from environment."""
    keys: list[str] = []
    for i in range(1, 5):
        k = os.environ.get(f"GEMINI_API_KEY_{i}")
        if k and k.strip():
            keys.append(k.strip())
    if not keys:
        single = os.environ.get("GEMINI_API_KEY")
        if single and single.strip():
            keys.append(single.strip())
    return keys


def _get_next_gemini_key() -> str:
    """Get the next rotated Gemini API key from GEMINI_API_KEY_1..4."""
    global _gemini_key_index
    keys = _get_gemini_keys()
    if not keys:
        raise RuntimeError("No Gemini API key found in environment (GEMINI_API_KEY_1..4)")

    key = keys[_gemini_key_index % len(keys)]
    _gemini_key_index += 1
    return key


def _call_gemini_translate(
    text: str, target: str = "fa", timeout: float = 30.0
) -> str:
    """Translate text using Gemini API fallback with key rotation."""
    api_key = _get_next_gemini_key()
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={urllib.parse.quote(api_key)}"

    lang_desc = "natural business Persian" if target == "fa" else f"natural business {target}"
    prompt = (
        f"Translate the following text into {lang_desc}. "
        "Keep placeholders like ⟦0⟧, ⟦1⟧, ⟦ 0 ⟧, markdown syntax, and line breaks exactly as they are. "
        "Output only the translation, with no explanation or extra text:\n\n"
        f"{text}"
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt,
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
        },
    }

    req_data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "agw-bilingual-translator",
    }
    req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")

    parsed = json.loads(body)
    candidates = parsed.get("candidates", [])
    if not candidates:
        raise ValueError(f"Empty candidates in Gemini response: {body[:200]}")

    content = candidates[0].get("content", {})
    parts = content.get("parts", [])
    if not parts or "text" not in parts[0]:
        raise ValueError(f"No text part in Gemini response: {body[:200]}")

    translated_text = parts[0]["text"]
    return translated_text


# ---------------------------------------------------------------------------
# Chunk Translation with Retries & Fallback
# ---------------------------------------------------------------------------
def _translate_chunk(
    chunk: str,
    target: str = "fa",
    max_retries: int = 2,
    backoff: float = 0.1,
) -> str:
    """Translate a single chunk <= 4000 chars.

    Tries Google endpoint with up to max_retries backoff attempts.
    Falls back to Gemini API with key rotation and up to max_retries backoff.
    Raises RuntimeError on total failure.
    """
    google_err: Exception | None = None

    # 1. Primary Engine: Google
    for attempt in range(max_retries + 1):
        try:
            return _call_google_translate(chunk, target=target)
        except Exception as e:
            google_err = e
            logger.warning(
                "Google Translate attempt %d/%d failed: %s",
                attempt + 1,
                max_retries + 1,
                e,
            )
            if attempt < max_retries and backoff > 0:
                time.sleep(backoff * (2**attempt))

    # 2. Fallback Engine: Gemini
    logger.info("Falling back to Gemini API for translation...")
    gemini_err: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return _call_gemini_translate(chunk, target=target)
        except Exception as e:
            gemini_err = e
            logger.warning(
                "Gemini Translate attempt %d/%d failed: %s",
                attempt + 1,
                max_retries + 1,
                e,
            )
            if attempt < max_retries and backoff > 0:
                time.sleep(backoff * (2**attempt))

    raise RuntimeError(
        f"Translation failed on both engines after retries. Google: {google_err}; Gemini: {gemini_err}"
    )


# ---------------------------------------------------------------------------
# Main Public Interface
# ---------------------------------------------------------------------------
def translate(
    text: str,
    target: str = "fa",
    max_retries: int = 2,
    backoff: float | None = None,
) -> str:
    """Translate text to target language (default 'fa' for Persian).

    Protects special tokens (links, code, /commands, @handles, emails,
    file paths, IDs like SDD-006, words with underscores).
    Chunks text at <= 4000 characters while keeping line breaks.
    Uses Google single endpoint as primary engine, falling back to Gemini API
    with key rotation (GEMINI_API_KEY_1..4).
    Caches translations in an in-run dictionary indexed by sha1.

    Args:
        text: Source text.
        target: Target language code ('fa' default).
        max_retries: Number of retries per engine (default 2).
        backoff: Base backoff sleep seconds (default from TRANSLATE_BACKOFF or 0.1s).

    Returns:
        Translated text with all protected tokens restored.
    """
    if not text:
        return text

    if backoff is None:
        try:
            backoff = float(os.environ.get("TRANSLATE_BACKOFF", "0.1"))
        except ValueError:
            backoff = 0.1

    # In-run cache check (by sha1)
    if target == "fa":
        cache_key = hashlib.sha1(text.encode("utf-8")).hexdigest()
    else:
        cache_key = hashlib.sha1(f"{target}:{text}".encode("utf-8")).hexdigest()

    if cache_key in _CACHE:
        return _CACHE[cache_key]

    # 1. Protect special tokens
    masked, keep = protect(text)

    # 2. Chunk masked text at <= 4000 chars
    chunks = _chunk_text(masked, max_chars=4000)

    # 3. Translate each chunk
    translated_chunks = [
        _translate_chunk(chunk, target=target, max_retries=max_retries, backoff=backoff)
        for chunk in chunks
    ]
    translated_masked = "".join(translated_chunks)

    # 4. Restore protected tokens
    result = restore(translated_masked, keep)

    # 5. Cache result
    _CACHE[cache_key] = result
    return result


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        sample_input = " ".join(sys.argv[1:])
        print(f"Translating: {sample_input}")
        print(translate(sample_input))
    else:
        print("Usage: translate.py <text>")
