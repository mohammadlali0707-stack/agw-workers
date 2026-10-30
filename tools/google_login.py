"""Shared Google site-login account for agents (owner 2026-10-30).

Two Google accounts reserved for signing in to websites, each with its OWN session (separate from Notion AI's):
  ngocg  = ngocgminh5@gmail.com      secret GOOGLE_SITE_COOKIES
  momona = momonakikugava@gmail.com  secret GOOGLE_SITE_COOKIES_MOMONA
RULE: one agent per account at a time. Every use must hold that account's global lock (git ref
refs/locks/google-<name> in mohammadlali0707-stack/hq-ai-results), so Google never sees one session used from two
places at once. session() takes whichever account is free (or the one you ask for); cookies() refuse without it.

This repo is PUBLIC: never print/log/commit the cookies, never upload storage_state files, Playwright traces,
HAR files or screenshots of Google pages as artifacts. Values are masked in logs automatically.

Workflow step env:
    GOOGLE_SITE_COOKIES: ${{ secrets.GOOGLE_SITE_COOKIES }}
    GOOGLE_SITE_COOKIES_MOMONA: ${{ secrets.GOOGLE_SITE_COOKIES_MOMONA }}
    GOOGLE_LOCK_TOKEN:   ${{ secrets.ACC6_PAT }}
Python:
    import google_login
    with google_login.session("my-agent") as gl:            # any free account; waits up to 10 min if both busy
        # google_login.session("my-agent", account="momona") -> only that account; gl.account = its email
        ctx = browser.new_context(storage_state=gl.storage_state())
        ...                                                   # sign in to the site with Google here
Shell:
    python tools/google_login.py run -- python my_script.py  # lock + $GOOGLE_STATE file for the command
If Google shows a password page instead of the account, the session expired: stop and report it to the owner.
"""
import json, os, sys, time, threading, urllib.request, urllib.error, contextlib, tempfile, subprocess

ACCOUNTS = {"ngocg": ("ngocgminh5@gmail.com", "GOOGLE_SITE_COOKIES"),
            "momona": ("momonakikugava@gmail.com", "GOOGLE_SITE_COOKIES_MOMONA")}
LOCK_REPO = "mohammadlali0707-stack/hq-ai-results"
STALE = 600          # a lock without heartbeat for 10 min is considered dead
BEAT = 120
_held = {}   # name -> threading.Event

class Busy(RuntimeError): pass

