"""HQ Study job on a GitHub runner. Job: /tmp/job.json -> /tmp/out/result.json (+ data.json).
actions: subs (title search on OpenSubtitles, or an uploaded subtitle file ref) | yt_search (YouTube search, metadata only)."""
import gzip, io, json, os, re, subprocess, urllib.parse, urllib.request, zipfile
OUT = "/tmp/out"; os.makedirs(OUT, exist_ok=True)
job = json.load(open("/tmp/job.json"))
def done(ok, data=None, **kw):
    if data is not None: json.dump(data, open(OUT + "/data.json", "w"), ensure_ascii=False)
    json.dump(dict(ok=ok, **kw), open(OUT + "/result.json", "w"), ensure_ascii=False); raise SystemExit(0)
OSH = {"User-Agent": "TemporaryUserAgent", "X-User-Agent": "TemporaryUserAgent"}
def fetch(url, headers=None, t=60):
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"}), timeout=t).read()
def decode(b):
    if b[:2] == b"\x1f\x8b": b = gzip.decompress(b)
    if b[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(b)); names = sorted((n for n in z.namelist() if n.lower().endswith((".srt", ".vtt", ".ass", ".ssa", ".sub"))), key=lambda n: -z.getinfo(n).file_size)
        if not names: raise ValueError("no subtitle file inside the zip")
        b = z.read(names[0])
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            s = b.decode(enc)
            if enc == "utf-16" and not (b[:2] in (b"\xff\xfe", b"\xfe\xff")): continue
            return s
        except Exception: pass
    return b.decode("utf-8", "replace")
AD = re.compile(r"opensubtitles|osdb\.link|subtitles? (by|ripped|synced|downloaded|corrected)|www\.|\.com\b|\.org\b|subscene|yify|addic7ed|advertise|support us and become vip", re.I)
def clean(s):
    s = s.replace("\r", "")
    cues = []
    if re.search(r"^\[Script Info\]", s, re.M | re.I):   # ASS/SSA
        for l in s.split("\n"):
            if l.startswith("Dialogue:"):
                f = l.split(",", 9)
                if len(f) == 10: cues.append((f[1].strip(), re.sub(r"\{[^}]*\}", "", f[9]).replace("\\N", " ").replace("\\n", " ")))
    else:
        for block in re.split(r"\n\s*\n", s):
            ls = [x for x in block.split("\n") if x.strip()]
            ts = next((x for x in ls if "-->" in x), None)
            if not ts: continue
            txt = " ".join(x for x in ls[ls.index(ts) + 1:])
            cues.append((ts.split("-->")[0].strip(), txt))
    out, prev = [], None
    for ts, t in cues:
        t = re.sub(r"<[^>]+>|\{[^}]*\}", "", t)
        t = re.sub(r"\[[^\]]*\]|\([A-Z][A-Z\s,'.!-]*\)|♪+|#", " ", t)
        t = re.sub(r"(?:(?<=^)|(?<=\s)|(?<=-))[A-Z][A-Z .'-]{1,24}:\s*", "", t)   # SPEAKER:
        t = re.sub(r"\s+", " ", t).strip(" -")
        if not t or not re.search(r"[A-Za-z]{2}", t) or AD.search(t) or t == prev: continue
        m = re.match(r"(\d+):(\d+):(\d+)", ts); tm = f"{int(m.group(1))*60+int(m.group(2)):d}:{m.group(3)}" if m else ""
        out.append(f"{tm} {t}".strip()); prev = t
    return out
def parse_query(q):
    q = q.strip(); season = episode = year = None
    m = re.search(r"\bs(\d{1,2})\s*e(\d{1,3})\b|\b(\d{1,2})x(\d{1,3})\b|season\s*(\d{1,2}).{0,4}episode\s*(\d{1,3})", q, re.I)
    if m:
        g = [x for x in m.groups() if x]; season, episode = int(g[0]), int(g[1]); q = (q[:m.start()] + " " + q[m.end():]).strip()
    y = re.search(r"\(?\b(19[3-9]\d|20[0-4]\d)\b\)?", q)
    if y and len(q.replace(y.group(0), "").strip()) > 1: year = y.group(1); q = q.replace(y.group(0), " ")
    q = re.sub(r"\s+", " ", re.sub(r"[^\w\s'&:-]", " ", q)).strip()
    return q, season, episode, year
