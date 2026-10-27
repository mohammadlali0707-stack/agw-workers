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
# owner 2026-10-27: Gemini website (Argon / Pro) on the same warm run -> queue files "w-<cid>.json", served by hqwarmweb.py (one open session)
PAR_WEB = int(os.environ.get("PAR_WEB", "1")); WEB = [0]; WEBP = None
if os.environ.get("GEMINI_WEB_COOKIES") and os.path.exists("/tmp/gv/bin/python"):
    WEBP = subprocess.Popen(["/tmp/gv/bin/python", os.path.join(os.path.dirname(os.path.abspath(__file__)), "hqwarmweb.py")],
                            env=dict(os.environ, PYTHONPATH="/tmp/gwlib", GWEB_PROXY="http://127.0.0.1:10809", GEMINI_COOKIE_PATH="/tmp/gwcache"))
def web_ready(): return WEBP is not None and WEBP.poll() is None and os.path.exists("/tmp/web_ready")
def run_web(cid, job):
    t = time.time(); d = f"/tmp/wdone/{cid}"
    try:
        os.makedirs("/tmp/wjobs", exist_ok=True)
        open(f"/tmp/wjobs/{cid}.tmp", "w").write(json.dumps(job)); os.replace(f"/tmp/wjobs/{cid}.tmp", f"/tmp/wjobs/{cid}.json")
        while not os.path.isdir(d) and time.time() - t < 2150 and (WEBP.poll() is None): time.sleep(0.5)
        if os.path.isdir(d): files = {n: open(f"{d}/{n}").read() for n in ("answer.txt", "result.json")}
        else: files = {"answer.txt": "", "result.json": json.dumps({"ok": False, "files": [], "warm": True, "error": "warm web session stopped"})}
        save(cid, files); ok = json.loads(files["result.json"]).get("ok")
    except Exception as e: ok = False; save(cid, {"answer.txt": "", "result.json": json.dumps({"ok": False, "files": [], "error": f"warm web {type(e).__name__}"})})
    with LOCK: WEB[0] -= 1; STATS["jobs"] += 1; STATS["fail"] += (not ok)
