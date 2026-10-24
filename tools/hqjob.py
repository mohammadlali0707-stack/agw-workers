"""HQ Gemini-website job on a GitHub runner (engine web | vision | media). Job: /tmp/job.json. Output: /tmp/out/."""
import asyncio, json, os, sys, urllib.request, uuid
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
    eng, prompt = job["engine"], job["prompt"]
    wm = job.get("model") or "gemini-flash"; level = int(job.get("level") or 2)
    if eng == "media": wm = "gemini-flash"
    if wm not in alias: return result(False, error=f"model {wm} not available on this account")
    try:
        files = [fetch_ref(r, i) for i, r in enumerate(job.get("refs") or [])] or None
        if eng == "web":
            REQ_OVERRIDES.set({"inner": {80: level}, "think": level}); out = []
            async for ch in c.generate_content_stream(prompt, model=alias[wm], extended_thinking=(level != 1)):
                if ch.text_delta: out.append(ch.text_delta)
            text = "".join(out).strip()
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
                r = await asyncio.wait_for(it.save(path=OUT + "/files", filename=stem + (".mp4" if kind == "video" else ".png")), 900)
                if isinstance(r, dict): r = next((v for v in r.values() if str(v).endswith(".mp4")), None) or next(iter(r.values()), None)
                if isinstance(r, (list, tuple)): r = r[0] if r else None
                if r and os.path.exists(str(r)): names.append(os.path.basename(str(r)))
            if not names: return result(False, error="media files could not be saved")
        open(OUT + "/answer.txt", "w").write(text)
        return result(True, files=names)
    except Exception as e:
        return result(False, error=f"{type(e).__name__}: {str(e)[:200]}")
    finally:
        try: await c.close()
        except Exception: pass
sys.exit(asyncio.run(main()))
