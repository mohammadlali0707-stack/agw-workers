#!/usr/bin/env python3
"""GitHub backup for Mohammad's emergency line (owner 2026-10-28). Runs only when the server did not pick up a comment.
Reads the comment (+ a little context), asks Antigravity (shell + internet), posts the answer. Prints nothing (public log)."""
import json, os, subprocess, sys, tempfile, urllib.request, urllib.error
REPO = "mohammadlali0707-stack/emergency-line"; MARK = "<!-- hq-line -->"; PAT = os.environ["LINE_PAT"]; CID = os.environ["CID"]
def gh(m, p, b=None):
    r = urllib.request.Request("https://api.github.com" + p, method=m, data=json.dumps(b).encode() if b is not None else None,
                               headers={"Authorization": "Bearer " + PAT, "Accept": "application/vnd.github+json", "Content-Type": "application/json"})
    r.add_header("User-Agent", "hq-line")
    try:
        with urllib.request.urlopen(r, timeout=60) as x: return json.loads(x.read() or b"null")
    except urllib.error.HTTPError as e:
        try: msg = json.loads(e.read()).get("message", "")[:120]
        except Exception: msg = ""
        sys.stderr.write(f"GHFAIL {m} {p.split('?')[0].replace(REPO, 'R')} {e.code} {msg}\n"); raise
# --- owner 2026-10-28: images / videos even when the server is down: the backup itself queues a website-Flash media job ---
import re as _re, time as _time, base64 as _b64, uuid as _uuid
RES = "mohammadlali0707-stack/hq-ai-results"; ACC_REPO = "mohammadlali0707-stack/agw-workers"
_VERB = _re.compile(r"(بساز|بسازی|بکش|درست\s*کن|تولید|طراحی\s*کن|خلق\s*کن|generate|make|create|draw|render)", _re.I)
_IMG = _re.compile(r"(عکس|تصویر|نقاشی|طرح|لوگو|پوستر|والپیپر|image|picture|photo|logo|poster|wallpaper|illustration)", _re.I)
_VID = _re.compile(r"(ویدیو|ویدئو|فیلم|کلیپ|انیمیشن|video|clip|animation)", _re.I)
def media_kind(q):
    t = _re.sub(r"(?i)@(flash-task|flash)\b", "", q or "").strip()
    if not _re.search(r"(?i)@flash\b", q or ""): return None, t
    m = _re.match(r"^\s*(عکس|تصویر|image|ویدیو|ویدئو|video)\s*[:：]", t, _re.I)
    if m: return ("video" if _VID.match(m.group(1)) else "image"), t
    if _VERB.search(t[:300]) and _VID.search(t[:300]): return "video", t
    if _VERB.search(t[:300]) and _IMG.search(t[:300]): return "image", t
    return None, t
def rgh(m, p, b=None, raw=False):
    r = urllib.request.Request("https://api.github.com" + p, method=m, data=json.dumps(b).encode() if b is not None else None,
                               headers={"Authorization": "Bearer " + os.environ["RES_PAT"], "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
                                        "Content-Type": "application/json", "User-Agent": "hq-line"})
    try:
        with urllib.request.urlopen(r, timeout=60) as x: d = x.read(); return d if raw else json.loads(d or b"null")
    except urllib.error.HTTPError as e:
        if e.code == 404 and raw: return None
        sys.stderr.write(f"GHFAIL res {m} {e.code}\n"); raise
def make_media(kind, prompt):
    fa = "ویدیو" if kind == "video" else "تصویر"
    if kind == "image" and not _IMG.search(prompt): prompt = "Generate an image: " + prompt
    if kind == "video" and not _VID.search(prompt): prompt = "Generate a video: " + prompt
    cid = _time.strftime("%Y%m%d-%H%M%S-") + _uuid.uuid4().hex[:8]
    job = {"prompt": prompt, "kind": kind, "engine": "media", "acc": 6, "caller": "gh-line-fallback", "ts": int(_time.time())}
    rgh("PUT", f"/repos/{RES}/contents/jobs/{cid}/job.json", {"message": f"job {cid} (in)", "content": _b64.b64encode(json.dumps(job, ensure_ascii=False).encode()).decode()})
    rgh("POST", f"/repos/{ACC_REPO}/actions/workflows/hq-gemini.yml/dispatches", {"ref": "main", "inputs": {"callback_id": cid, "engine": "media", "acc": "6"}})
    end = _time.time() + (2100 if kind == "video" else 1200); res = None
    while _time.time() < end:
        _time.sleep(20)
        d = rgh("GET", f"/repos/{RES}/contents/jobs/{cid}/result.json", raw=True)
        if d: res = json.loads(d); break
    if not res: return f"⚠️ ساخت {fa} در زمان مجاز تمام نشد. کمی بعد دوباره بفرست."
    files = res.get("files") or []
    if not res.get("ok") or not files: return f"⚠️ {fa} ساخته نشد (جمینای فایلی برنگرداند). متن را ساده‌تر بنویس و دوباره بفرست."
    out = []
    for f in files[:4]:
        u = f"https://github.com/{RES}/blob/main/jobs/{cid}/files/{f}?raw=true"
        out.append(f"[🎞 دیدن / دانلود ویدیو]({u})" if f.endswith(".mp4") else f"![{fa}]({u})\n[🔗 باز کردن / دانلود]({u})")
    return f"✅ {fa} آماده شد:\n\n" + "\n\n".join(out)