def _api(method, path, body=None):
    tok = os.environ.get("GOOGLE_LOCK_TOKEN") or os.environ.get("ACC6_PAT") or ""
    if not tok: raise RuntimeError("GOOGLE_LOCK_TOKEN (secrets.ACC6_PAT) missing - cannot take the account lock")
    r = urllib.request.Request(f"https://api.github.com/repos/{LOCK_REPO}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "token " + tok, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(r, timeout=30) as f:
        t = f.read(); return json.loads(t) if t else {}

def _who():
    return os.environ.get("GITHUB_REPOSITORY", "local") + "#" + os.environ.get("GITHUB_RUN_ID", str(os.getpid()))

class _Lock:
    def __init__(self, holder, name):
        self.holder, self.name, self.sha, self._stop = holder, name, None, threading.Event()
        self.ref = "locks/google-" + name; self.ev = _held.setdefault(name, threading.Event())
    def _commit(self):
        base = _api("GET", "/commits/HEAD")
        return _api("POST", "/git/commits", {"message": json.dumps({"holder": self.holder, "who": _who(), "ts": time.time()}),
                                              "tree": base["commit"]["tree"]["sha"], "parents": []})["sha"]
    def try_once(self):
        for _ in range(3):
            sha = self._commit()
            try:
                _api("POST", "/git/refs", {"ref": "refs/" + self.ref, "sha": sha}); self.sha = sha
                self.ev.set(); threading.Thread(target=self._beat, daemon=True).start(); return True
            except urllib.error.HTTPError as e:
                if e.code != 422: raise
            try:
                cur = _api("GET", f"/git/ref/{self.ref}")["object"]["sha"]
                info = json.loads(_api("GET", f"/git/commits/{cur}")["message"])
                if time.time() - info.get("ts", 0) > STALE:
                    print(f"google_login: breaking stale {self.name} lock of {info.get('holder')}", file=sys.stderr)
                    _api("DELETE", f"/git/refs/{self.ref}"); continue
                print(f"google_login: {self.name} busy (used by {info.get('holder')})", file=sys.stderr); return False
            except urllib.error.HTTPError as e:
                if e.code == 404: continue
                raise
        return False
    def _beat(self):
        while not self._stop.wait(BEAT):
            try:
                cur = _api("GET", f"/git/ref/{self.ref}")["object"]["sha"]
                if cur != self.sha: self.ev.clear(); print("google_login: LOST the lock - stop using the account", file=sys.stderr); return
                new = self._commit(); _api("PATCH", f"/git/refs/{self.ref}", {"sha": new, "force": True}); self.sha = new
            except Exception as e: print(f"google_login: heartbeat error {type(e).__name__}", file=sys.stderr)
    def release(self):
        self._stop.set(); self.ev.clear()
        try:
            if _api("GET", f"/git/ref/{self.ref}")["object"]["sha"] == self.sha: _api("DELETE", f"/git/refs/{self.ref}")
        except Exception: pass

def cookies(name):
    if not _held.get(name, threading.Event()).is_set(): raise RuntimeError("take the lock first: with google_login.session(...) as gl")
    var = ACCOUNTS[name][1]; raw = os.environ.get(var, "").strip()
    if not raw: raise RuntimeError(f"{var} secret is not available in this job")
    out = []
    for c in json.loads(raw):
        if os.environ.get("GITHUB_ACTIONS") and len(str(c.get("value", ""))) > 6: print("::add-mask::" + str(c["value"]))
        d = {k: c[k] for k in ("name", "value", "domain", "path", "httpOnly", "secure") if k in c}
        if c.get("expires", -1) > 0: d["expires"] = c["expires"]
        if c.get("sameSite") in ("Strict", "Lax", "None"): d["sameSite"] = c["sameSite"]
        out.append(d)
    return out

def storage_state(name, path=None):
    st = {"cookies": cookies(name), "origins": []}
    if not path: return st
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f: json.dump(st, f)
    return path

class _Session:
    def __init__(self, name): self.name, self.account = name, ACCOUNTS[name][0]
    def cookies(self): return cookies(self.name)
    def storage_state(self, path=None): return storage_state(self.name, path)
    def add_to(self, context): context.add_cookies(cookies(self.name)); return context

@contextlib.contextmanager
def session(holder="agent", account=None, wait=600):
    names = [account] if account else [n for n in ACCOUNTS if os.environ.get(ACCOUNTS[n][1])] or list(ACCOUNTS)
    end = time.time() + wait; lk = None
    while lk is None:
        for n in names:
            l = _Lock(holder, n)
            if l.try_once(): lk = l; break
        if lk is None:
            if time.time() > end: raise Busy("all Google site accounts are in use by other agents - try again later")
            time.sleep(20)
    try: yield _Session(lk.name)
    finally: lk.release()

if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["run"] and "--" in a:
        cmd = a[a.index("--") + 1:]
        with session(os.environ.get("GITHUB_WORKFLOW", "cli"), os.environ.get("GOOGLE_ACCOUNT") or None) as gl:
            fd, p = tempfile.mkstemp(suffix=".json"); os.close(fd)
            try: gl.storage_state(p); rc = subprocess.call(cmd, env={**os.environ, "GOOGLE_STATE": p, "GOOGLE_ACCOUNT_EMAIL": gl.account})
            finally: os.path.exists(p) and os.remove(p)
        sys.exit(rc)
    print(__doc__)
