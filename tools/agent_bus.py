#!/usr/bin/env python3
"""SDD-006 Agent Bus client for GitHub Actions workers (stdlib only).
Auth: GitHub OIDC — the job needs `permissions: id-token: write`. No shared secret.
  python3 tools/agent_bus.py post <topic> <kind> "<text>" [refs]     kind = status|finding|blocked|ask|done
  python3 tools/agent_bus.py read [topic] [limit]
Informational only: notes never trigger actions. Never post secrets (they are redacted and flagged)."""
import json, os, sys, urllib.parse, urllib.request
URL = os.environ.get("AGENT_BUS_URL", "https://n8n.airboxvip.top/agent-bus/")
def _token():
    u, t = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL"), os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not u or not t: raise SystemExit("agent_bus: add `permissions: id-token: write` to the job")
    r = urllib.request.Request(u + "&audience=leaddesk-agent-bus", headers={"Authorization": "Bearer " + t})
    return json.load(urllib.request.urlopen(r, timeout=20))["value"]
def _req(method, path="", body=None):
    r = urllib.request.Request(URL + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                               headers={"Authorization": "Bearer " + _token(), "Content-Type": "application/json", "User-Agent": "agw-agent-bus"})
    return json.load(urllib.request.urlopen(r, timeout=20))
def post(topic, kind, text, refs="", sender=None):
    sender = sender or os.environ.get("GITHUB_WORKFLOW", "worker").lower().replace(" ", "-")[:16]
    return _req("POST", "", {"from": sender, "topic": topic, "kind": kind, "text": text, "refs": refs})
def read(topic="", limit=20): return _req("GET", "?" + urllib.parse.urlencode({"topic": topic, "limit": limit}))["messages"]
if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["post"] and len(a) >= 4: print(json.dumps(post(a[1], a[2], a[3], a[4] if len(a) > 4 else ""), ensure_ascii=False))
    elif a[:1] == ["read"]: print(json.dumps(read(a[1] if len(a) > 1 else "", int(a[2]) if len(a) > 2 else 20), ensure_ascii=False, indent=1))
    else: print(__doc__)
