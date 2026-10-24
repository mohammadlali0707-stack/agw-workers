"""Commit /tmp/out/* to the private results repo under jobs/<cid>/ (Git Data API, retried for concurrent pushes)."""
import base64, json, os, time, urllib.request, urllib.error
R, T, CID = os.environ["RES_REPO"], os.environ["RES_PAT"], os.environ["CID"]
def gh(m, p, b=None):
    r = urllib.request.Request(f"https://api.github.com/repos/{R}{p}", data=json.dumps(b).encode() if b is not None else None, method=m,
                               headers={"Authorization": "Bearer " + T, "Accept": "application/vnd.github+json"})
    return json.loads(urllib.request.urlopen(r, timeout=120).read() or b"{}")
OUT = "/tmp/out"
if not os.path.exists(OUT + "/result.json"):
    os.makedirs(OUT, exist_ok=True); json.dump({"ok": False, "error": "runner produced no result"}, open(OUT + "/result.json", "w"))
paths = []
for root, _, fs in os.walk(OUT):
    for f in fs:
        full = os.path.join(root, f); paths.append((os.path.relpath(full, OUT), full))
paths.sort(key=lambda x: x[0] == "result.json")   # result.json last is irrelevant (one commit) but keep order stable
import random
def entry(rel, full):
    data = open(full, "rb").read(); e = {"path": f"jobs/{CID}/{rel}", "mode": "100644", "type": "blob"}
    if len(data) < 400000 and rel.endswith((".json", ".txt")):
        try: e["content"] = data.decode("utf-8"); return e   # inline -> no separate blob request
        except UnicodeDecodeError: pass
    for i in range(6):
        try: e["sha"] = gh("POST", "/git/blobs", {"content": base64.b64encode(data).decode(), "encoding": "base64"})["sha"]; return e
        except urllib.error.HTTPError: time.sleep(2 + i * 2)
    raise SystemExit("blob upload failed")
blobs = [entry(rel, full) for rel, full in paths]
# each job writes only its own folder, so only the branch pointer can race; non-forced update + jittered retry
for attempt in range(40):
    try:
        head = gh("GET", "/git/ref/heads/main")["object"]["sha"]
        base = gh("GET", f"/git/commits/{head}")["tree"]["sha"]
        tree = gh("POST", "/git/trees", {"base_tree": base, "tree": blobs})["sha"]
        com = gh("POST", "/git/commits", {"message": f"job {CID}", "tree": tree, "parents": [head]})["sha"]
        gh("PATCH", "/git/refs/heads/main", {"sha": com}); print("saved", len(blobs), "files"); break
    except urllib.error.HTTPError as e:
        print("retry", e.code); time.sleep(random.uniform(0.5, 2.5) + min(attempt, 10) * 0.5)
else: raise SystemExit("could not save results")
