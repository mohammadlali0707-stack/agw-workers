"""Two hard-capped website comparisons. No cookie writeback and no secret output."""
import json, os, signal, subprocess, sys, time
from urllib.parse import urlparse

OUT = "/tmp/out"
os.makedirs(OUT, exist_ok=True)
CASE = os.environ.get("CASE", "")

def write(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f)

def child():
    import asyncio
    from gemini_webapi import GeminiClient
    from gemini_webapi.client import REQ_OVERRIDES
    from curl_cffi.requests import AsyncSession
    marks = []
    orig_request = AsyncSession.request
    orig_stream = AsyncSession.stream

    async def request(self, method, url, *a, **kw):
        if os.environ.get("GWEB_PROXY"):
            kw["allow_redirects"] = True
        return await orig_request(self, method, url, *a, **kw)

    def stream(self, method, url, *a, **kw):
        if os.environ.get("GWEB_PROXY"):
            kw["allow_redirects"] = True
        cm = orig_stream(self, method, url, *a, **kw)

        class Wrapped:
            async def __aenter__(self_inner):
                resp = await cm.__aenter__()
                try:
                    marks.append(urlparse(str(getattr(resp, "url", url))).netloc + ":" + str(getattr(resp, "status_code", "")))
                except Exception:
                    marks.append("status-unreadable")
                return resp

            async def __aexit__(self_inner, *exc):
                return await cm.__aexit__(*exc)

        return Wrapped()

    AsyncSession.request = request
    AsyncSession.stream = stream
    state = {"stage": "credentials", "ok": False}
    start = time.monotonic()

    def finish(ok, **kw):
        state.update(ok=ok, stage=state["stage"], elapsed_s=round(time.monotonic() - start, 1), statuses=marks[-4:], **kw)
        write(OUT + "/case-" + CASE + ".json", state)
        return state

    def on_term(*_):
        finish(False, error="killed")
        os._exit(124)

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGALRM, on_term)
    signal.alarm(48)

    async def run():
        client = None
        try:
            ck = json.loads(os.environ.get("GEMINI_WEB_COOKIES") or "{}")
            if isinstance(ck, list):
                ck = {x.get("name"): x.get("value") for x in ck if isinstance(x, dict)}
            client = GeminiClient(ck["__Secure-1PSID"], ck.get("__Secure-1PSIDTS"), proxy=os.environ.get("GWEB_PROXY") or None, timeout=25, watchdog_timeout=15)
            for k, v in ck.items():
                if k not in ("__Secure-1PSID", "__Secure-1PSIDTS") and isinstance(v, str) and v:
                    client._cookies.set(k, v, domain=".google.com", secure=True)
            state["stage"] = "init"
            await asyncio.wait_for(client.init(timeout=20, auto_refresh=False, watchdog_timeout=15), 25)
            state["stage"] = "model"
            models = [m for m in (client.list_models() or []) if m.is_available and m.model_number == 1]
            if not models:
                return finish(False, error="flash-unavailable", account_status=str(getattr(client, "account_status", "unknown")))
            state["model"] = models[0].model_name
            state["stage"] = "generate"
            thinking = CASE == "high"
            REQ_OVERRIDES.set({"inner": {80: 2}, "think": 2} if thinking else None)
            chunks = []

            async def generate():
                async for chunk in client.generate_content_stream("Reply exactly OK.", model=models[0].model_name, extended_thinking=thinking):
                    if chunk.text_delta:
                        chunks.append(chunk.text_delta)
                return "".join(chunks).strip()

            answer = await asyncio.wait_for(generate(), 22)
            state["stage"] = "complete"
            exact = answer == "OK"
            finish(exact, answer_len=len(answer), exact_ok=exact, error=None if exact else "unexpected-answer")
            if exact:
                with open(OUT + "/answer-" + CASE + ".txt", "w") as f:
                    f.write(answer)
        except Exception as e:
            finish(False, error=type(e).__name__)
        finally:
            if client:
                try:
                    await asyncio.wait_for(client.close(), 3)
                except Exception:
                    pass

    asyncio.run(run())

def parent():
    env_base = os.environ.copy()
    env_base["PYTHONPATH"] = os.environ.get("PYTHONPATH", "")
    cases = []
    for name in ("plain", "high"):
        env = env_base.copy()
        env["CASE"] = name
        t0 = time.monotonic()
        try:
            proc = subprocess.run([sys.executable, __file__], env=env, timeout=52)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            code = 124
        path = OUT + "/case-" + name + ".json"
        if os.path.exists(path):
            with open(path) as f:
                item = json.load(f)
        else:
            item = {"ok": False, "stage": "no-result", "error": "missing-case-file"}
        item["returncode"] = code
        item["wall_s"] = round(time.monotonic() - t0, 1)
        cases.append({"case": name, **item})
    write(OUT + "/result.json", {"ok": all(c.get("exact_ok") for c in cases), "cases": cases, "files": []})

if CASE:
    child()
else:
    parent()
