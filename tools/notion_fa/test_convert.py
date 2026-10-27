"""Stub tests (no network): python3 -m tools.notion_fa.test_convert"""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__)); import convert as C
calls = []; created = {}
def rt(t, **ann): return {"type": "text", "text": {"content": t, "link": None}, "plain_text": t, "annotations": {"bold": False, "italic": False, "strikethrough": False, "underline": False, "code": False, "color": "default", **ann}}
PAGE = {"id": "p1", "object": "page", "url": "https://www.notion.so/p1", "parent": {"type": "page_id", "page_id": "hq"}, "properties": {"Name": {"type": "title", "title": [rt("Daily routine for agents")]}}}
BLOCKS = [
 {"id": "b1", "type": "heading_2", "heading_2": {"rich_text": [rt("9) Change Log")]}, "has_children": False},
 {"id": "b2", "type": "paragraph", "paragraph": {"rich_text": [rt("Run "), rt("/plan", code=True), rt(" every morning and check "), rt("SDD-006", bold=True), rt(" at https://x.y/z.")]}, "has_children": False},
 {"id": "b3", "type": "paragraph", "paragraph": {"rich_text": [rt("این متن از قبل فارسی است.")]}, "has_children": False},
 {"id": "b4", "type": "toggle", "toggle": {"rich_text": [rt("Details")]}, "has_children": True, "_kids_src": [{"id": "b5", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [rt("Ask the fleet to fix it")]}, "has_children": False}]},
 {"id": "b6", "type": "code", "code": {"rich_text": [rt("print('hello world')")], "language": "python"}, "has_children": False},
]
def fake_n(method, path, body=None, tries=6):
    calls.append((method, path, body))
    if path.startswith("/blocks/p1/children") and method == "GET": return {"results": BLOCKS, "has_more": False}
    if path.startswith("/blocks/b4/children") and method == "GET": return {"results": next(b for b in BLOCKS if b["id"] == "b4")["_kids_src"], "has_more": False}
    if path == "/pages" and method == "POST": created["m"] = body; return {"id": "m1-0000"}
    if method == "PATCH" and path.endswith("/children"): return {"results": [{"id": f"new{i}"} for i, _ in enumerate(body["children"])]}
    if method == "GET" and path.startswith("/pages/"): return {"parent": {"type": "workspace"}}
    return {"ok": True}
def fake_gemini(items, tl):
    out = {}
    for it in items:
        t = it["text"]
        out[it["i"]] = ("FA:" if tl == "fa" else "EN:") + t   # keeps tags/placeholders like a good model
    return out
C.n = fake_n; C.gemini = fake_gemini
C.process(PAGE, "pilot")
patch = {p.split("/")[2]: b for m, p, b in calls if m == "PATCH" and p.startswith("/blocks/b")}
assert "b1" not in patch, "anchor heading must stay"
assert "b3" not in patch, "Persian block must stay"
assert "b6" not in patch, "code block must stay"
b2 = patch["b2"]["paragraph"]["rich_text"]; txt = "".join(x["text"]["content"] if x["type"] == "text" else "" for x in b2)
assert "/plan" in txt and "SDD-006" in txt and "https://x.y/z." in txt, txt
assert any(x.get("annotations", {}).get("code") and x["text"]["content"] == "/plan" for x in b2), "code annotation kept"
assert "b5" in patch, "nested block translated"
mirror_children = [b for m, p, b in calls if m == "PATCH" and p.startswith("/blocks/m10000/children") or (m == "PATCH" and p.startswith("/blocks/m1-0000/children"))]
first = [b for m, p, b in calls if m == "PATCH" and p == "/blocks/p1/children"]
assert first and first[-1]["position"] == {"type": "start"} and first[-1]["children"][0]["paragraph"]["rich_text"][0]["text"]["content"] == "🇬🇧 English"
title = [b for m, p, b in calls if m == "PATCH" and p == "/pages/p1"]
assert title and "FA:" in title[0]["properties"]["Name"]["title"][0]["text"]["content"]
mirror_all = json.dumps([b for m, p, b in calls if m == "PATCH" and ("m1" in p or "/blocks/new" in p)], ensure_ascii=False); print("EN:" in mirror_all, "Ask the fleet to fix it" in mirror_all, "FA:" in mirror_all)
assert "EN:" in mirror_all and "Ask the fleet to fix it" in mirror_all and "FA:" not in mirror_all, "mirror = English originals + translated Persian blocks"
# second run: link exists and nothing English left -> skipped
calls.clear()
BLOCKS.insert(0, {"id": "L", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": "🇬🇧 English", "link": {"url": "https://www.notion.so/" + "a" * 32}}, "plain_text": "🇬🇧 English"}]}, "has_children": False})
for b in BLOCKS[1:]:
    if b["type"] in ("paragraph",) and b["id"] == "b2": b["paragraph"]["rich_text"] = [rt("متن فارسی /plan")]
BLOCKS[4]["_kids_src"][0]["bulleted_list_item"]["rich_text"] = [rt("از ناوگان بخواه")]
BLOCKS[4]["toggle"]["rich_text"] = [rt("جزئیات")]
PAGE["properties"]["Name"]["title"] = [rt("روال روزانه")]
C.process(PAGE, "full")
assert not [c for c in calls if c[0] in ("PATCH", "POST", "DELETE")], "idempotent: no writes on second run"
print("ALL TESTS PASSED", json.dumps(C.STATS))


def test_mismatched_tags_removed():
    rt = [{"type": "text", "text": {"content": "Safety first"}, "annotations": {}}]
    out = C.from_tagged("<s0>ایمنی اول</s2> و \\<s4>بیشتر", rt, [])
    txt = "".join(x["text"]["content"] for x in out)
    assert "<s" not in txt and "</s" not in txt and "ایمنی اول" in txt, txt
test_mismatched_tags_removed(); print("TAG TEST OK")
