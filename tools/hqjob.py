
"""Unwrap and redact the small Gemini frame. No secrets or raw text."""
import json, os
OUT = "/tmp/out"; os.makedirs(OUT, exist_ok=True)

def unwrap(obj, depth=0):
    if depth > 6:
        return obj
    if isinstance(obj, str) and obj[:1] in "[{":
        try:
            return unwrap(json.loads(obj), depth + 1)
        except Exception:
            return obj
    if isinstance(obj, list):
        return [unwrap(x, depth + 1) for x in obj]
    if isinstance(obj, dict):
        return {k: unwrap(v, depth + 1) for k, v in obj.items()}
    return obj

def redact(obj, depth=0):
    if depth > 8:
        return "deep"
    if obj is None or isinstance(obj, (int, float, bool)):
        return obj
    if isinstance(obj, str):
        if len(obj) <= 12 and obj.isascii() and not any(c in obj for c in "/=+"):
            return obj
        kind = "cid" if obj.startswith("c_") else "str"
        return kind + ":" + str(len(obj))
    if isinstance(obj, list):
        head = [redact(x, depth + 1) for x in obj[:16]]
        return head + (["more:" + str(len(obj))] if len(obj) > 16 else [])
    if isinstance(obj, dict):
        return {str(k)[:20]: redact(v, depth + 1) for k, v in list(obj.items())[:12]}
    return type(obj).__name__

async def main():
    import asyncio
    from gemini_webapi import GeminiClient
    from gemini_webapi.client import REQ_OVERRIDES
    from gemini_webapi.utils.parsing import StreamingFrameParser
    from curl_cffi.requests import AsyncSession
    body = bytearray()
    orig_stream, orig_request = AsyncSession.stream, AsyncSession.request
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
    AsyncSession.request, AsyncSession.stream = request, stream
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
        async for _chunk in chat.send_message_stream("Reply exactly OK.", extended_thinking=True):
            pass
    err = None
    try:
        await asyncio.wait_for(generate(), 8)
    except Exception as e:
        err = type(e).__name__
    frames = StreamingFrameParser().feed(body.decode("utf-8", "replace"))
    parsed = unwrap(frames)
    result = {"ok": False, "files": [], "stream_error": err, "body_bytes": len(body), "frames": redact(parsed), "has_cid": bool(getattr(chat, "cid", ""))}
    json.dump(result, open(OUT + "/result.json", "w"))
    try:
        await asyncio.wait_for(client.close(), 3)
    except Exception:
        pass
import asyncio
asyncio.run(main())
