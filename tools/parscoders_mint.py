#!/usr/bin/env python3
"""Mint a fresh ParsCoders session inside a GitHub runner's own headed
browser, with the owner typing the login himself through a remote view.

Runs ONLY on GitHub Actions (never on the VPS). Flow:
  1. Start Xvfb + x11vnc + noVNC + cloudflared quick tunnel. The public
     noVNC URL is written to /tmp/novnc-url.txt and NEVER printed in the
     log (this repo is PUBLIC, so run logs are public; the URL is a live
     capability to the desktop where the owner types his Google password).
     The script publishes the URL to the PRIVATE hq-ai-results repo the
     moment the tunnel is up, so the owner can log in while the login poll
     runs; the workflow's later publish step is only a no-op safety net.
     Muse hands the URL to the owner in chat.
  2. Launch real Google Chrome (headed) under Xvfb at parscoders.com/login,
     routed through GWEB_PROXY (Oracle exit) when set.
  3. dry_run: verify the tunnel URL answers HTTP 200, the browser page
     loaded, save a screenshot, exit success WITHOUT writing any secret.
  4. login: poll up to MINT_POLL_SECS for a signed-in ParsCoders session
     (parscoders.com cookies present AND the tab off /login AND the
     dashboard markers visible). When found, export the parscoders.com
     cookies + localStorage as {"cookies": [...], "localStorage": {...}}
     (the exact shape scripts/site_proposal/capture_parscoders.py writes)
     to /tmp/parscoders-session.json (0600, never printed). The workflow
     step then seals it with `gh secret set SITE_SESSION_PARSCODERS`
     and shreds the file.
If a challenge (captcha) appears, do NOT exit and NEVER try to solve
it: the owner is sitting at the browser precisely to solve it himself.
NOTE: the ParsCoders login page always embeds a reCAPTCHA iframe; that
alone is NOT a challenge. Only an actual challenge page counts. Log it plainly, keep polling, and pause the login countdown
while the challenge is on screen (extra wait capped, so the run still
ends inside the 55-minute job timeout). If the poll times out, exit
non-zero and do NOT touch the secret.
A tunnel watchdog runs during the login poll: if the cloudflared tunnel
dies, it restarts it, publishes the fresh URL early to the private
results repo (never in the log), and keeps waiting.
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

ACCOUNT = "parscoders"  # fixed: this mint is ParsCoders only
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
CHALLENGE_EXTRA_MAX = 600  # max extra poll seconds while a challenge is visible
TUNNEL_CHECK_EVERY = 4     # poll iterations between tunnel health checks
CF_PROC = None             # cloudflared Popen handle (restartable)
CF_LOG = "/tmp/cloudflared.log"
WS_PROC = None             # websockify Popen handle (restartable)
WS_CMD = None              # websockify argv, for restart


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


def _launch_cloudflared(logfile):
    """Start cloudflared and wait up to 90s for its public URL.
    Returns (proc, url) with url None on timeout. Never logs the URL."""
    cf = "/tmp/cloudflared"
    if not (os.path.isfile(cf) and os.access(cf, os.X_OK)):
        fail("cloudflared binary missing at /tmp/cloudflared")
    proc = run_bg([cf, "tunnel", "--url", "http://localhost:6080",
                   "--no-autoupdate"], logfile)
    url = None
    end = time.time() + 90
    while time.time() < end:
        try:
            with open(logfile, "r", errors="replace") as f:
                m = NOVNC_RE.search(f.read())
            if m:
                url = m.group(0)
                break
        except OSError:
            pass
        time.sleep(2)
    return proc, url


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
    global WS_PROC, WS_CMD, CF_PROC
    root = novnc_root()
    ws = shutil.which("websockify")
    WS_CMD = ([ws] if ws else [sys.executable, "-m", "websockify"]) + [
        "--web", root, "6080", "localhost:5900"]
    WS_PROC = run_bg(WS_CMD, "/tmp/websockify.log")
    if not wait_port("127.0.0.1", 6080, timeout=20):
        fail("websockify/noVNC did not come up")
    log("noVNC up on 127.0.0.1:6080")
    CF_PROC, url = _launch_cloudflared(CF_LOG)
    if not url:
        fail("cloudflared tunnel did not produce a URL within 90s")
    full_url = url + "/vnc.html"
    with open(URL_FILE, "w") as f:
        f.write(full_url + "\n")
    # The URL itself is NEVER printed: this repo's run logs are public.
    log("remote desktop is up; the noVNC URL was saved for private delivery "
        "(Muse hands it to the owner in chat)")
    return full_url


def publish_novnc_url_early():
    """Publish the noVNC URL to the private results repo NOW, while the
    login poll is still ahead of us. The workflow's later publish step
    runs only after this mint step ends, which in login mode is after the
    owner has logged in (or the 40-minute poll times out) -- far too late:
    the owner needs the URL to log in at all. That timing bug killed the
    first real login run (45157009008, cancelled). Never logs the URL
    itself. Fail-open: on failure it warns and the workflow publish step
    remains as fallback."""
    results_dir = os.environ.get("MINT_RESULTS_DIR", "hq-ai-results").strip()
    run_id = (os.environ.get("MINT_RUN_ID", "") or
              os.environ.get("RUN_ID_INPUT", "")).strip()
    if not run_id:
        log("WARNING: no run id (MINT_RUN_ID/RUN_ID_INPUT); "
            "cannot publish noVNC URL early")
        return False
    if not os.path.isfile(URL_FILE):
        log("WARNING: noVNC URL file missing; cannot publish early")
        return False
    jobdir = os.path.join(results_dir, "jobs",
                          f"parscoders-mint-{run_id}")
    rel_jobdir = os.path.join("jobs", f"parscoders-mint-{run_id}")
    try:
        os.makedirs(jobdir, exist_ok=True)
        shutil.copyfile(URL_FILE, os.path.join(jobdir, "novnc-url.txt"))
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"],
                       cwd=results_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email",
                        "github-actions[bot]@users.noreply.github.com"],
                       cwd=results_dir, check=True, capture_output=True)
        subprocess.run(["git", "add", rel_jobdir],
                       cwd=results_dir, check=True, capture_output=True)
        staged = subprocess.run(["git", "diff", "--staged", "--quiet"],
                                cwd=results_dir)
        if staged.returncode == 0:
            log("noVNC URL already published (nothing new to commit)")
            return True
        subprocess.run(
            ["git", "commit", "-m",
             f"parscoders-mint: run {run_id} noVNC URL [skip ci]"],
            cwd=results_dir, check=True, capture_output=True)
        for i in range(1, 6):
            subprocess.run(["git", "rebase", "--abort"],
                           cwd=results_dir, capture_output=True)
            pull = subprocess.run(
                ["git", "pull", "--rebase", "-X", "theirs", "origin", "main"],
                cwd=results_dir, capture_output=True)
            push = subprocess.run(["git", "push", "origin", "HEAD:main"],
                                  cwd=results_dir, capture_output=True)
            if pull.returncode == 0 and push.returncode == 0:
                log("noVNC URL published early to private results repo")
                return True
            time.sleep(i * 2)
        log("WARNING: early noVNC URL publish failed after 5 attempts; "
            "the workflow publish step remains as fallback")
        return False
    except Exception as e:
        log(f"WARNING: early noVNC URL publish failed: "
            f"{type(e).__name__}: {e}")
        return False


def _tunnel_url_ok(url):
    """True if the public noVNC URL serves the noVNC page right now."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200 and b"noVNC" in r.read()
    except Exception:
        return False


