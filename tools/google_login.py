"""Shared Google site-login account for agents (owner 2026-10-30).

ONLY ngocgminh5@gmail.com is shared with agents (momonakikugava is used by Notion AI only - never here).
Secret GOOGLE_SITE_COOKIES = its Google cookies. RULE: one agent at a time. Every use must hold the global lock
(git ref refs/locks/google-ngocg in mohammadlali0707-stack/hq-ai-results), so Google never sees the account used
from two places at once. cookies()/storage_state() refuse to work without the lock.

This repo is PUBLIC: never print/log/commit the cookies, never upload storage_state files, Playwright traces,
HAR files or screenshots of Google pages as artifacts. Values are masked in logs automatically.

Workflow step env:
    GOOGLE_SITE_COOKIES: ${{ secrets.GOOGLE_SITE_COOKIES }}
    GOOGLE_LOCK_TOKEN:   ${{ secrets.ACC6_PAT }}
Python:
    import google_login
    with google_login.session("my-agent") as gl:            # waits up to 10 min for the other agent
        ctx = browser.new_context(storage_state=gl.storage_state())
        ...                                                   # sign in to the site with Google here
Shell:
    python tools/google_login.py run -- python my_script.py  # lock + $GOOGLE_STATE file for the command
If Google shows a password page instead of the account, the session expired: stop and report it to the owner.
"""
import json, os, sys, time, threading, urllib.request, urllib.error, contextlib, tempfile, subprocess

ACCOUNT = "ngocgminh5@gmail.com"
LOCK_REPO = "mohammadlali0707-stack/hq-ai-results"
LOCK_REF = "locks/google-ngocg"
STALE = 600          # a lock without heartbeat for 10 min is considered dead
BEAT = 120
_held = threading.Event()

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
    def __init__(self, holder): self.holder, self.sha, self._stop = holder, None, threading.Event()
    def _commit(self):
        base = _api("GET", "/commits/HEAD")
        return _api("POST", "/git/commits", {"message": json.dumps({"holder": self.holder, "who": _who(), "ts": time.time()}),
                                              "tree": base["commit"]["tree"]["sha"], "parents": []})["sha"]
    def acquire(self, wait):
        end = time.time() + wait
        while True:
            sha = self._commit()
            try:
                _api("POST", "/git/refs", {"ref": "refs/" + LOCK_REF, "sha": sha}); self.sha = sha; break
            except urllib.error.HTTPError as e:
                if e.code != 422: raise
            try:
                cur = _api("GET", f"/git/ref/{LOCK_REF}")["object"]["sha"]
                info = json.loads(_api("GET", f"/git/commits/{cur}")["message"])
                if time.time() - info.get("ts", 0) > STALE:
                    print(f"google_login: breaking stale lock of {info.get('holder')}", file=sys.stderr)
                    _api("DELETE", f"/git/refs/{LOCK_REF}"); continue
                print(f"google_login: account busy (used by {info.get('holder')}), waiting...", file=sys.stderr)
            except urllib.error.HTTPError as e:
                if e.code == 404: continue
                raise
            if time.time() > end: raise Busy("Google site account is in use by another agent - try again later")
            time.sleep(20)
        _held.set(); threading.Thread(target=self._beat, daemon=True).start(); return self
    def _beat(self):
        while not self._stop.wait(BEAT):
            try:
                cur = _api("GET", f"/git/ref/{LOCK_REF}")["object"]["sha"]
                if cur != self.sha: _held.clear(); print("google_login: LOST the lock - stop using the account", file=sys.stderr); return
                new = self._commit(); _api("PATCH", f"/git/refs/{LOCK_REF}", {"sha": new, "force": True}); self.sha = new
            except Exception as e: print(f"google_login: heartbeat error {type(e).__name__}", file=sys.stderr)
    def release(self):
        self._stop.set(); _held.clear()
        try:
            if _api("GET", f"/git/ref/{LOCK_REF}")["object"]["sha"] == self.sha: _api("DELETE", f"/git/refs/{LOCK_REF}")
        except Exception: pass

def cookies():
    if not _held.is_set(): raise RuntimeError("take the lock first: with google_login.session(...) as gl")
    raw = os.environ.get("GOOGLE_SITE_COOKIES", "").strip()
    if not raw: raise RuntimeError("GOOGLE_SITE_COOKIES secret is not available in this job")
    out = []
    for c in json.loads(raw):
        if os.environ.get("GITHUB_ACTIONS") and len(str(c.get("value", ""))) > 6: print("::add-mask::" + str(c["value"]))
        d = {k: c[k] for k in ("name", "value", "domain", "path", "httpOnly", "secure") if k in c}
        if c.get("expires", -1) > 0: d["expires"] = c["expires"]
        if c.get("sameSite") in ("Strict", "Lax", "None"): d["sameSite"] = c["sameSite"]
        out.append(d)
    return out

def storage_state(path=None):
    st = {"cookies": cookies(), "origins": []}
    if not path: return st
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f: json.dump(st, f)
    return path

def add_to(context): context.add_cookies(cookies()); return context

class _Session:
    storage_state = staticmethod(storage_state); cookies = staticmethod(cookies); add_to = staticmethod(add_to)
    account = ACCOUNT

@contextlib.contextmanager
def session(holder="agent", wait=600):
    lk = _Lock(holder).acquire(wait)
    try: yield _Session()
    finally: lk.release()

if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["run"] and "--" in a:
        cmd = a[a.index("--") + 1:]
        with session(os.environ.get("GITHUB_WORKFLOW", "cli")):
            fd, p = tempfile.mkstemp(suffix=".json"); os.close(fd)
            try: storage_state(p); rc = subprocess.call(cmd, env={**os.environ, "GOOGLE_STATE": p})
            finally: os.path.exists(p) and os.remove(p)
        sys.exit(rc)
    print(__doc__)
