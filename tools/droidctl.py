"""Remote control for the emulator: reads ctl/<cid>/cmds.json from the private repo, runs new commands, uploads screen/ui/state.
Commands: shot | tap x y | swipe x1 y1 x2 y2 ms | text t ({{GPASS}} -> secret, never echoed) | key code | sh cmd | sleep s | stop"""
import base64, json, os, re, subprocess, time, urllib.request, urllib.error
R, T, CID = os.environ["RES_REPO"], os.environ["RES_PAT"], os.environ["CID"]
ADB = os.environ.get("ANDROID_HOME", "/usr/local/lib/android/sdk") + "/platform-tools/adb"
END = time.time() + int(os.environ.get("MINUTES", "90")) * 60
SEC = os.environ.get("GPASS", "")
def gh(m, p, b=None, raw=False):
    rq = urllib.request.Request(f"https://api.github.com/repos/{R}/contents/ctl/{CID}/{p}", method=m,
        data=json.dumps(b).encode() if b is not None else None,
        headers={"Authorization": "Bearer " + T, "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json"})
    return urllib.request.urlopen(rq, timeout=60).read()
SHA = {}
def put(name, data):
    for i in range(5):
        try:
            if name not in SHA:
                try: SHA[name] = json.loads(gh("GET", name))["sha"]
                except urllib.error.HTTPError: SHA[name] = None
            b = {"message": f"ctl {CID} {name}", "content": base64.b64encode(data).decode()}
            if SHA[name]: b["sha"] = SHA[name]
            SHA[name] = json.loads(gh("PUT", name, b))["content"]["sha"]; return
        except urllib.error.HTTPError: SHA.pop(name, None); time.sleep(1 + i)
def adb(*a, t=60):
    try: return subprocess.run([ADB, *a], capture_output=True, timeout=t)
    except Exception as e: return None
def scrub(s): return s.replace(SEC, "***") if SEC else s
def esc(t):  # adb input text escaping
    return "".join(("%s" if c == " " else ("\\" + c if c in "\\\"'`$&|;<>()[]{}*?!~#^" else c)) for c in t)
def snap(state):
    r = adb("exec-out", "screencap", "-p")
    if r and r.stdout: put("shot.png", r.stdout)
    adb("shell", "uiautomator", "dump", "/sdcard/ui.xml"); u = adb("exec-out", "cat", "/sdcard/ui.xml")
    if u and u.stdout:
        x = u.stdout.decode(errors="replace")
        # compact: text/desc/id + bounds of each node
        nodes = re.findall(r'text="([^"]*)" resource-id="([^"]*)" class="([^"]*)"[^>]*content-desc="([^"]*)"[^>]*bounds="([^"]*)"', x)
        lines = [f"{b} | {t} | {d} | {rid.split('/')[-1]} | {c.split('.')[-1]}" for t, rid, c, d, b in nodes if t or d or rid]
        put("ui.txt", scrub("\n".join(lines)).encode())
    state["ts"] = time.strftime("%H:%M:%S"); put("state.json", json.dumps(state, ensure_ascii=False).encode())
done, state = 0, {"done": 0, "out": [], "alive": True}
snap(state)
while time.time() < END:
    try: cmds = json.loads(gh("GET", "cmds.json", raw=True)).get("cmds", [])
    except Exception: cmds = []
    new = [c for c in cmds if c.get("id", 0) > done]
    if not new: time.sleep(4); continue
    out = []
    for c in new:
        op = c.get("op"); a = c.get("a", [])
        if op == "stop": END = 0; break
        if op == "tap": adb("shell", "input", "tap", *map(str, a))
        elif op == "swipe": adb("shell", "input", "swipe", *map(str, a))
        elif op == "key": adb("shell", "input", "keyevent", *map(str, a))
        elif op == "text":
            t = a[0].replace("{{GPASS}}", SEC); adb("shell", "input", "text", esc(t))
        elif op == "sh":
            r = adb("shell", a[0], t=180); out.append(scrub((r.stdout + r.stderr).decode(errors="replace")[-1500:]) if r else "ERR")
        elif op == "host":
            r = subprocess.run(a[0], shell=True, capture_output=True, timeout=600); out.append(scrub((r.stdout + r.stderr).decode(errors="replace")[-1500:]))
        elif op == "sleep": time.sleep(float(a[0]))
        done = c["id"]; time.sleep(0.8)
    time.sleep(1.5); state.update(done=done, out=out); snap(state)
state["alive"] = False; put("state.json", json.dumps(state).encode())
