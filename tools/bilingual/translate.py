#!/usr/bin/env python3
"""Shared translation interface for Bilingual Notion (Part 1).

Interface:
  translate(text: str, target: str = 'fa') -> str
  protect(text: str) -> tuple[str, list[str]]
  restore(masked: str, keep: list[str]) -> str

Rules:
- Never change or delete protected elements:
  links, @handles, /commands, code, file paths, IDs (SDD-006, hq_*), numbers, emojis.
- Code blocks are copied as-is, not translated.
- Uses Gemini API with keys GEMINI_API_KEY_1..4 or GEMINI_API_KEY. Never prints keys.
"""

import json
import os
import re
import urllib.error
import urllib.request

# Regexes in order of protection precedence
PROTECT_PATTERNS = [
    # Multi-line code blocks
    re.compile(r"```[\w-]*\n[\s\S]*?```"),
    # Inline code
    re.compile(r"`[^`\n]+`"),
    # URLs
    re.compile(r"https?://[^\s)\]>\"']+"),
    # @handles
    re.compile(r"@[a-zA-Z0-9_.-]+"),
    # Specific IDs (e.g. SDD-006, hq_*, TBS-*)
    re.compile(r"\b(?:SDD|TBS)-\w+\b|\bhq_\w+\b"),
    # File paths (relative with / or absolute)
    re.compile(r"(?:/?[a-zA-Z0-9_.-]+|\.?\.)(?:/[a-zA-Z0-9_.-]+)+"),
    # /commands (e.g. /goal, /schedule)
    re.compile(r"/[a-zA-Z0-9_-]+(?=\s|$|[.,;:!?])"),
    # Emojis (common unicode emoji blocks)
    re.compile(r"[\U00010000-\U0010ffff\u2600-\u27bf\u2300-\u23ff\u2b50\u2b55]"),
    # Numbers (integers or decimals)
    re.compile(r"\b\d+(?:\.\d+)?\b"),
]


def protect(text: str) -> tuple[str, list[str]]:
    """Mask tokens that must remain untranslated and return (masked_text, keep_list)."""
    if not text:
        return text, []

    keep = []

    def _replace_match(match: re.Match) -> str:
        token = match.group(0)
        idx = len(keep)
        keep.append(token)
        return f"__KEEP_{idx}__"

    masked = text
    for pattern in PROTECT_PATTERNS:
        masked = pattern.sub(_replace_match, masked)

    return masked, keep


def restore(masked: str, keep: list[str]) -> str:
    """Restore masked tokens from keep_list."""
    if not masked or not keep:
        return masked

    def _replacer(match: re.Match) -> str:
        try:
            idx = int(match.group(1))
            if 0 <= idx < len(keep):
                return keep[idx]
        except (ValueError, IndexError):
            pass
        return match.group(0)

    # First regex pass handling potential LLM whitespace around tokens
    restored = re.sub(r"__\s*KEEP_\s*(\d+)\s*__", _replacer, masked)

    # Direct fallback pass in case regex missed exact tokens
    for i, token in enumerate(keep):
        placeholder = f"__KEEP_{i}__"
        if placeholder in restored:
            restored = restored.replace(placeholder, token)

    return restored


def _get_gemini_keys() -> list[str]:
    """Retrieve Gemini API keys without logging them."""
    keys = []
    for var in ("GEMINI_API_KEY_1", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY_4", "GEMINI_API_KEY"):
        val = os.environ.get(var, "").strip()
        if val and val not in keys:
            keys.append(val)
    return keys


def _call_gemini_translate(masked_text: str, target: str = "fa") -> str:
    """Translate masked text via Gemini API, rotating available keys."""
    keys = _get_gemini_keys()
    if not keys:
        # Offline or test stub fallback
        return masked_text

    prompt = (
        f"You are a professional translator for technical documentation and project management software. "
        f"Translate the following text into natural, accurate Persian ({target}).\n"
        f"CRITICAL RULES:\n"
        f"1. DO NOT translate, change, or remove placeholder tokens matching __KEEP_<number>__.\n"
        f"2. Return ONLY the translation, with no quotes, commentary, or explanation.\n\n"
        f"Text to translate:\n{masked_text}"
    )

    payload = json.dumps({
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 2048,
        }
    }).encode("utf-8")

    models = ["gemini-1.5-flash", "gemini-2.0-flash", "gemini-1.5-pro"]

    last_err = None
    for key in keys:
        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            return parts[0].get("text", "").strip()
            except urllib.error.HTTPError as e:
                last_err = e
                # Quota or rate limit: try next key
                if e.code in (429, 403, 503):
                    break
                continue
            except Exception as e:
                last_err = e
                continue

    # Fallback if all API calls failed
    if last_err:
        return masked_text
    return masked_text


def translate(text: str, target: str = "fa") -> str:
    """Translate text to target language (default 'fa'), preserving protected tokens."""
    if not text or not text.strip():
        return text

    masked, keep = protect(text)
    translated_masked = _call_gemini_translate(masked, target=target)
    return restore(translated_masked, keep)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        input_text = " ".join(sys.argv[1:])
        print(translate(input_text))
