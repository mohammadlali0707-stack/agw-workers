"""HQ Gemini-website job on a GitHub runner (engine web | vision | media). Job: /tmp/job.json. Output: /tmp/out/."""
import asyncio, json, os, sys, time, urllib.request, uuid
from gemini_webapi import GeminiClient
from gemini_webapi.client import REQ_OVERRIDES
from curl_cffi.requests import AsyncSession as _AS
_orig_request = _AS.request
async def _request(self, method, url, *a, **kw):
    if os.environ.get("GWEB_PROXY"): kw["allow_redirects"] = True   # curl_cffi SAFE redirect mode rejects the local proxy IP
    return await _orig_request(self, method, url, *a, **kw)
_AS.request = _request
CANON = {6: "gemini-flash-lite", 1: "gemini-flash", 3: "gemini-pro", 8: "gemini-argon"}
OUT = "/tmp/out"; os.makedirs(OUT + "/files", exist_ok=True)
job = json.load(open("/tmp/job.json"))
def result(ok, **kw):
    json.dump(dict(ok=ok, **kw), open(OUT + "/result.json", "w")); return 0 if ok else 3
def _jar(c):
    sess = getattr(c, "_live_client", None) or c.client
    out = {}
    for ck in (sess.cookies.jar if sess else []):
        d = (ck.domain or "").lstrip(".").lower()
        if (d == "google.com" or d.endswith(".google.com")) and ck.value:
            if ck.name not in out or d == "google.com": out[ck.name] = ck.value
    return out