SAFE_ENV = {k: v for k, v in os.environ.items() if k not in ("RES_PAT", "COOKIE_PAT", "GEMINI_WEB_COOKIES", "ACTIONS_RUNTIME_TOKEN", "GITHUB_TOKEN")}
def run_work(cid, job, home):
    """Argon project subtask (owner 2026-10-27): optional repo checkout on branch argon/..., agy does the work, push the branch, report -> private repo."""
    t = time.time(); work = f"{home}/work"; out, rc, info = "", 1, {}
    H = ["-c", "http.https://github.com/.extraHeader=AUTHORIZATION: basic " + base64.b64encode(f"x-access-token:{T}".encode()).decode()]
    def git(*a, cwd=None, check=False):
        p = subprocess.run(["git", *H, *a], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
        if check and p.returncode: raise RuntimeError(f"git {a[0]} failed rc={p.returncode}")
        return p.stdout.decode("utf-8", "replace")
    try:
        shutil.rmtree(work, ignore_errors=True); os.makedirs(work)
        repo, br = job.get("repo"), job.get("branch") or f"argon/{cid}"
        cwd = work
        if repo:
            url = f"https://github.com/{repo}.git"; cwd = f"{work}/repo"
            if subprocess.run(["git", *H, "clone", "-q", "--branch", br, url, cwd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600).returncode:
                shutil.rmtree(cwd, ignore_errors=True); git("clone", "-q", url, cwd, check=True); git("checkout", "-q", "-b", br, cwd=cwd, check=True)
            git("config", "user.email", "argon-workers@leaddesk.local", cwd=cwd); git("config", "user.name", f"Argon worker ACC{ACC}", cwd=cwd)
            base = git("rev-parse", "HEAD", cwd=cwd).strip(); info["base"] = base
        pf = f"{home}/prompt.txt"; open(pf, "w").write(job.get("prompt", ""))
        hq = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hqteam.py")
        subprocess.run(["python3", hq, "brief", pf, job.get("topic", "")], env=dict(SAFE_ENV, HQTEAM_NAME=job.get("who", "argon-task")), timeout=60,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        p = subprocess.run(["/tmp/agy", "--model", "gemini-3.8-flash-high", "--dangerously-skip-permissions", "--print-timeout", "1680s", "-p", open(pf).read()],
                           cwd=cwd, env=dict(SAFE_ENV, HOME=home, HQTEAM_NAME=job.get("who", "argon-task")), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=1750)
        out, rc = p.stdout.decode("utf-8", "replace").strip(), p.returncode
        if repo:
            git("add", "-A", cwd=cwd)
            if git("status", "--porcelain", cwd=cwd).strip(): git("commit", "-q", "-m", f"{job.get('title', 'Argon subtask')[:70]} ({cid})", cwd=cwd)
            head = git("rev-parse", "HEAD", cwd=cwd).strip(); info["head"] = head; info["branch"] = br; info["repo"] = repo
            if head != info["base"] or job.get("branch"):
                info["pushed"] = subprocess.run(["git", *H, "push", "-q", "origin", f"HEAD:refs/heads/{br}"], cwd=cwd, stdout=subprocess.DEVNULL,
                                                stderr=subprocess.DEVNULL, timeout=600).returncode == 0
            info["diffstat"] = git("diff", "--stat", info["base"], "HEAD", cwd=cwd)[-3000:]
            diff = git("diff", info["base"], "HEAD", cwd=cwd)
        else: diff = ""
    except subprocess.TimeoutExpired: out, rc = (out or "") + "\n[timed out]", 124; diff = ""
    except Exception as e: out = (out or "") + f"\n[{type(e).__name__}: {str(e)[:200]}]"; diff = ""
    ok = rc == 0 and bool(out) and not (len(out) < 600 and BAD.search(out)) and (not job.get("repo") or info.get("pushed", True))
    res = {"ok": ok, "files": [], "warm": True, "work": True, "secs": round(time.time() - t, 1), **info, **({} if ok else {"error": f"agy rc={rc} " + out[-300:]})}
    save(cid, {"answer.txt": out[-60000:], "result.json": json.dumps(res), "diff.txt": diff[:200000]})
    with LOCK:
        ACTIVE[0] -= 1; HOMES.append(home); STATS["jobs"] += 1; STATS["fail"] += (not ok)
def run_job(cid, job, home):
    t = time.time(); out, rc = "", 1
    try:
        work = f"{home}/work"; shutil.rmtree(work, ignore_errors=True); os.makedirs(work)
        p = subprocess.run(["/tmp/agy", "--model", "gemini-3.8-flash-high", "--dangerously-skip-permissions", "--print-timeout", "1680s",
                            "-p", PRE + job.get("prompt", "")], cwd=work, env=dict(SAFE_ENV, HOME=home), stdout=subprocess.PIPE,
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
    if not claiming and active == 0 and WEB[0] == 0: break
    if claiming and (active < PAR or (WEB[0] < PAR_WEB and web_ready())):
        try:
            STATS["polls"] += 1
            x, items = gh("GET", f"/contents/queue?ref={BR}", h={"If-None-Match": etag} if etag else None)
            etag = x.headers.get("ETag"); STATS["changes"] += 1
            if int(x.headers.get("X-RateLimit-Remaining") or 9999) < 1500: time.sleep(20)
            for it in sorted((i for i in items if i["name"].endswith(".json")), key=lambda i: i["name"]):
                if it["name"].startswith("w-"):
                    with LOCK:
                        if WEB[0] >= PAR_WEB or not web_ready(): continue
                    job = claim(it)
                    if job is None: continue
                    with LOCK: WEB[0] += 1
                    threading.Thread(target=run_web, args=(it["name"][2:-5], job), daemon=True).start(); etag = None; continue
                with LOCK:
                    if ACTIVE[0] >= PAR: continue
                job = claim(it)
                if job is None: continue
                with LOCK: ACTIVE[0] += 1; home = HOMES.pop()
                if it["name"].startswith("t-"): threading.Thread(target=run_work, args=(it["name"][2:-5], job, home), daemon=True).start()
                else: threading.Thread(target=run_job, args=(it["name"][:-5], job, home), daemon=True).start()
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
if WEBP is not None and WEBP.poll() is None:   # let the web session store its refreshed cookies before the run ends
    open("/tmp/web_stop", "w").write("1")
    try: WEBP.wait(150)
    except Exception: WEBP.kill()
print(f"done {STATS}", flush=True)