c = gh("GET", f"/repos/{REPO}/issues/comments/{CID}")
if any(x.get("content") in ("eyes", "rocket") for x in gh("GET", f"/repos/{REPO}/issues/comments/{CID}/reactions")): raise SystemExit(0)
gh("POST", f"/repos/{REPO}/issues/comments/{CID}/reactions", {"content": "rocket"})
num = c["issue_url"].rsplit("/", 1)[1]
ctx = [x for x in gh("GET", f"/repos/{REPO}/issues/{num}/comments?per_page=100") if x["id"] < int(CID)][-6:]
hist = "\n\n".join(("Argon: " if MARK in (x.get("body") or "") else "Mohammad: ") + (x.get("body") or "").replace(MARK, "")[:1500] for x in ctx)
prompt = ("You are the GitHub BACKUP of Argon, the operator of Mohammad's company LeadDesk AI. Mohammad writes from his emergency line (a GitHub issue); "
          "the company server is not reachable right now, so you answer directly from a GitHub runner. You have a shell and full internet: when he asks "
          "about news, a web page, prices, how-tos or anything current, search/fetch it yourself (curl, python) and summarise the real content. "
          "Answer in simple Persian, complete and self-contained (plain text and links; images may not load for him), max ~25 lines. Never invent facts; "
          "say clearly if you could not verify something. Do not touch any repository, account or secret. Output only the answer text.\n\n"
          "Now: " + __import__("datetime").datetime.now(__import__("zoneinfo").ZoneInfo("Asia/Tehran")).strftime("%Y-%m-%d %H:%M Tehran time") + "\n\nRecent conversation:\n" + hist + "\n\nMohammad's new message:\n" + (c.get("body") or ""))
kind, mq = media_kind(c.get("body") or "")
if kind and os.environ.get("RES_PAT"):
    gh("POST", f"/repos/{REPO}/issues/{num}/comments", {"body": "⏳ **⚡ Flash (پشتیبان گیت‌هاب)** در حال ساخت " + ("ویدیو" if kind == "video" else "تصویر") + "… (سرور در دسترس نیست)\n\n" + MARK})
    try: a = make_media(kind, mq)
    except Exception as e: a = f"⚠️ ساخت فایل انجام نشد ({type(e).__name__})."
    gh("POST", f"/repos/{REPO}/issues/{num}/comments", {"body": "🛟 **⚡ Flash (پشتیبان گیت‌هاب)** — سرور در دسترس نبود؛ مستقیم از گیت‌هاب.\n\n" + a + "\n\n" + MARK})
    raise SystemExit(0)
env = {k: v for k, v in os.environ.items() if k not in ("LINE_PAT", "RES_PAT", "ACTIONS_RUNTIME_TOKEN", "GITHUB_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN")}
try:
    p = subprocess.run(["/tmp/agy", "--model", "gemini-3.8-flash-high", "--dangerously-skip-permissions", "--print-timeout", "900s", "-p", prompt],
                       cwd=tempfile.mkdtemp(), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=960)
    a = p.stdout.decode("utf-8", "replace").replace(MARK, "").replace("🛠 **Argon**", "").strip() or "⚠️ جوابی ساخته نشد."
except Exception as e: a = f"⚠️ پشتیبان گیت‌هاب جواب نداد ({type(e).__name__})."
gh("POST", f"/repos/{REPO}/issues/{num}/comments", {"body": "🛟 **آرگان (پشتیبان گیت‌هاب)** — سرور در دسترس نبود؛ این جواب مستقیم از گیت‌هاب است.\n\n" + a[-60000:] + "\n\n" + MARK})
