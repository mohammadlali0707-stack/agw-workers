#!/usr/bin/env python3
"""Notion page Persian toggle synchronization (Part 2).

Interface:
  sync_page(page_id: str, dry_run: bool = False) -> dict
  Returns: {'page': id, 'status': 'updated|unchanged|no_access|error', 'blocks': n}

Rules:
- Never change or delete English content.
- Persian lives inside ONE toggle block at the top titled '🇮🇷 نسخه فارسی (خودکار)'.
- Hidden marker paragraph 'fa-sync:<sha1 of English text>' as its last child.
- Skip page when hash is unchanged.
- Code blocks are copied, not translated.
"""

import hashlib
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

NOTION_VERSION = "2022-06-28"
NOTION_API = "https://api.notion.com/v1"
TOGGLE_TITLE = "🇮🇷 نسخه فارسی (خودکار)"
MARKER_PREFIX = "fa-sync:"

_last_request_time = [0.0]


def _rate_limit():
    gap = time.time() - _last_request_time[0]
    if gap < 0.34:
        time.sleep(0.34 - gap)
    _last_request_time[0] = time.time()


def _notion_req(method: str, path: str, token: str, body: dict = None, _tries: int = 0) -> dict:
    _rate_limit()
    url = f"{NOTION_API}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Notion-Version", NOTION_VERSION)
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429 and _tries < 5:
            wait = float(e.headers.get("Retry-After", 2))
            time.sleep(wait)
            return _notion_req(method, path, token, body, _tries + 1)
        if e.code in (500, 502, 503, 504) and _tries < 3:
            time.sleep(2 ** _tries)
            return _notion_req(method, path, token, body, _tries + 1)
        raise


def _extract_plain_text(rich_text_list: list) -> str:
    return "".join(t.get("plain_text", "") for t in rich_text_list)


def _get_page_blocks(page_id: str, token: str) -> list:
    blocks = []
    cursor = None
    while True:
        path = f"/blocks/{page_id}/children?page_size=100"
        if cursor:
            path += f"&start_cursor={cursor}"
        res = _notion_req("GET", path, token)
        blocks.extend(res.get("results", []))
        if not res.get("has_more"):
            break
        cursor = res.get("next_cursor")
    return blocks


def _get_block_children(block_id: str, token: str) -> list:
    children = []
    cursor = None
    while True:
        path = f"/blocks/{block_id}/children?page_size=100"
        if cursor:
            path += f"&start_cursor={cursor}"
        res = _notion_req("GET", path, token)
        children.extend(res.get("results", []))
        if not res.get("has_more"):
            break
        cursor = res.get("next_cursor")
    return children


def _extract_english_text_and_blocks(blocks: list, token: str) -> tuple[str, list, dict | None, str | None]:
    english_blocks = []
    text_chunks = []
    existing_toggle = None
    existing_hash = None

    for b in blocks:
        b_type = b.get("type", "")
        # Check if this is the Persian toggle block
        if b_type == "toggle":
            rt = b.get("toggle", {}).get("rich_text", [])
            title = _extract_plain_text(rt).strip()
            if title == TOGGLE_TITLE:
                existing_toggle = b
                # Fetch toggle children to read marker
                children = _get_block_children(b["id"], token)
                if children:
                    last_child = children[-1]
                    if last_child.get("type") == "paragraph":
                        p_rt = last_child.get("paragraph", {}).get("rich_text", [])
                        p_txt = _extract_plain_text(p_rt).strip()
                        if p_txt.startswith(MARKER_PREFIX):
                            existing_hash = p_txt[len(MARKER_PREFIX):].strip()
                continue

        english_blocks.append(b)
        content_obj = b.get(b_type, {})
        rt = content_obj.get("rich_text", [])
        txt = _extract_plain_text(rt)
        if txt:
            text_chunks.append(txt)

    combined_text = "\n".join(text_chunks)
    return combined_text, english_blocks, existing_toggle, existing_hash


def _translate_block_content(block: dict) -> dict | None:
    b_type = block.get("type", "")
    content = block.get(b_type, {})

    if b_type == "code":
        # Rule: Code blocks are copied, not translated
        return {
            "object": "block",
            "type": "code",
            "code": {
                "language": content.get("language", "plain text"),
                "rich_text": content.get("rich_text", []),
            }
        }

    # Translatable block types with rich_text
    if b_type in ("paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list_item",
                  "numbered_list_item", "quote", "callout"):
        rt = content.get("rich_text", [])
        raw_text = _extract_plain_text(rt)
        if not raw_text.strip():
            return None

        translated_text = translate(raw_text, target="fa")
        new_block = {
            "object": "block",
            "type": b_type,
            b_type: {
                "rich_text": [{"type": "text", "text": {"content": translated_text[:2000]}}]
            }
        }
        if b_type == "callout" and "icon" in content:
            new_block[b_type]["icon"] = content["icon"]
        return new_block

    if b_type == "divider":
        return {"object": "block", "type": "divider", "divider": {}}

    return None


def sync_page(page_id: str, dry_run: bool = False, token: str = None) -> dict:
    """Synchronize a Notion page with its Persian toggle block."""
    norm_page_id = page_id.replace("-", "")
    token = token or os.environ.get("NOTION_PAT", "")

    if not token and not dry_run:
        return {"page": page_id, "status": "no_access", "blocks": 0}

    try:
        blocks = _get_page_blocks(norm_page_id, token)
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return {"page": page_id, "status": "no_access", "blocks": 0}
        return {"page": page_id, "status": "error", "blocks": 0}
    except Exception:
        return {"page": page_id, "status": "error", "blocks": 0}

    combined_text, english_blocks, existing_toggle, existing_hash = _extract_english_text_and_blocks(blocks, token)
    current_sha1 = hashlib.sha1(combined_text.encode("utf-8")).hexdigest()

    if existing_hash == current_sha1:
        return {"page": page_id, "status": "unchanged", "blocks": len(english_blocks)}

    if dry_run:
        return {"page": page_id, "status": "updated", "blocks": len(english_blocks)}

    # Build translated blocks
    translated_children = []
    for b in english_blocks:
        tb = _translate_block_content(b)
        if tb:
            translated_children.append(tb)

    # Marker paragraph as the last child
    marker_block = {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": f"{MARKER_PREFIX}{current_sha1}"}}]
        }
    }
    translated_children.append(marker_block)

    try:
        # Delete old toggle if present
        if existing_toggle:
            _notion_req("DELETE", f"/blocks/{existing_toggle['id']}", token)

        # Create new toggle block
        toggle_payload = {
            "children": [
                {
                    "object": "block",
                    "type": "toggle",
                    "toggle": {
                        "rich_text": [{"type": "text", "text": {"content": TOGGLE_TITLE}}],
                        "children": translated_children[:100],  # Notion max 100 children per call
                    }
                }
            ]
        }
        _notion_req("PATCH", f"/blocks/{norm_page_id}/children", token, body=toggle_payload)
        return {"page": page_id, "status": "updated", "blocks": len(translated_children)}
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return {"page": page_id, "status": "no_access", "blocks": 0}
        return {"page": page_id, "status": "error", "blocks": 0}
    except Exception:
        return {"page": page_id, "status": "error", "blocks": 0}


if __name__ == "__main__":
    if len(sys.argv) > 1:
        p_id = sys.argv[1]
        dry = "--dry-run" in sys.argv
        res = sync_page(p_id, dry_run=dry)
        print(json.dumps(res, indent=2))