def _kill_proc(proc):
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    except Exception:
        pass


def restart_websockify():
    """Relaunch websockify if its local port went down. Returns True if
    the noVNC port is listening afterwards."""
    global WS_PROC
    log("TUNNEL WATCH: restarting websockify")
    _kill_proc(WS_PROC)
    WS_PROC = run_bg(WS_CMD, "/tmp/websockify-restart.log")
    return wait_port("127.0.0.1", 6080, timeout=20)


def restart_tunnel():
    """cloudflared died (or its tunnel went stale): relaunch it, get the
    fresh URL, save it, and re-publish it early to the private repo.
    Never logs the URL. Returns the new full URL, or None on failure."""
    global CF_PROC
    log("TUNNEL WATCH: tunnel is down; restarting cloudflared")
    _kill_proc(CF_PROC)
    logfile = "/tmp/cloudflared-restart.log"
    try:
        open(logfile, "w").close()
    except OSError:
        pass
    CF_PROC, url = _launch_cloudflared(logfile)
    if not url:
        log("TUNNEL WATCH: restarted cloudflared produced no URL")
        return None
    full_url = url + "/vnc.html"
    with open(URL_FILE, "w") as f:
        f.write(full_url + "\n")
    if publish_novnc_url_early():
        log("TUNNEL WATCH: fresh tunnel URL published early to the "
            "private repo; owner needs the new link from Muse")
        return full_url
    log("TUNNEL WATCH: fresh tunnel up but early publish failed; "
        "the workflow publish step remains as fallback")
    return full_url


