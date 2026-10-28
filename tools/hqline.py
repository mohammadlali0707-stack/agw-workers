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
          "Recent conversation:\n" + hist + "\n\nMohammad's new message:\n" + (c.get("body") or ""))
env = {k: v for k, v in os.environ.items() if k not in ("LINE_PAT", "ACTIONS_RUNTIME_TOKEN", "GITHUB_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN")}
try:
    p = subprocess.run(["/tmp/agy", "--model", "gemini-3.8-flash-high", "--dangerously-skip-permissions", "--print-timeout", "900s", "-p", prompt],
                       cwd=tempfile.mkdtemp(), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=960)
    a = p.stdout.decode("utf-8", "replace").replace(MARK, "").replace("🛠 **Argon**", "").strip() or "⚠️ جوابی ساخته نشد."
except Exception as e: a = f"⚠️ پشتیبان گیت‌هاب جواب نداد ({type(e).__name__})."
gh("POST", f"/repos/{REPO}/issues/{num}/comments", {"body": "🛟 **آرگان (پشتیبان گیت‌هاب)** — سرور در دسترس نبود؛ این جواب مستقیم از گیت‌هاب است.\n\n" + a[-60000:] + "\n\n" + MARK})