def os_search(q, season, episode):
    parts = []
    if episode: parts.append(f"episode-{episode}")
    parts.append("query-" + urllib.parse.quote(q.lower()))
    if season: parts.append(f"season-{season}")
    parts.append("sublanguageid-eng")
    return json.loads(fetch("https://rest.opensubtitles.org/search/" + "/".join(parts), OSH))
def subs():
    if job.get("ref"):
        b = fetch(f"https://api.github.com/repos/{os.environ['RES_REPO']}/contents/{job['ref']}", {"Authorization": "Bearer " + os.environ["RES_PAT"], "Accept": "application/vnd.github.raw"})
        lines = clean(decode(b)); title = job.get("title") or "Your subtitle file"
        if len(lines) < 5: done(False, error="That file did not contain readable English subtitles.")
        done(True, {"title": title, "source": "uploaded file", "lines": lines}, title=title, count=len(lines))
    q, season, episode, year = parse_query(job.get("query", ""))
    if len(q) < 2: done(False, error="Please send a movie or series name.")
    res = os_search(q, season, episode)
    def ok(x):
        if x.get("SubFormat") not in ("srt", "vtt", "ass", "ssa"): return False
        if season and (str(x.get("SeriesSeason")) != str(season) or str(x.get("SeriesEpisode")) != str(episode)): return False
        if year and str(x.get("MovieYear")) not in (year, str(int(year) - 1), str(int(year) + 1)): return False
        if not season and x.get("MovieKind") == "episode" and not job.get("allow_episode"): return False
        return True
    c = [x for x in res if ok(x)] or ([x for x in res if x.get("SubFormat") == "srt"] if not (season or year) else [])
    if not c: done(False, error="No English subtitles were found for that title. Check the spelling, add the year (Inception 2010) or the episode (Friends S01E01), or upload the .srt file.")
    key = lambda x: (x.get("MovieImdbRating") is not None, int(x.get("SubDownloadsCnt") or 0))
    best = max(c, key=key)
    tt = (best.get("MovieName") or q).strip()
    m = re.match(r'^"([^"]+)"\s*(.*)$', tt)
    if m: tt = m.group(1) + (" — " + m.group(2) if m.group(2) else "")
    if best.get("MovieKind") == "episode" and best.get("SeriesSeason") not in (None, "0"):
        tt = f"{tt} (S{int(best['SeriesSeason']):02d}E{int(best['SeriesEpisode']):02d})"
    elif best.get("MovieYear"): tt = f"{tt} ({best['MovieYear']})"
    last = None
    for x in sorted(c, key=key, reverse=True)[:4]:
        try:
            lines = clean(decode(fetch(x["SubDownloadLink"], OSH)))
            if len(lines) >= 30: done(True, {"title": tt, "source": "OpenSubtitles", "imdb": best.get("IDMovieImdb"), "lines": lines}, title=tt, count=len(lines))
        except SystemExit: raise
        except Exception as e: last = e
    done(False, error="Subtitles were found but could not be downloaded" + (f" ({type(last).__name__})" if last else "") + ". Try again or upload the .srt file.")
