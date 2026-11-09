
"""Poll history while the generate stream stays open. No secrets."""
import json, os, time
OUT = "/tmp/out"; os.makedirs(OUT, exist_ok=True)
PHRASE = "I encountered an error"

async def main():
    import asyncio
    from gemini_webapi import GeminiClient
    from gemini_webapi.client import REQ_OVERRIDES
    from curl_cffi.requests import AsyncSession
    orig = AsyncSession.request
    async def request(self, method, url, *a, **kw):
        if os.environ.get("GWEB_PROXY"):
            kw["allow_redirects"] = True
        return await orig(self, method, url, *a, **kw)
    AsyncSession.request = request
    ck = json.loads(os.environ.get("GEMINI_WEB_COOKIES") or "{}")
    if isinstance(ck, list):
        ck = {x.get("name"): x.get("value") for x in ck if isinstance(x, dict)}
    client = GeminiClient(ck["__Secure-1PSID"], ck.get("__Secure-1PSIDTS"), proxy=os.environ.get("GWEB_PROXY") or None, timeout=70, watchdog_timeout=60)
    for k, v in ck.items():
        if k not in ("__Secure-1PSID", "__Secure-1PSIDTS") and isinstance(v, str) and v:
            client._cookies.set(k, v, domain=".google.com", secure=True)
    await asyncio.wait_for(client.init(timeout=20, auto_refresh=False), 25)
    models = [m for m in (client.list_models() or []) if m.is_available and m.model_number == 1]
    REQ_OVERRIDES.set({"inner": {80: 2}, "think": 2})
    chat = client.start_chat(model=models[0].model_name)
    polls = []
    found = {"ok": False}

    async def generate():
        async for ch in chat.send_message_stream("Reply exactly OK.", extended_thinking=True):
            text = getattr(ch, "text", "") or ""
            if text.strip() == "OK":
                found["ok"] = True
                return
        return

    async def poll():
        await asyncio.sleep(4)
        for i in range(8):
            cid = getattr(chat, "cid", "")
            item = {"i": i, "has_cid": bool(cid)}
            if cid:
                try:
                    hist = await asyncio.wait_for(client.read_chat(cid), 8)
                except Exception as e:
                    item["error"] = type(e).__name__
                    polls.append(item)
                    await asyncio.sleep(4)
                    continue
                item["hist_none"] = hist is None
                turns = list(getattr(hist, "turns", []) or [])
                model = next((t for t in turns if getattr(t, "role", "") == "model"), None)
                raw = (getattr(model, "text", "") or "") if model else ""
                item.update(turns=len(turns), model=bool(model), exact_ok=raw.strip()=="OK", error_phrase=PHRASE in raw, text_len=len(raw))
                if raw.strip() == "OK":
                    found["ok"] = True
                    open(OUT + "/answer.txt", "w").write("OK")
                    polls.append(item)
                    return
            polls.append(item)
            await asyncio.sleep(4)

    try:
        await asyncio.wait_for(asyncio.gather(generate(), poll()), 45)
    except Exception as e:
        found["error"] = type(e).__name__
    result = {"ok": found["ok"], "files": [], "error": found.get("error"), "polls": polls}
    json.dump(result, open(OUT + "/result.json", "w"))
    try:
        await asyncio.wait_for(client.close(), 3)
    except Exception:
        pass

import asyncio
asyncio.run(main())