def check_tunnel(novnc_url):
    """Periodic tunnel health check during the login poll. Returns
    (ok, url_to_use): ok is True if the public URL serves (or was
    revived), url_to_use is the current URL (a fresh one after a
    restart). Never logs any URL."""
    global CF_PROC
    # Local noVNC first: if websockify died, the tunnel has nothing to serve.
    if not wait_port("127.0.0.1", 6080, timeout=3):
        log("TUNNEL WATCH: local noVNC port 6080 is down")
        if not restart_websockify():
            log("TUNNEL WATCH: websockify restart failed")
            return False, novnc_url
    # cloudflared process itself gone?
    try:
        cf_dead = CF_PROC is not None and CF_PROC.poll() is not None
    except Exception:
        cf_dead = True
    if cf_dead:
        log("TUNNEL WATCH: cloudflared process is gone")
        new_url = restart_tunnel()
        return new_url is not None, new_url or novnc_url
    # Public fetch, two attempts 10s apart: a single blip must not force
    # a restart (and a new URL) on the owner.
    for attempt in (1, 2):
        if _tunnel_url_ok(novnc_url):
            return True, novnc_url
        log(f"TUNNEL WATCH: public URL check failed (attempt {attempt}/2)")
        time.sleep(10)
    log("TUNNEL WATCH: public URL down on both attempts; restarting tunnel")
    new_url = restart_tunnel()
    return new_url is not None, new_url or novnc_url


def start_chrome():
    chrome = shutil.which("google-chrome") or "/usr/bin/google-chrome"
    if not os.path.isfile(chrome):
        fail("google-chrome not found")
    env = dict(os.environ, DISPLAY=DISPLAY)
    cmd = [chrome, "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
           "--remote-debugging-port=9222",
           "--user-data-dir=" + CHROME_PROFILE,
           "--no-first-run", "--disable-features=Translate",
           "--disable-blink-features=AutomationControlled",
           "--window-size=1280,800"]
    if PROXY:
        cmd.append("--proxy-server=" + PROXY)
    cmd.append("https://parscoders.com/login")
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
    page.goto("https://parscoders.com/login", wait_until="domcontentloaded",
              timeout=60000)
    log("Playwright-launched browser up")
    return browser, ctx, page


def parscoders_cookies(ctx):
    """Full cookie dicts for parscoders.com (the shape the site-proposal
    runner consumes), plus the page's localStorage."""
    out = []
    try:
        for c in ctx.cookies():
            d = (c.get("domain") or "").lstrip(".").lower()
            if (d == "parscoders.com" or d.endswith(".parscoders.com")) and c.get("value"):
                out.append({k: c[k] for k in (
                    "name", "value", "domain", "path", "expires",
                    "httpOnly", "secure", "sameSite") if k in c})
    except Exception:
        pass
    return out


def parscoders_local_storage(page):
    try:
        return page.evaluate(
            "() => Object.fromEntries(Object.entries(localStorage))") or {}
    except Exception:
        return {}


DASH_MARKERS = ("داشبورد", "حساب در یک نگاه", "محمد لعلی", "خروج")


def logged_in(page, cookies):
    """True when the browser is signed in to ParsCoders: cookies exist,
    the tab is off /login, and a dashboard marker is visible."""
    if not cookies:
        return False
    try:
        url = page.url or ""
    except Exception:
        return False
    if "/login" in url or "/auth" in url:
        return False
    if not url.startswith("https://parscoders.com"):
        return False
    try:
        body = page.inner_text("body") or ""
    except Exception:
        body = ""
    return any(m in body for m in DASH_MARKERS)