async def writeback(c, orig):
    """Rotate __Secure-1PSIDTS and store the refreshed session back into this account's secret."""
    try:
        from gemini_webapi.utils.rotate_1psidts import rotate_1psidts
        await rotate_1psidts(getattr(c, "_live_client", None) or c.client)
    except Exception as e:
        print("rotate failed", type(e).__name__, file=sys.stderr)
    new = dict(orig); new.update(_jar(c))
    if not new.get("__Secure-1PSID") or new == orig: return "unchanged"
    try:
        from nacl import encoding, public
        import base64 as b64
        repo, tok, idx = os.environ["PUB_REPO"], os.environ.get("COOKIE_PAT") or os.environ["RES_PAT"], os.environ["ACC_IDX"]
        H = {"Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json"}
        k = json.loads(urllib.request.urlopen(urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/secrets/public-key", headers=H), timeout=60).read())
        enc = b64.b64encode(public.SealedBox(public.PublicKey(k["key"].encode(), encoding.Base64Encoder())).encrypt(json.dumps(new).encode())).decode()
        urllib.request.urlopen(urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/secrets/GEMINI_WEB_COOKIES_ACC{idx}",
            data=json.dumps({"encrypted_value": enc, "key_id": k["key_id"]}).encode(), method="PUT", headers=H), timeout=60)
        return "saved"
    except Exception as e:
        return f"save failed {type(e).__name__}"
def fetch_ref(r, i):
    p = f"/tmp/ref{i}" + os.path.splitext(r.split("?")[0])[1][:6]
    if r.startswith("repo:"):
        req = urllib.request.Request(f"https://api.github.com/repos/{os.environ['RES_REPO']}/contents/{r[5:]}",
                                     headers={"Authorization": "Bearer " + os.environ["RES_PAT"], "Accept": "application/vnd.github.raw"})
    else:
        req = urllib.request.Request(r, headers={"User-Agent": "Mozilla/5.0"})
    open(p, "wb").write(urllib.request.urlopen(req, timeout=120).read()); return p
async def main():
    raw = (os.environ.get("GEMINI_WEB_COOKIES") or "").strip()
    if not raw: return result(False, error="cookie secret missing for this account")
    try: ck = json.loads(raw)
    except Exception: return result(False, error="cookie secret is not valid JSON")
    if isinstance(ck, list): ck = {c.get("name"): c.get("value") for c in ck if isinstance(c, dict)}
    if not ck.get("__Secure-1PSID"): return result(False, error="cookie __Secure-1PSID missing")
    c = GeminiClient(ck["__Secure-1PSID"], ck.get("__Secure-1PSIDTS"), proxy=os.environ.get("GWEB_PROXY") or None)
    for k, v in ck.items():
        if k not in ("__Secure-1PSID", "__Secure-1PSIDTS") and isinstance(v, str) and v:
            try: c._cookies.set(k, v, domain=".google.com", secure=True)
            except Exception: pass
    try: await c.init(timeout=1500, auto_refresh=False)
    except Exception as e: return result(False, error=f"init {type(e).__name__}: {str(e)[:200]}")
    alias = {CANON.get(m.model_number, m.model_name): m.model_name for m in (c.list_models() or []) if m.is_available}
    if len(alias) <= 1: return result(False, error=f"signed_out (status {getattr(c, 'account_status', '?')})")
    try: return await run_job(c, alias)
    finally:
        wb = await writeback(c, ck)
        try:
            r = json.load(open(OUT + "/result.json")); r["session"] = wb; json.dump(r, open(OUT + "/result.json", "w"))
        except Exception: pass
        try: await c.close()
        except Exception: pass

async def poll_task(c, chat_id, stall=1800):
    """Task mode runs asynchronously on Google's side: poll the conversation until the answer is stable."""
    prev, stable, sig, last_chg, errs = None, 0, None, time.time(), 0
    while True:
        await asyncio.sleep(10)
        try: h = await asyncio.wait_for(c.read_chat(chat_id), 60); errs = 0
        except Exception:
            errs += 1
            if errs >= 12: raise RuntimeError("task status could not be read 12 times in a row")
            continue
        mts = [x for x in (h.turns if h else []) if x.role == "model"]
        cur = mts[0].text if mts else ""
        try: th = str(getattr(mts[0].model_output, "thoughts", "")) if mts else ""
        except Exception: th = ""
        nsig = (cur, th, len(h.turns) if h else 0)
        if nsig != sig: sig, last_chg = nsig, time.time()
        elif time.time() - last_chg > stall: raise RuntimeError(f"task made no progress for {stall // 60} minutes")
        stable = stable + 1 if (cur and cur == prev) else 0
        prev = cur
        if cur and stable >= 2: return cur.strip()

async def run_job(c, alias):
    eng, prompt = job["engine"], job["prompt"]
    if eng == "keepalive": open(OUT + "/answer.txt", "w").write(""); return result(True, files=[])
    wm = job.get("model") or "gemini-flash"; level = int(job.get("level") or 2)
    if eng == "media": wm = "gemini-flash"
    if wm not in alias: return result(False, error=f"model {wm} not available on this account")
    try:
        files = [fetch_ref(r, i) for i, r in enumerate(job.get("refs") or [])] or None
        if eng in ("web", "task"):
            ov = {"inner": {80: level}, "think": level}
            if eng == "task": ov["inner"][49] = 40   # Gemini "Task" (agent) mode, US exit via Xray
            REQ_OVERRIDES.set(ov); out = []; meta = None
            async for ch in c.generate_content_stream(prompt, model=alias[wm], extended_thinking=(level != 1)):
                meta = ch.metadata or meta
                if ch.text_delta: out.append(ch.text_delta)
            text = "".join(out).strip()
            if eng == "task" and not text and meta:
                text = await poll_task(c, meta[0])
            if not text: return result(False, error="empty answer")
            open(OUT + "/answer.txt", "w").write(text); return result(True, files=[])
        REQ_OVERRIDES.set(None)
        o = await asyncio.wait_for(c.generate_content(prompt, files=files, model=alias[wm]), 900 if eng == "media" else 300)
        cand = o.candidates[0]; text = cand.text or ""
        names = []
        if eng == "media":
            kind = job.get("kind") or "image"
            items = list(cand.generated_videos or []) if kind == "video" else list(cand.generated_images or cand.web_images or [])
            if not items: return result(False, error="no media: " + text[:200])
            for it in items[:4]:
                stem = uuid.uuid4().hex
                fn = stem + (".mp4" if kind == "video" else ".png"); r = None
                for att in range(40 if kind == "video" else 3):   # video URL answers 403/206 until Veo has finished rendering
                    try:
                        kw = {"client": getattr(c, "_live_client", None) or c.client} if att % 2 else {}
                        r = await asyncio.wait_for(it.save(path=OUT + "/files", filename=fn, **kw), 900); break
                    except Exception as e:
                        if att == (39 if kind == "video" else 2) or not any(x in str(e) for x in ("403", "404", "429", "500", "502", "503")): raise
                        print("media save retry", att, str(e)[:80], file=sys.stderr); await asyncio.sleep(20)
                if isinstance(r, dict): r = next((v for v in r.values() if str(v).endswith(".mp4")), None) or next(iter(r.values()), None)
                if isinstance(r, (list, tuple)): r = r[0] if r else None
                if r and os.path.exists(str(r)): names.append(os.path.basename(str(r)))
            if not names: return result(False, error="media files could not be saved")
        open(OUT + "/answer.txt", "w").write(text)
        return result(True, files=names)
    except Exception as e:
        return result(False, error=f"{type(e).__name__}: {str(e)[:200]}")
sys.exit(asyncio.run(main()))
