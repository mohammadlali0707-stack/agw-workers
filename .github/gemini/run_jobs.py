"""Run media jobs against the Gemini worker. The key comes from the GEMINI_WORKER_KEY secret.
Request file: .github/gemini/request.txt -> first token is the jobs file name inside .github/gemini/."""
import json, os, sys, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

BASE = os.environ.get("GEMINI_WORKER_URL", "").rstrip("/")
KEY = os.environ.get("GEMINI_WORKER_KEY", "")
HERE = os.path.dirname(os.path.abspath(__file__))


def note(title, msg, level="notice"):
    msg = str(msg).replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    print("::%s title=%s::%s" % (level, title, msg[:3500]), flush=True)


def run(job):
    body = {k: job[k] for k in ("prompt", "type", "model", "aspect_ratio", "account") if job.get(k)}
    refs = job.get("refs") or []
    if refs:
        body["reference_image_urls"] = refs
    if isinstance(body.get("prompt"), list):
        body["prompt"] = "\n".join(body["prompt"])
    tries = int(job.get("tries", 2))
    last = None
    for attempt in range(1, tries + 1):
        t0 = time.time()
        req = urllib.request.Request(BASE + "/generate", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "X-API-Key": KEY,
                                              "Authorization": "Bearer " + KEY}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=1500) as r:
                res = json.loads(r.read().decode())
            url = res.get("public_url") or res.get("media_url") or res.get("image_url")
            out = {"name": job["name"], "ok": bool(url), "url": url, "status": res.get("status"),
                   "account": res.get("account_used"), "model": res.get("model_used"),
                   "bytes": res.get("file_size_bytes"), "latency": res.get("latency_seconds"),
                   "attempt": attempt}
            if url:
                return out
            last = out
            last["raw"] = json.dumps(res)[:1500]
        except urllib.error.HTTPError as e:
            last = {"name": job["name"], "ok": False, "attempt": attempt,
                    "error": "HTTP %d %s" % (e.code, e.read().decode(errors="replace")[:1500])}
        except Exception as e:  # noqa
            last = {"name": job["name"], "ok": False, "attempt": attempt, "error": repr(e)[:1500],
                    "secs": round(time.time() - t0)}
        time.sleep(20)
    return last


def main():
    if not KEY:
        note("gemini", "GEMINI_WORKER_KEY secret is empty", "error")
        sys.exit(1)
    req = open(os.path.join(HERE, "request.txt")).read().split()
    jobs = json.load(open(os.path.join(HERE, req[0])))
    only = set(req[1:])
    if only:
        jobs = [j for j in jobs if j["name"] in only]
    note("gemini", "running %d jobs: %s" % (len(jobs), ", ".join(j["name"] for j in jobs)))
    par = max(1, min(6, len(jobs)))
    with ThreadPoolExecutor(par) as ex:
        results = list(ex.map(run, jobs))
    for r in results:
        note("gem-" + r["name"], json.dumps(r), "notice" if r.get("ok") else "warning")
    note("gemini-summary", json.dumps({r["name"]: r.get("url") for r in results}))
    rd = os.path.join(HERE, "results")
    os.makedirs(rd, exist_ok=True)
    rp = os.path.join(rd, req[0])
    old = json.load(open(rp)) if os.path.exists(rp) else {}
    for r in results:
        old.setdefault(r["name"], []).append(dict(r, run=os.environ.get("GITHUB_RUN_ID"), t=int(time.time())))
    json.dump(old, open(rp, "w"), indent=1)
    if not any(r.get("ok") for r in results):
        sys.exit(1)


main()
