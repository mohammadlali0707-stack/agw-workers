"""Capture a redacted generate-frame shape and a short history poll. No secrets."""
import json, os, time
OUT = "/tmp/out"; os.makedirs(OUT, exist_ok=True)

def redact(obj, depth=0):
    if depth > 7:
        return "deep"
    if obj is None or isinstance(obj, (int, float, bool)):
        return obj
    if isinstance(obj, str):
        if len(obj) <= 18 and obj.isascii() and not any(c in obj for c in "/=+"):
            return obj
        return "str:" + str(len(obj))
    if isinstance(obj, list):
        head = [redact(x, depth + 1) for x in obj[:14]]
        return head + (["more:" + str(len(obj))] if len(obj) > 14 else [])
    if isinstance(obj, dict):
        return {str(k)[:24]: redact(v, depth + 1) for k, v in list(obj.items())[:12]}
    return type(obj).__name__

async def main():
    import asyncio
    from gemini_webapi import GeminiClient
    from gemini_webapi.client import REQ_OVERRIDES
    from gemini_webapi.utils.parsing import StreamingFrameParser
    from curl_cffi.requests import AsyncSession
    body = bytearray()
    orig_stream = AsyncSession.stream
    orig_request = AsyncSession.request

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
                orig = resp.aiter_content
                def aiter_content(*aa, **kk):
                    async def gen():
                        async for chunk in orig(*aa, **kk):
                            raw = chunk.encode("utf-8", "replace") if isinstance(chunk, str) else chunk
                            if len(body) < 2500:
                                body.extend(raw[:2500 - len(body)])
                            yield chunk
                    return gen()
                resp.aiter_content = aiter_content
                return resp
            async def __aexit__(self_inner, *exc):
                return await cm.__aexit__(*exc)
        return Wrapped()
    AsyncSession.request = request
    AsyncSession.stream = stream
    result = {"ok": False}
    ck = json.loads(os.environ.get("GEMINI_WEB_COOKIES") or "{}")
    if isinstance(ck, list):
        ck = {x.get("name"): x.get("value") for x in ck if isinstance(x, dict)}
    client = GeminiClient(ck["__Secure-1PSID"], ck.get("__Secure-1PSIDTS"), proxy=os.environ.get("GWEB_PROXY") or None, timeout=12, watchdog_timeout=6)
    for k, v in ck.items():
        if k not in ("__Secure-1PSID", "__Secure-1PSIDTS") and isinstance(v, str) and v:
            client._cookies.set(k, v, domain=".google.com", secure=True)
    await asyncio.wait_for(client.init(timeout=20, auto_refresh=False), 25)
    models = [m for m in (client.list_models() or []) if m.is_available and m.model_number == 1]
    REQ_OVERRIDES.set({"inner": {80: 2}, "think": 2})
    chat = client.start_chat(model=models[0].model_name)
    async def generate():
        async for chunk in chat.send_message_stream("Reply exactly OK.", extended_thinking=True):
            if getattr(chunk, "text_delta", None):
                return chunk.text_delta
        return ""
    try:
        text = await asyncio.wait_for(generate(), 10)
    except Exception as e:
        text = ""
        result["stream_error"] = type(e).__name__
    parser = StreamingFrameParser()
    frames = parser.feed(body.decode("utf-8", "replace"))
    result["body_bytes"] = len(body)
    result["frames"] = redact(frames) 
    result["stream_exact_ok"] = text.strip() == "OK"
    polls = []
    cid = getattr(chat, "cid", "")
    if cid:
        for i in range(6):
            await asyncio.sleep(3)
            try:
                hist = await asyncio.wait_for(client.read_chat(cid), 8)
            except Exception as e:
                polls.append({"i": i, "error": type(e).__name__})
                continue
            turns = list(getattr(hist, "turns", []) or [])
            model = next((t for t in turns if getattr(t, "role", "") == "model"), None)
            raw = ""
            if model is not None:
                raw = getattr(model, "text", "") or ""
            polls.append({"i": i, "turns": len(turns), "model": bool(model), "exact_ok": raw.strip() == "OK", "error_phrase": "I encountered an error" in raw, "text_len": len(raw)})
            if raw.strip() == "OK":
                result["ok"] = True
                open(OUT + "/answer.txt", "w").write("OK")
                break
    result["polls"] = polls
    result["files"] = []
    json.dump(result, open(OUT + "/result.json", "w"))
    try:
        await asyncio.wait_for(client.close(), 3)
    except Exception:
        pass

import asyncio
asyncio.run(main())
