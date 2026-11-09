
"""Compare direct, socks, and http-proxy streaming. Record only mode and success flags."""
import json, os
OUT = "/tmp/out"; os.makedirs(OUT, exist_ok=True)

def socks_proxy():
    try:
        cfg = json.load(open("/tmp/xray/config.json"))
    except Exception:
        return None
    for ib in cfg.get("inbounds") or []:
        proto = str(ib.get("protocol") or "").lower()
        port = ib.get("port")
        if proto.startswith("socks") and isinstance(port, int):
            return "socks5://127.0.0.1:" + str(port)
    return None

async def attempt(proxy, mode):
    import asyncio
    from gemini_webapi import GeminiClient
    from gemini_webapi.client import REQ_OVERRIDES
    from curl_cffi.requests import AsyncSession
    if proxy:
        orig = AsyncSession.request
        async def request(self, method, url, *a, **kw):
            kw["allow_redirects"] = True
            return await orig(self, method, url, *a, **kw)
        AsyncSession.request = request
    ck = json.loads(os.environ.get("GEMINI_WEB_COOKIES") or "{}")
    if isinstance(ck, list):
        ck = {x.get("name"): x.get("value") for x in ck if isinstance(x, dict)}
    client = GeminiClient(ck["__Secure-1PSID"], ck.get("__Secure-1PSIDTS"), proxy=proxy, timeout=12, watchdog_timeout=6)
    for k, v in ck.items():
        if k not in ("__Secure-1PSID", "__Secure-1PSIDTS") and isinstance(v, str) and v:
            client._cookies.set(k, v, domain=".google.com", secure=True)
    item = {"mode": mode, "ok": False}
    try:
        await asyncio.wait_for(client.init(timeout=15, auto_refresh=False), 18)
        models = [m for m in (client.list_models() or []) if m.is_available and m.model_number == 1]
        if not models:
            item["error"] = "flash-unavailable"
            return item
        REQ_OVERRIDES.set({"inner": {80: 2}, "think": 2})
        chunks = []
        async def generate():
            async for ch in client.generate_content_stream("Reply exactly OK.", model=models[0].model_name, extended_thinking=True):
                if getattr(ch, "text_delta", None):
                    chunks.append(ch.text_delta)
            return "".join(chunks).strip()
        answer = await asyncio.wait_for(generate(), 18)
        item["exact_ok"] = answer == "OK"
        item["answer_len"] = len(answer)
        item["ok"] = answer == "OK"
        if answer == "OK":
            open(OUT + "/answer.txt", "w").write(answer)
    except Exception as e:
        item["error"] = type(e).__name__
    finally:
        try:
            await asyncio.wait_for(client.close(), 3)
        except Exception:
            pass
    return item

async def main():
    import asyncio
    modes = [("direct", None)]
    socks = socks_proxy()
    if socks:
        modes.append(("socks", socks))
    modes.append(("http", os.environ.get("GWEB_PROXY") or None))
    cases = []
    for name, proxy in modes:
        if name != "direct" and not proxy:
            cases.append({"mode": name, "ok": False, "error": "unavailable"})
            continue
        item = await attempt(proxy, name)
        cases.append(item)
        if item.get("ok"):
            break
    json.dump({"ok": any(c.get("ok") for c in cases), "files": [], "cases": cases}, open(OUT + "/result.json", "w"))

import asyncio
asyncio.run(main())