def captcha_present(page):
    # The login page ALWAYS embeds a reCAPTCHA iframe: that alone is not a
    # challenge. Only a real challenge page counts.
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
    return (("verify it's you" in text)
            or ("unusual traffic" in text)
            or ("are you a robot" in text)
            or ("تایید کنید که ربات نیستید" in text))


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
    if MODE not in ("dry_run", "login"):
        fail(f"unknown mode {MODE!r}")
    session_file = "/tmp/parscoders-session.json"
    log(f"parscoders-mint mode={MODE} poll={POLL_SECS}s")
    check_proxy()
    novnc_url = start_desktop()
    # Publish the URL NOW: the workflow's publish step only runs after
    # this mint step ends, which in login mode is far too late (the owner
    # needs the URL to log in while the poll below runs).
    publish_novnc_url_early()
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
            # Warm-up: visit the ParsCoders home page first, like an
            # ordinary session, then settle on the login page.
            try:
                page.goto("https://parscoders.com",
                          wait_until="domcontentloaded", timeout=60000)
                time.sleep(5)
                page.goto("https://parscoders.com/login",
                          wait_until="domcontentloaded", timeout=60000)
                log("browser warmed up; on parscoders.com/login")
            except Exception as e:
                log(f"warm-up navigation failed (non-fatal): "
                    f"{type(e).__name__}; continuing on the current page")
            log("WAITING FOR LOGIN: open the noVNC URL Muse hands you in chat, "
                "sign in to ParsCoders yourself, then leave the tab open. "
                "Do not close the browser window. If a challenge appears, "
                "solve it in the browser yourself -- the wait continues while "
                "you do, and nothing here touches the challenge.")
            deadline = time.time() + POLL_SECS
            last_banner = 0.0
            last_iter = time.time()
            challenge_active = False
            challenge_extra = 0
            tunnel_ok = True
            iteration = 0
            while time.time() < deadline:
                iteration += 1
                now = time.time()
                dt = max(0.0, now - last_iter)
                last_iter = now
                if now - last_banner > 300:
                    left = int(deadline - now)
                    log(f"still waiting for login ({left}s left); "
                        "the noVNC URL is the one Muse handed you in chat")
                    last_banner = now
                try:
                    closed = page.is_closed()
                except Exception:
                    closed = True
                if closed:
                    fail("browser page was closed", code=5)
                # A challenge is the owner's job, not the script's: wait
                # for him to solve it, and pause the countdown while it is
                # on screen (extra wait capped by CHALLENGE_EXTRA_MAX, so
                # the run still ends inside the 55-minute job timeout).
                in_challenge = captcha_present(page)
                if in_challenge and not challenge_active:
                    log("CHALLENGE: a sign-in challenge is showing. "
                        "Owner: please solve it in the browser -- the wait "
                        "continues and the countdown is paused while the "
                        "challenge is on screen. Nothing here touches it.")
                    challenge_active = True
                elif not in_challenge and challenge_active:
                    log("challenge cleared; countdown resumed")
                    challenge_active = False
                if in_challenge and challenge_extra < CHALLENGE_EXTRA_MAX:
                    add = min(dt, CHALLENGE_EXTRA_MAX - challenge_extra)
                    deadline += add
                    challenge_extra += add
                # Tunnel watchdog: every few iterations, make sure the
                # owner can still reach the desktop; revive it if not.
                if iteration % TUNNEL_CHECK_EVERY == 0:
                    ok, novnc_url = check_tunnel(novnc_url)
                    if ok and not tunnel_ok:
                        log("tunnel is serving again")
                    elif not ok and tunnel_ok:
                        log("TUNNEL WATCH: tunnel is down and could not be "
                            "restarted; the login wait continues -- the owner "
                            "needs a fresh link from Muse")
                    tunnel_ok = ok
                cookies = parscoders_cookies(ctx)
                if logged_in(page, cookies):
                    local_storage = parscoders_local_storage(page)
                    session = {"cookies": cookies,
                               "localStorage": local_storage or {}}
                    fd = os.open(session_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    with os.fdopen(fd, "w") as f:
                        json.dump(session, f)
                    for c in cookies:
                        mask(c.get("value", ""))
                    log(f"RESULT ok cookies={len(cookies)} "
                        f"names={','.join(sorted(c['name'] for c in cookies))} "
                        f"localStorage_keys={len(local_storage or {})} "
                        f"file={session_file}")
                    return 0
                time.sleep(15)
            fail("login_timeout: no signed-in ParsCoders session within "
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
