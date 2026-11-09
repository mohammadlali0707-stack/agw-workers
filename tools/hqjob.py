"""Prove whether a same-chat resend recovers Gemini's transient error card. No secrets."""
import json, os, signal, subprocess, sys, time

OUT = "/tmp/out"
os.makedirs(OUT, exist_ok=True)
CASE = os.environ.get("CASE", "")
ERROR_PHRASE = b"I encountered an error"

def write(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f)

def child():
    import asyncio
    from gemini_webapi import GeminiClient
    from gemini_webapi.client import REQ_OVERRIDES
    from curl_cffi.requests import AsyncSession
    flags = {"bytes": 0, "card": False, "error_phrase": False, "statuses": []}
    tail = b""
    orig_request, orig_stream = AsyncSession.request, AsyncSession.stream

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
                flags["statuses"].append(str(getattr(resp, "status_code", "")))
                orig = resp.aiter_content

                def aiter_content(*aa, **kk):
                    async def gen():
                        nonlocal tail
                        async for chunk in orig(*aa, **kk):
                            if isinstance(chunk, str):
                                chunk = chunk.encode("utf-8", "replace")
                            flags["bytes"] += len(chunk)
                            tail = (tail + chunk)[-160:]
                            if b"card_content" in tail:
                                flags["card"] = True
                            if ERROR_PHRASE in tail:
                                flags["error_phrase"] = True
                            yield chunk
                    return gen()

                resp.aiter_content = aiter_content
                return resp

            async def __aexit__(self_inner, *exc):
                return await cm.__aexit__(*exc)

        return Wrapped()

    AsyncSession.request = request
    AsyncSession.stream = stream
    state = {"ok": False, "attempts": []}
    signal.signal(signal.SIGTERM, lambda *_: os._exit(124))
    signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
    signal.alarm(75)

    def snap(name, answer, started, chat):
        state["attempts"].append({
            "name": name,
            "elapsed_s": round(time.monotonic() - started, 1),
            "exact_ok": answer == "OK",
            "answer_len": len(answer),
            "error_phrase": flags["error_phrase"],
            "card": flags["card"],
            "bytes": flags["bytes"],
            "statuses": flags["statuses"][-3:],
            "has_cid": bool(getattr(chat, "cid", "")),
        })
        write(OUT + "/case-retry.json", state)

    async def once(chat, name):
        flags.update(bytes=0, card=False, error_phrase=False)
        flags["statuses"] = []
        started = time.monotonic()
        chunks = []

        async def generate():
            async for chunk in chat.send_message_stream("Reply exactly OK.", extended_thinking=True):
                if getattr(chunk, "text_delta", None):
                    chunks.append(chunk.text_delta)
            return "".join(chunks).strip()

        try:
            answer = await asyncio.wait_for(generate(), 16)
        except Exception as e:
            answer = ""
            state["last_error"] = type(e).__name__
        snap(name, answer, started, chat)
        return answer

    async def run():
        ck = json.loads(os.environ.get("GEMINI_WEB_COOKIES") or "{}")
        if isinstance(ck, list):
            ck = {x.get("name"): x.get("value") for x in ck if isinstance(x, dict)}
        client = GeminiClient(ck["__Secure-1PSID"], ck.get("__Secure-1PSIDTS"), proxy=os.environ.get("GWEB_PROXY") or None, timeout=12, watchdog_timeout=8)
        for k, v in ck.items():
            if k not in ("__Secure-1PSID", "__Secure-1PSIDTS") and isinstance(v, str) and v:
                client._cookies.set(k, v, domain=".google.com", secure=True)
        await asyncio.wait_for(client.init(timeout=20, auto_refresh=False, watchdog_timeout=8), 25)
        models = [m for m in (client.list_models() or []) if m.is_available and m.model_number == 1]
        if not models:
            state["error"] = "flash-unavailable"
            write(OUT + "/case-retry.json", state)
            return
        REQ_OVERRIDES.set({"inner": {80: 2}, "think": 2})
        chat = client.start_chat(model=models[0].model_name)
        answer = await once(chat, "first")
        if answer != "OK":
            answer = await once(chat, "same-chat")
        if answer != "OK":
            answer = await once(client.start_chat(model=models[0].model_name), "fresh")
        state["ok"] = answer == "OK"
        state["exact_ok"] = answer == "OK"
        write(OUT + "/case-retry.json", state)
        if answer == "OK":
            with open(OUT + "/answer.txt", "w") as f:
                f.write(answer)
        try:
            await asyncio.wait_for(client.close(), 3)
        except Exception:
            pass

    asyncio.run(run())

def parent():
    env = os.environ.copy()
    env["CASE"] = "retry"
    t0 = time.monotonic()
    try:
        proc = subprocess.run([sys.executable, __file__], env=env, timeout=80)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        code = 124
    path = OUT + "/case-retry.json"
    item = json.load(open(path)) if os.path.exists(path) else {"ok": False, "error": "missing-case-file"}
    item["returncode"] = code
    item["wall_s"] = round(time.monotonic() - t0, 1)
    write(OUT + "/result.json", {"ok": bool(item.get("exact_ok")), "files": [], **item})

if CASE:
    child()
else:
    parent()
