#!/usr/bin/env python3
"""Mint a fresh Gemini website session inside a GitHub runner's own headed
browser, with the owner typing the login himself through a remote view.

Runs ONLY on GitHub Actions (never on the VPS). Flow:
  1. Start Xvfb + x11vnc + noVNC + cloudflared quick tunnel. The public
     noVNC URL is written to /tmp/novnc-url.txt and NEVER printed in the
     log (this repo is PUBLIC, so run logs are public; the URL is a live
     capability to the desktop where the owner types his Google password).
     The workflow publishes the URL file to the PRIVATE hq-ai-results repo
     and Muse hands the URL to the owner in chat.
  2. Launch real Google Chrome (headed) under Xvfb at gemini.google.com,
     routed through GWEB_PROXY (Oracle exit) when set.
  3. dry_run: verify the tunnel URL answers HTTP 200, the browser page
     loaded, save a screenshot, exit success WITHOUT writing any secret.
  4. login: poll up to MINT_POLL_SECS for a signed-in Gemini session
     (__Secure-1PSID cookie present AND the tab is on gemini.google.com).
     When found, export all .google.com cookies as a flat {name: value}
     dict (the exact shape tools/hqjob.py expects) to
     /tmp/gemini-session-<acc>.json (0600, never printed). The workflow
     step then seals it with `gh secret set GEMINI_WEB_COOKIES_ACC<idx>`
     and shreds the file.
If a captcha appears, stop and report it (never try to solve one).
If the poll times out, exit non-zero and do NOT touch the secret.
Cookie values are never printed; they are masked in the log.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import urllib.request

ACCOUNT = os.environ.get("MINT_ACCOUNT", "acc0")
MODE = os.environ.get("MINT_MODE", "dry_run")
POLL_SECS = int(os.environ.get("MINT_POLL_SECS", "2400") or "2400")
PROXY = os.environ.get("GWEB_PROXY", "").strip()
DISPLAY = ":99"
URL_FILE = "/tmp/novnc-url.txt"
CHROME_PROFILE = "/tmp/chrome-profile"
CDP_URL = "http://127.0.0.1:9222"
NOVNC_RE = re.compile(r"https://[A-Za-z0-9-]+\.trycloudflare\.com")

PROCS = []       # subprocess.Popen handles, killed on cleanup
LOG_FILES = []   # open log file handles, closed on cleanup


def log(msg):
    print(msg, flush=True)


def fail(msg, code=1):
    log("ERROR: " + msg)
    raise SystemExit(code)


def mask(value):
    if value and len(str(value)) > 6:
        print("::add-mask::" + str(value), flush=True)


def wait_port(host, port, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def wait_path(path, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if os.path.exists(path):
            return True
        time.sleep(0.5)
    return False


def run_bg(argv, logfile, env_extra=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    f = open(logfile, "wb")
    LOG_FILES.append(f)
    p = subprocess.Popen(argv, stdout=f, stderr=subprocess.STDOUT,
                         env=env, start_new_session=True)
    PROCS.append(p)
    return p


def check_proxy():
    if not PROXY:
        log("no GWEB_PROXY set; browser will go direct")
        return
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
    try:
        with opener.open("https://ifconfig.me", timeout=15) as r:
            ip = r.read().decode().strip()
    except Exception as e:
        fail(f"proxy check failed: {type(e).__name__}: {e}")
    log(f"egress via proxy: {ip}")
    if not ip.startswith("159.54."):
        fail(f"proxy egress is not the Oracle range: {ip}")


def novnc_root():
    if os.path.isfile("/usr/share/novnc/vnc.html"):
        return "/usr/share/novnc"
    log("noVNC not found under /usr/share/novnc; downloading noVNC 1.5.0")
    url = "https://github.com/novnc/noVNC/archive/refs/tags/v1.5.0.tar.gz"
    tgz = "/tmp/novnc.tar.gz"
    try:
        urllib.request.urlretrieve(url, tgz)
        with tarfile.open(tgz, "r:gz") as tf:
            tf.extractall(path="/tmp/novnc_dl", filter="data")
    except Exception as e:
        fail(f"noVNC download failed: {type(e).__name__}: {e}")
    for root, _dirs, files in os.walk("/tmp/novnc_dl"):
        if "vnc.html" in files:
            return root
    fail("noVNC download did not contain vnc.html")


def start_desktop():
    run_bg(["Xvfb", DISPLAY, "-screen", "0", "1280x800x24", "-ac"],
           "/tmp/xvfb.log")
    if not wait_path("/tmp/.X11-unix/X99", timeout=15):
        fail("Xvfb did not come up")
    log("Xvfb up on " + DISPLAY)
    # x11vnc binds localhost only; the tunnel is the only way in.
    run_bg(["x11vnc", "-display", DISPLAY, "-forever", "-shared", "-nopw",
            "-localhost", "-rfbport", "5900"], "/tmp/x11vnc.log")
    if not wait_port("127.0.0.1", 5900, timeout=20):
        fail("x11vnc did not come up")
    log("x11vnc up on 127.0.0.1:5900")
    root = novnc_root()
    ws = shutil.which("websockify")
    cmd = ([ws] if ws else [sys.executable, "-m", "websockify"]) + [
        "--web", root, "6080", "localhost:5900"]
    run_bg(cmd, "/tmp/websockify.log")
    if not wait_port("127.0.0.1", 6080, timeout=20):
        fail("websockify/noVNC did not come up")
    log("noVNC up on 127.0.0.1:6080")
    cf = "/tmp/cloudflared"
    if not (os.path.isfile(cf) and os.access(cf, os.X_OK)):
        fail("cloudflared binary missing at /tmp/cloudflared")
    run_bg([cf, "tunnel", "--url", "http://localhost:6080", "--no-autoupdate"],
           "/tmp/cloudflared.log")
    url = None
    end = time.time() + 90
    while time.time() < end:
        try:
            with open("/tmp/cloudflared.log", "r", errors="replace") as f:
                m = NOVNC_RE.search(f.read())
            if m:
                url = m.group(0)
                break
        except OSError:
            pass
        time.sleep(2)
    if not url:
        fail("cloudflared tunnel did not produce a URL within 90s")
    full_url = url + "/vnc.html"
    with open(URL_FILE, "w") as f:
        f.write(full_url + "\n")
    # The URL itself is NEVER printed: this repo's run logs are public.
    log("remote desktop is up; the noVNC URL was saved for private delivery "
        "(Muse hands it to the owner in chat)")
    return full_url


def start_chrome():
    chrome = shutil.which("google-chrome") or "/usr/bin/google-chrome"
    if not os.path.isfile(chrome):
        fail("google-chrome not found")
    env = dict(os.environ, DISPLAY=DISPLAY)
    cmd = [chrome, "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
           "--remote-debugging-port=9222",
           "--user-data-dir=" + CHROME_PROFILE,
           "--no-first-run", "--disable-features=Translate",
           "--window-size=1280,800"]
    if PROXY:
        cmd.append("--proxy-server=" + PROXY)
    cmd.append("https://gemini.google.com")
    p = subprocess.Popen(cmd, env=env,
                         stdout=open("/tmp/chrome.log", "wb"),
                         stderr=subprocess.STDOUT, start_new_session=True)
    PROCS.append(p)
    end = time.time() + 30
    while time.time() < end:
        try:
            with urllib.request.urlopen(CDP_URL + "/json/version",
                                        timeout=3) as r:
                if r.status == 200:
                    log("Chrome up, CDP reachable")
                    return p
        except Exception:
            time.sleep(1)
    fail("Chrome CDP did not come up within 30s")


def connect_browser(pw, chrome_proc):
    # Preferred: attach to the plain Chrome launched above, so the browser
    # itself carries no automation marker for the Google sign-in.
    try:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        log("attached to Chrome over CDP")
        return browser, ctx, page
    except Exception as e:
        log(f"CDP attach failed ({type(e).__name__}); "
            "falling back to Playwright launch")
    try:
        chrome_proc.terminate()
    except Exception:
        pass
    launch_kw = {"headless": False,
                 "args": ["--no-sandbox", "--disable-dev-shm-usage",
                          "--disable-blink-features=AutomationControlled"]}
    if PROXY:
        launch_kw["proxy"] = {"server": PROXY}
    try:
        browser = pw.chromium.launch(channel="chrome", **launch_kw)
    except Exception:
        browser = pw.chromium.launch(**launch_kw)
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    page.goto("https://gemini.google.com", wait_until="domcontentloaded",
              timeout=60000)
    log("Playwright-launched browser up")
    return browser, ctx, page


def google_cookies(ctx):
    out = {}
    try:
        for c in ctx.cookies():
            d = (c.get("domain") or "").lstrip(".").lower()
            if (d == "google.com" or d.endswith(".google.com")) and c.get("value"):
                out[c["name"]] = c["value"]
    except Exception:
        pass
    return out


def captcha_present(page):
    try:
        url = page.url or ""
    except Exception:
        url = ""
    if "challenge" in url.lower():
        return True
    try:
        text = (page.content() or "").lower()
    except Exception:
        return False
    return ("not a robot" in text) or ("i'm not a robot" in text)


def cleanup():
    for p in reversed(PROCS):
        try:
            p.terminate()
        except Exception:
            pass
    for p in reversed(PROCS):
        try:
            p.wait(timeout=5)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    for f in LOG_FILES:
        try:
            f.close()
        except Exception:
            pass
    shutil.rmtree(CHROME_PROFILE, ignore_errors=True)


def main():
    if ACCOUNT not in ("acc0", "acc2", "acc4", "acc5", "acc6", "acc8"):
        fail(f"unknown account {ACCOUNT!r}; expected acc0/acc2/acc4/acc5/acc6/acc8")
    if MODE not in ("dry_run", "login"):
        fail(f"unknown mode {MODE!r}")
    session_file = f"/tmp/gemini-session-{ACCOUNT}.json"
    log(f"gemini-mint account={ACCOUNT} mode={MODE} poll={POLL_SECS}s")
    check_proxy()
    novnc_url = start_desktop()
    log("desktop ready; browser starting")
    from playwright.sync_api import sync_playwright
    browser = None
    with sync_playwright() as pw:
        chrome_proc = start_chrome()
        try:
            try:
                browser, ctx, page = connect_browser(pw, chrome_proc)
            except Exception as e:
                fail(f"browser did not come up: {type(e).__name__}: {e}")
            try:
                page.wait_for_load_state("domcontentloaded", timeout=60000)
            except Exception:
                pass
            try:
                log(f"browser page: url={page.url} title={page.title()!r}")
            except Exception:
                log("browser page: url/title unreadable")
            if MODE == "dry_run":
                # Verify the public tunnel answers from outside the runner.
                try:
                    req = urllib.request.Request(
                        novnc_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=30) as r:
                        body = r.read()
                        status = r.status
                except Exception as e:
                    fail(f"tunnel URL fetch failed: {type(e).__name__}: {e}")
                if status != 200 or b"noVNC" not in body:
                    fail(f"tunnel URL did not serve noVNC (status={status})")
                if not wait_port("127.0.0.1", 5900, timeout=5):
                    fail("VNC port not listening")
                try:
                    page.screenshot(path="/tmp/mint-shot.png")
                    log("screenshot saved to /tmp/mint-shot.png")
                except Exception as e:
                    log(f"screenshot failed (non-fatal): {type(e).__name__}")
                log("RESULT dry_run_ok tunnel=200 novnc=yes vnc=yes")
                return 0
            # ---- login mode: wait for the owner to sign in ----
            log("WAITING FOR LOGIN: open the noVNC URL Muse hands you in chat, "
                "sign in to Gemini with this account, then leave the tab open. "
                "Do not close the browser window.")
            deadline = time.time() + POLL_SECS
            last_banner = 0.0
            while time.time() < deadline:
                if time.time() - last_banner > 300:
                    left = int(deadline - time.time())
                    log(f"still waiting for login ({left}s left); "
                        "the noVNC URL is the one Muse handed you in chat")
                    last_banner = time.time()
                try:
                    closed = page.is_closed()
                except Exception:
                    closed = True
                if closed:
                    fail("browser page was closed", code=5)
                if captcha_present(page):
                    fail("CAPTCHA_DETECTED: Google showed a challenge; "
                         "stopping without touching the secret", code=3)
                cookies = google_cookies(ctx)
                try:
                    url = page.url or ""
                except Exception:
                    url = ""
                if cookies.get("__Secure-1PSID") and url.startswith(
                        "https://gemini.google.com"):
                    names = sorted(cookies)
                    fd = os.open(session_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    with os.fdopen(fd, "w") as f:
                        json.dump(cookies, f)
                    for v in cookies.values():
                        mask(v)
                    log(f"RESULT ok cookies={len(cookies)} "
                        f"names={','.join(names)} "
                        f"psidts={'yes' if '__Secure-1PSIDTS' in cookies else 'no'} "
                        f"file={session_file}")
                    return 0
                time.sleep(15)
            fail("login_timeout: no signed-in Gemini session within "
                 f"{POLL_SECS}s; secret untouched", code=4)
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            cleanup()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        log(f"ERROR unexpected: {type(e).__name__}: {e}")
        try:
            cleanup()
        except Exception:
            pass
        sys.exit(1)
