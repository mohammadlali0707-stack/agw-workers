"""SDD-004 P0 probe: real Chrome + pool cookies + xray proxy -> is Gemini Live / Gemini in Chrome available?"""
import json, os, platform, subprocess, sys, time, traceback, urllib.request
OUT = "/tmp/out"; os.makedirs(OUT, exist_ok=True)
R = {"os": platform.platform(), "steps": []}
def log(k, v): R["steps"].append({k: v}); json.dump(R, open(OUT + "/probe.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
WIN = sys.platform.startswith("win")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe" if WIN else "google-chrome"
PROF = os.path.abspath("/tmp/chprof")
def shot_screen(name):
    try:
        if WIN:
            from PIL import ImageGrab; ImageGrab.grab().save(f"{OUT}/{name}.png")
        else:
            subprocess.run(["import", "-window", "root", f"{OUT}/{name}.png"], timeout=30)
    except Exception as e: log("screen_err_" + name, str(e)[:200])
def launch(extra):
    args = [CHROME, f"--user-data-dir={PROF}", "--remote-debugging-port=9222", "--proxy-server=http://127.0.0.1:10809",
            "--lang=en-US", "--no-first-run", "--no-default-browser-check", "--window-size=1580,980", "--window-position=0,0",
            "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"] + extra
    env = dict(os.environ, LANG="en_US.UTF-8", LANGUAGE="en_US")
    p = subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try: urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=2); return p
        except Exception: time.sleep(1)
    return p
def main():
    try:
        px = urllib.request.build_opener(urllib.request.ProxyHandler({"https": "http://127.0.0.1:10809"}))
        log("egress", json.loads(px.open("https://ipinfo.io/json", timeout=20).read()).get("country"))
    except Exception as e: log("egress", "ERR " + str(e)[:150])
    ck = json.loads(os.environ.get("GEMINI_WEB_COOKIES") or "{}")
    log("cookie_names", sorted(ck.keys()))
    from playwright.sync_api import sync_playwright
    FEAT = ["--enable-features=Glic,GlicRollout,TabstripComboButton,GlicActor,GlicLiveMode"]
    for rnd, extra in enumerate([[], FEAT]):
        p = launch(extra); time.sleep(5)
        with sync_playwright() as pw:
            b = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
            ctx = b.contexts[0]
            if rnd == 0:
                ACC = {"LSID", "ACCOUNT_CHOOSER", "SMSV"}
                bad = []
                for k, v in ck.items():
                    if not (isinstance(v, str) and v): continue
                    c = {"name": k, "value": v, "secure": True, "httpOnly": True, "expires": int(time.time()) + 30 * 86400}
                    if k.startswith("__Host-") or k in ACC: c["url"] = "https://accounts.google.com/"
                    else: c.update(domain=".google.com", path="/")
                    if "3P" in k: c["sameSite"] = "None"
                    try: ctx.add_cookies([c])
                    except Exception: bad.append(k)
                log("cookies_rejected", bad)
            pg = ctx.pages[0] if ctx.pages else ctx.new_page()
            def visit(url, name, wait=6):
                try:
                    pg.goto(url, timeout=60000); time.sleep(wait)
                    pg.screenshot(path=f"{OUT}/r{rnd}_{name}.png")
                    txt = pg.evaluate("() => document.body ? document.body.innerText.slice(0, 3000) : ''")
                    return txt
                except Exception as e: return "ERR " + str(e)[:200]
            if rnd == 0:
                t = visit("chrome://version", "version", 2); log("version", t[:900])
            t = visit("https://gemini.google.com/app?hl=en", "gemini", 10)
            labels = pg.evaluate("""() => [...document.querySelectorAll('button,a,[role=button]')].map(e => (e.getAttribute('aria-label')||e.innerText||'').trim()).filter(x=>x).slice(0,150)""") if not t.startswith("ERR") else []
            log(f"r{rnd}_gemini", {"signed_in_guess": "Sign in" not in t[:600], "has_live_word": any("live" in l.lower() for l in labels),
                                    "live_labels": [l for l in labels if "live" in l.lower() or "mic" in l.lower() or "voice" in l.lower()][:20],
                                    "head": t[:400]})
            shot_screen(f"r{rnd}_screen_gemini")
            t = visit("https://gemini.google.com/live?hl=en", "gemini_live", 8); log(f"r{rnd}_gemini_live", t[:400])
            for q in ("glic", "gemini"):
                try:
                    pg.goto("chrome://flags", timeout=60000); time.sleep(2)
                    pg.locator("#search").fill(q); time.sleep(2)
                    pg.screenshot(path=f"{OUT}/r{rnd}_flags_{q}.png", full_page=True)
                    log(f"r{rnd}_flags_{q}", pg.evaluate("() => [...document.querySelectorAll('.experiment:not(.hidden) .experiment-name, .experiment-name')].map(e=>e.innerText).slice(0,40)"))
                except Exception as e: log(f"r{rnd}_flags_{q}", "ERR " + str(e)[:200])
            t = visit("chrome://settings/ai", "settings_ai", 3); log(f"r{rnd}_settings_ai", t[:1200])
            t = visit("chrome://settings/gemini", "settings_gemini", 3); log(f"r{rnd}_settings_gemini", t[:1200])
            t = visit("chrome://glic", "glic", 5); log(f"r{rnd}_glic", t[:600])
            t = visit("https://www.google.com/?hl=en", "google", 4); shot_screen(f"r{rnd}_screen_google")
            t = visit("https://accounts.google.com/ServiceLogin?continue=https://www.google.com/&hl=en", "accounts", 8); log(f"r{rnd}_accounts", t[:300]); shot_screen(f"r{rnd}_screen_accounts")
            if rnd == 0: time.sleep(45)   # let variations seed download before relaunch
            try: b.close()
            except Exception: pass
        p.terminate(); time.sleep(4)
        try: p.kill()
        except Exception: pass
try: main(); R["ok"] = True
except Exception: R["ok"] = False; R["error"] = traceback.format_exc()[-1500:]
json.dump(R, open(OUT + "/probe.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
json.dump({"ok": R["ok"], "files": []}, open(OUT + "/result.json", "w"))
