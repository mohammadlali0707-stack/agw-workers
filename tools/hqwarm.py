"""HQ warm worker: one long GitHub-hosted run per account that keeps the Antigravity CLI ready and serves
Flash jobs from the PRIVATE repo queue (branch wq<N>, queue/<cid>.json) -> answer in jobs/<cid>/ (same as hq-gemini.yml).
Nothing sensitive is printed: this repo is public. Claim = delete the queue file (atomic; a loser gets 404/409)."""
import base64, json, os, random, re, shutil, subprocess, threading, time, urllib.request, urllib.error
R, T, ACC = os.environ["RES_REPO"], os.environ["RES_PAT"], os.environ["ACC"]
PAR = int(os.environ.get("PAR", "3")); CLAIM_SECS = float(os.environ.get("CLAIM_SECS", "18900")); POLL = float(os.environ.get("POLL", "1.0"))
T0 = time.time(); BR = f"wq{ACC}"   # own queue branch per account: queue writes never race result commits on main
PRE = ("You are being used as a plain chat model by an API. Reply ONLY with the answer text the request below asks for (keep any requested "
       "format such as JSON exactly). Do not create or edit files and do not run commands unless the request explicitly requires it.\n\n---\n\n")
BAD = re.compile(r"too many requests|rate.?limit|\b429\b|quota|unauthorized|authentication failed|invalid (api key|token)|not logged in|timed? ?out|invalid model selection", re.I)
def gh(m, p, b=None, h=None, raw=False):
    hd = {"Authorization": "Bearer " + T, "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json"}; hd.update(h or {})
    r = urllib.request.Request(f"https://api.github.com/repos/{R}{p}", data=json.dumps(b).encode() if b is not None else None, method=m, headers=hd)
    with urllib.request.urlopen(r, timeout=60) as x:
        d = x.read(); return x, (d if raw else json.loads(d or b"{}"))
def save(cid, files):
    blobs = [{"path": f"jobs/{cid}/{n}", "mode": "100644", "type": "blob", "content": c} for n, c in files.items()]
    for attempt in range(40):
        try:
            head = gh("GET", "/git/ref/heads/main")[1]["object"]["sha"]; base = gh("GET", f"/git/commits/{head}")[1]["tree"]["sha"]
            tree = gh("POST", "/git/trees", {"base_tree": base, "tree": blobs})[1]["sha"]
            com = gh("POST", "/git/commits", {"message": f"job {cid}", "tree": tree, "parents": [head]})[1]["sha"]
            gh("PATCH", "/git/refs/heads/main", {"sha": com}); return True
        except Exception: time.sleep(random.uniform(0.5, 2.5) + min(attempt, 10) * 0.5)
    return False
HOMES = []
for n in range(PAR):   # one HOME per slot: parallel CLI processes never share state files
    h = f"/tmp/wh{n}"; shutil.copytree(os.path.expanduser("~/.gemini"), h + "/.gemini", dirs_exist_ok=True); HOMES.append(h)
LOCK = threading.Lock(); ACTIVE = [0]; STATS = {"jobs": 0, "fail": 0, "polls": 0, "changes": 0}
def run_job(cid, job, home):
    t = time.time(); out, rc = "", 1
    try:
        work = f"{home}/work"; shutil.rmtree(work, ignore_errors=True); os.makedirs(work)
        p = subprocess.run(["/tmp/agy", "--model", "gemini-3.8-flash-high", "--dangerously-skip-permissions", "--print-timeout", "1680s",
                            "-p", PRE + job.get("prompt", "")], cwd=work, env=dict(os.environ, HOME=home), stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=1700)
        out, rc = p.stdout.decode("utf-8", "replace").strip(), p.returncode
    except subprocess.TimeoutExpired: out, rc = "timed out", 124
    except Exception as e: out = f"{type(e).__name__}"
    ok = rc == 0 and bool(out) and not (len(out) < 600 and BAD.search(out))
    res = {"ok": ok, "files": [], "warm": True, "secs": round(time.time() - t, 1), **({} if ok else {"error": f"agy rc={rc} " + out[-300:]})}
    save(cid, {"answer.txt": out, "result.json": json.dumps(res)})
    with LOCK:
        ACTIVE[0] -= 1; HOMES.append(home); STATS["jobs"] += 1; STATS["fail"] += (not ok)
def claim(item):
    try: _, job = gh("GET", f"/contents/{item['path']}?ref={BR}", raw=True); job = json.loads(job)
    except Exception: return None
    try: gh("DELETE", f"/contents/{item['path']}", {"message": f"claim {item['name'][:-5]} acc{ACC}", "sha": item["sha"], "branch": BR})
    except urllib.error.HTTPError: return None   # someone else (overlapping run) claimed it
    except Exception: return None
    return job
etag, last_log = None, 0
print(f"warm worker acc{ACC} ready par={PAR}", flush=True)
while True:
    claiming = time.time() - T0 < CLAIM_SECS
    with LOCK: active = ACTIVE[0]
    if not claiming and active == 0: break
    if claiming and active < PAR:
        try:
            STATS["polls"] += 1
            x, items = gh("GET", f"/contents/queue?ref={BR}", h={"If-None-Match": etag} if etag else None)
            etag = x.headers.get("ETag"); STATS["changes"] += 1
            if int(x.headers.get("X-RateLimit-Remaining") or 9999) < 1500: time.sleep(20)
            for it in sorted((i for i in items if i["name"].endswith(".json")), key=lambda i: i["name"]):
                with LOCK:
                    if ACTIVE[0] >= PAR: break
                job = claim(it)
                if job is None: continue
                with LOCK: ACTIVE[0] += 1; home = HOMES.pop()
                threading.Thread(target=run_job, args=(it["name"][:-5], job, home), daemon=True).start()
                etag = None   # re-list right away (more may be waiting)
        except urllib.error.HTTPError as e:
            if e.code == 304:   # unchanged -> free (does not count against the API limit)
                try:
                    if int(e.headers.get("X-RateLimit-Remaining") or 9999) < 1500: time.sleep(20)   # protect the shared ACC6 API budget
                except ValueError: pass
            elif e.code == 404: etag = None; time.sleep(10)   # queue/.keep missing -> every poll would count; slow down
            else: time.sleep(10)
        except Exception: time.sleep(5)
    if time.time() - last_log > 1800: last_log = time.time(); print(f"t={int(time.time()-T0)}s {STATS}", flush=True)
    time.sleep(POLL)
print(f"done {STATS}", flush=True)
