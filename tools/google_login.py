"""Shared Google site-login session for every agent (owner 2026-10-30).

Secret GOOGLE_SITE_COOKIES (set in every agw-workers repo) holds the Google cookies of TWO accounts that are
reserved for signing in to websites ("Sign in with Google"):
  /u/0 = momonakikugava@gmail.com     /u/1 = ngocgminh5@gmail.com
They are NOT part of the GitHub/Gemini pool. Never print, log, commit or upload these cookies.

Workflow step:   env:  GOOGLE_SITE_COOKIES: ${{ secrets.GOOGLE_SITE_COOKIES }}
Playwright (py): import google_login; ctx = browser.new_context(storage_state=google_login.storage_state())
                 or: google_login.add_to(context)
CLI:             python tools/google_login.py state.json   -> writes a Playwright storage_state file (chmod 600)
On a Google account chooser, click the wanted email. If Google shows a sign-in page instead, the session
expired: report it to the owner (do not try to log in with a password).
"""
import json, os, sys

ACCOUNTS = {"momona": "momonakikugava@gmail.com", "ngocg": "ngocgminh5@gmail.com"}

def cookies():
    raw = os.environ.get("GOOGLE_SITE_COOKIES", "").strip()
    if not raw: raise RuntimeError("GOOGLE_SITE_COOKIES secret is not available in this job")
    out = []
    for c in json.loads(raw):
        d = {k: c[k] for k in ("name", "value", "domain", "path", "httpOnly", "secure") if k in c}
        if c.get("expires", -1) > 0: d["expires"] = c["expires"]
        if c.get("sameSite") in ("Strict", "Lax", "None"): d["sameSite"] = c["sameSite"]
        out.append(d)
    return out

def storage_state(path=None):
    st = {"cookies": cookies(), "origins": []}
    if path:
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f: json.dump(st, f)
        return path
    return st

def add_to(context):
    context.add_cookies(cookies()); return context

if __name__ == "__main__":
    print(storage_state(sys.argv[1] if len(sys.argv) > 1 else "google_state.json"), "written")
