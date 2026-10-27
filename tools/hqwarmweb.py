"""HQ warm Gemini-website worker (owner 2026-10-27): keeps ONE signed-in Gemini website session (this account's own cookies) open for the
whole warm run, so Argon / Pro answers start in seconds instead of booting a new runner. Fed by hqwarm.py through local folders:
/tmp/wjobs/<cid>.json -> /tmp/wdone/<cid>/{answer.txt,result.json}. /tmp/web_ready exists while the session is usable.
Nothing sensitive is printed (public repo)."""
import asyncio, json, os, sys, time, urllib.request
from gemini_webapi import GeminiClient
from gemini_webapi.client import REQ_OVERRIDES
from curl_cffi.requests import AsyncSession as _AS
_orig_request = _AS.request
async def _request(self, method, url, *a, **kw):
    if os.environ.get("GWEB_PROXY"): kw["allow_redirects"] = True
    return await _orig_request(self, method, url, *a, **kw)
_AS.request = _request
CANON = {6: "gemini-flash-lite", 1: "gemini-flash", 3: "gemini-pro", 8: "gemini-argon"}
JOBS, DONE, READY = "/tmp/wjobs", "/tmp/wdone", "/tmp/web_ready"
os.makedirs(JOBS, exist_ok=True); os.makedirs(DONE, exist_ok=True)
STOP = "/tmp/web_stop"; WB_EVERY = 25 * 60
def ready(on):
    if on: open(READY, "w").write(str(int(time.time())))
    elif os.path.exists(READY): os.remove(READY)
def _jar(c):
    sess = getattr(c, "_live_client", None) or c.client; out = {}
    for ck in (sess.cookies.jar if sess else []):
        d = (ck.domain or "").lstrip(".").lower()
        if (d == "google.com" or d.endswith(".google.com")) and ck.value:
            if ck.name not in out or d == "google.com": out[ck.name] = ck.value
    return out
async def writeback(c, orig):
    try:
        from gemini_webapi.utils.rotate_1psidts import rotate_1psidts
        await rotate_1psidts(getattr(c, "_live_client", None) or c.client)
    except Exception as e: print("rotate failed", type(e).__name__, flush=True)
    new = dict(orig); new.update(_jar(c))
    if not new.get("__Secure-1PSID") or new == orig: return orig, "unchanged"
    try:
        from nacl import encoding, public
        import base64 as b64
        repo, tok, idx = os.environ["PUB_REPO"], os.environ.get("COOKIE_PAT") or os.environ["RES_PAT"], os.environ["ACC"]
        H = {"Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json"}
        k = json.loads(urllib.request.urlopen(urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/secrets/public-key", headers=H), timeout=60).read())
        enc = b64.b64encode(public.SealedBox(public.PublicKey(k["key"].encode(), encoding.Base64Encoder())).encrypt(json.dumps(new).encode())).decode()
        urllib.request.urlopen(urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/secrets/GEMINI_WEB_COOKIES_ACC{idx}",
            data=json.dumps({"encrypted_value": enc, "key_id": k["key_id"]}).encode(), method="PUT", headers=H), timeout=60)
        return new, "saved"
    except Exception as e: return orig, f"save failed {type(e).__name__}"
async def connect(ck):
    c = GeminiClient(ck["__Secure-1PSID"], ck.get("__Secure-1PSIDTS"), proxy=os.environ.get("GWEB_PROXY") or None)
    for k, v in ck.items():
        if k not in ("__Secure-1PSID", "__Secure-1PSIDTS") and isinstance(v, str) and v:
            try: c._cookies.set(k, v, domain=".google.com", secure=True)
            except Exception: pass
    await c.init(timeout=1500, auto_refresh=False)
    alias = {CANON.get(m.model_number, m.model_name): m.model_name for m in (c.list_models() or []) if m.is_available}
    if len(alias) <= 1: raise RuntimeError("signed_out")
    return c, alias
def finish(cid, ok, text="", t0=0, err=""):
    d = f"{DONE}/{cid}.tmp"; os.makedirs(d, exist_ok=True)
    open(d + "/answer.txt", "w").write(text or "")
    json.dump({"ok": ok, "files": [], "warm": True, "secs": round(time.time() - t0, 1), **({} if ok else {"error": err[:300]})}, open(d + "/result.json", "w"))
    os.replace(d, f"{DONE}/{cid}")
async def one(c, alias, cid, job):
    t0 = time.time()
    try:
        wm = job.get("model") or "gemini-flash"; level = int(job.get("level") or 2)
        if wm not in alias: return finish(cid, False, t0=t0, err=f"model {wm} not available on this account")
        REQ_OVERRIDES.set({"inner": {80: level}, "think": level}); out = []
        async def gen():
            async for ch in c.generate_content_stream(job.get("prompt", ""), model=alias[wm], extended_thinking=(level != 1)):
                if ch.text_delta: out.append(ch.text_delta)
        await asyncio.wait_for(gen(), 2000)
        text = "".join(out).strip()
        finish(cid, bool(text), text, t0, "" if text else "empty answer")
        return bool(text)
    except Exception as e:
        finish(cid, False, t0=t0, err=f"{type(e).__name__}: {str(e)[:200]}"); return False
async def main():
    raw = (os.environ.get("GEMINI_WEB_COOKIES") or "").strip()
    try:
        ck = json.loads(raw)
        if isinstance(ck, list): ck = {x.get("name"): x.get("value") for x in ck if isinstance(x, dict)}
        assert ck.get("__Secure-1PSID")
    except Exception: print("web: no usable session secret for this account -> Flash only", flush=True); return
    try: c, alias = await connect(ck)
    except Exception as e: print("web: sign-in failed", type(e).__name__, "-> Flash only", flush=True); return
    ready(True); print("web session ready", sorted(alias), flush=True)
    last_wb, fails, running, stats = time.time(), 0, set(), {"jobs": 0, "fail": 0}
    while not os.path.exists(STOP):
        for f in sorted(os.listdir(JOBS)):
            if not f.endswith(".json"): continue
            p = f"{JOBS}/{f}"
            try: job = json.load(open(p))
            except Exception: continue
            os.remove(p); cid = f[:-5]
            async def run(cid=cid, job=job):
                nonlocal fails
                ok = await one(c, alias, cid, job); stats["jobs"] += 1; stats["fail"] += (not ok)
                fails = 0 if ok else fails + 1
            t = asyncio.create_task(run()); running.add(t); t.add_done_callback(running.discard)
        if fails >= 3 or time.time() - last_wb > WB_EVERY:
            if running: await asyncio.sleep(1); continue
            ready(False)
            if fails >= 3:   # session looks broken -> reconnect once with the freshest cookies
                try: await c.close()
                except Exception: pass
                try: c, alias = await connect(ck); fails = 0; print("web: reconnected", flush=True)
                except Exception as e: print("web: reconnect failed", type(e).__name__, "-> Flash only", flush=True); return
            else:
                ck, st = await writeback(c, ck); last_wb = time.time(); print("web session", st, stats, flush=True)
            ready(True)
        await asyncio.sleep(0.5)
    ready(False)
    while running: await asyncio.sleep(1)
    try: ck, st = await writeback(c, ck); print("web session", st, "at exit", stats, flush=True)
    except Exception: pass
asyncio.run(main())