def yt_search():
    Y = os.path.expanduser("~/.local/bin/yt-dlp"); seen, vids = set(), []
    for q in (job.get("queries") or [])[:4]:
        try:
            p = subprocess.run([Y, "--flat-playlist", "-J", f"ytsearch{int(job.get('per', 4))}:{q}"], capture_output=True, text=True, timeout=90)
            for e in (json.loads(p.stdout).get("entries") or []):
                if not e.get("id") or e["id"] in seen or (e.get("duration") or 0) > 3 * 3600 or "/shorts/" in str(e.get("url")): continue
                seen.add(e["id"]); vids.append({"id": e["id"], "title": e.get("title"), "channel": e.get("channel") or e.get("uploader"), "duration": e.get("duration"), "views": e.get("view_count"), "q": q})
        except Exception: pass
    if not vids: done(False, error="YouTube search returned no videos.")
    done(True, {"videos": vids}, count=len(vids))

def _cookies():
    raw = (os.environ.get("GEMINI_WEB_COOKIES") or "").strip()
    if not raw: done(False, error="The Gemini Notebook account session is missing.")
    ck = json.loads(raw)
    if isinstance(ck, list): ck = {c.get("name"): c.get("value") for c in ck if isinstance(c, dict)}
    return ck
def _writeback(orig, path):
    """Store the refreshed Google session back into this account's own secret (same scheme as hqjob.py)."""
    try:
        st = json.load(open(path)); new = dict(orig)
        for c in st.get("cookies", []):
            d = str(c.get("domain") or "").lstrip(".").lower()
            if (d == "google.com" or d.endswith(".google.com")) and c.get("value") and (c["name"] not in new or d == "google.com"): new[c["name"]] = c["value"]
        if new == orig: return "unchanged"
        from nacl import encoding, public
        import base64 as b64
        repo, tok, idx = os.environ["PUB_REPO"], os.environ["COOKIE_PAT"], os.environ["ACC_IDX"]
        H = {"Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json"}
        k = json.loads(urllib.request.urlopen(urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/secrets/public-key", headers=H), timeout=60).read())
        enc = b64.b64encode(public.SealedBox(public.PublicKey(k["key"].encode(), encoding.Base64Encoder())).encrypt(json.dumps(new).encode())).decode()
        urllib.request.urlopen(urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/secrets/GEMINI_WEB_COOKIES_ACC{idx}", data=json.dumps({"encrypted_value": enc, "key_id": k["key_id"]}).encode(), method="PUT", headers=H), timeout=60)
        return "saved"
    except Exception as e: return f"save failed {type(e).__name__}"
def notebook():
    import asyncio
    from notebooklm import NotebookLMClient
    ck = _cookies(); names = sorted(ck)
    if not {"SID", "__Secure-1PSIDTS"} <= set(ck): done(False, error="session lacks NotebookLM cookies", names=[n for n in names if n.isupper() or n.startswith("__")][:40])
    path = "/tmp/nlm_state.json"
    json.dump({"cookies": [{"name": n, "value": v, "domain": ".google.com", "path": "/", "expires": -1, "httpOnly": True, "secure": True, "sameSite": "None"} for n, v in ck.items() if isinstance(v, str) and v], "origins": []}, open(path, "w"))
    async def run():
        async with NotebookLMClient.from_storage(path, allow_headless=True) as c:
            nb = await c.notebooks.create("HQ video " + os.environ.get("CID", "")[-8:])
            try:
                src = await c.sources.add_url(nb.id, job["url"], wait=True, wait_timeout=600)
                ans = await c.chat.ask(nb.id, job["question"])
                return getattr(ans, "answer", None) or str(ans)
            finally:
                try: await c.notebooks.delete(nb.id)
                except Exception: pass
    try: text = asyncio.run(run())
    except Exception as e:
        wb = _writeback(ck, path); done(False, error=f"notebook {type(e).__name__}: {str(e)[:240]}", session=wb)
    wb = _writeback(ck, path)
    if not (text or "").strip(): done(False, error="Gemini Notebook returned an empty answer.", session=wb)
    done(True, {"text": text.strip()}, session=wb)
try:
    {"subs": subs, "yt_search": yt_search, "notebook": notebook}[job.get("action")]()
except SystemExit: raise
except Exception as e:
    done(False, error=f"{type(e).__name__}: {str(e)[:200]}")
