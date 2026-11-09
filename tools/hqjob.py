"""Bounded isolated web diagnostic. No session writeback or sensitive output."""
import asyncio, json, os, sys, time
from gemini_webapi import GeminiClient
from gemini_webapi.client import REQ_OVERRIDES
from curl_cffi.requests import AsyncSession
original_request = AsyncSession.request
async def request(self, method, url, *a, **kw):
    if os.environ.get('GWEB_PROXY'): kw['allow_redirects'] = True
    return await original_request(self, method, url, *a, **kw)
AsyncSession.request = request
OUT='/tmp/out'; os.makedirs(OUT, exist_ok=True)
start = time.monotonic(); stage='credentials'; client=None
def result(ok, **kw):
    with open(OUT+'/result.json','w') as f: json.dump({'ok':ok,'stage':stage,'elapsed_s':round(time.monotonic()-start),**kw},f)
async def main():
    global stage, client
    try:
        ck=json.loads(os.environ.get('GEMINI_WEB_COOKIES') or '{}')
        if isinstance(ck,list): ck={x.get('name'):x.get('value') for x in ck if isinstance(x,dict)}
        client=GeminiClient(ck['__Secure-1PSID'],ck.get('__Secure-1PSIDTS'),proxy=os.environ.get('GWEB_PROXY') or None)
        for k,v in ck.items():
            if k not in ('__Secure-1PSID','__Secure-1PSIDTS') and isinstance(v,str) and v: client._cookies.set(k,v,domain='.google.com',secure=True)
        stage='init'
        await asyncio.wait_for(client.init(timeout=70,auto_refresh=False),80)
        stage='model'
        models=[m for m in (client.list_models() or []) if m.is_available and m.model_number==1]
        if not models: return result(False, error='flash model unavailable',account_status=str(getattr(client,'account_status','unknown')))
        stage='generate'
        REQ_OVERRIDES.set({'inner':{80:2},'think':2})
        async def generate():
            chunks=[]
            async for chunk in client.generate_content_stream('Reply exactly OK.',model=models[0].model_name,extended_thinking=True):
                if chunk.text_delta: chunks.append(chunk.text_delta)
            return ''.join(chunks).strip()
        answer=await asyncio.wait_for(generate(),120)
        stage='complete'
        with open(OUT+'/answer.txt','w') as f: f.write(answer)
        result(bool(answer), files=[])
    except Exception as e: result(False,error=type(e).__name__)
    finally:
        if client:
            try: await asyncio.wait_for(client.close(),5)
            except Exception: pass
asyncio.run(main())
