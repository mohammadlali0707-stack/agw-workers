"""HQ Study job on a GitHub runner. Job: /tmp/job.json -> /tmp/out/result.json (+ data.json).
actions: subs (title search on OpenSubtitles, or an uploaded subtitle file ref) | yt_search (YouTube search, metadata only) | yt_captions (one video's captions)
| lrc (song lyrics + YouTube candidates) | gutenberg (public-domain book chapters) | tts (English voice note -> files/voice.ogg)."""
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
def yt_captions():   # SDD-005 M01: captions of one YouTube video -> timestamped plain transcript (no download)
    import glob
    Y = os.path.expanduser("~/.local/bin/yt-dlp"); d = "/tmp/ytc"; os.makedirs(d, exist_ok=True)
    p = subprocess.run([Y, "--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", "en.*,en,fa,fa-orig", "--sub-format", "vtt",
                        "-o", d + "/v.%(ext)s", "--print-json", "--no-warnings", job["url"]], capture_output=True, text=True, timeout=240)
    meta = {}
    for l in reversed((p.stdout or "").strip().splitlines()):
        try: meta = json.loads(l); break
        except Exception: pass
    fs = glob.glob(d + "/*.vtt")
    if not fs: done(False, error="No captions: " + ((p.stderr or "").strip().splitlines() or ["none"])[-1][:200])
    def rank(f):
        n = os.path.basename(f); auto = bool(meta.get("automatic_captions")) and n.split(".")[-2] in (meta.get("automatic_captions") or {}) and n.split(".")[-2] not in (meta.get("subtitles") or {})
        return (0 if ".en" in n else 1, 1 if auto else 0, len(n))
    f = sorted(fs, key=rank)[0]; out, last, mark = [], "", -999
    for blk in open(f, encoding="utf-8", errors="replace").read().split("\n\n"):
        m = re.search(r"(\d+):(\d\d):(\d\d)\.\d+\s+-->", blk) or re.search(r"(?:^|\n)()(\d\d):(\d\d)\.\d+\s+-->", blk)
        if not m: continue
        sec = int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        txt = " ".join(re.sub(r"<[^>]+>", "", l).strip() for l in blk.split("\n") if "-->" not in l and not l.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) and l.strip())
        txt = re.sub(r"\s+", " ", txt).strip()
        if not txt or txt == last or (last and txt in last): continue
        if last and txt.startswith(last): txt = txt[len(last):].strip()
        last = txt if not out or txt not in out[-1] else last
        if sec - mark >= 30: out.append(f"\n[{sec // 60}:{sec % 60:02d}] "); mark = sec
        out.append(txt + " ")
    text = re.sub(r"[ ]{2,}", " ", "".join(out)).strip()
    if len(text) < 200: done(False, error="Captions were empty.")
    done(True, {"title": meta.get("title"), "channel": meta.get("channel") or meta.get("uploader"), "duration": meta.get("duration"), "lang": os.path.basename(f).split(".")[-2], "text": text[:120000]}, count=len(text))

# ---------- owner 2026-10-27: Music / Book English lookups and voice notes run here, not on the 2 GB server ----------
UA = {"User-Agent": "LeadDesk-HQ/1.0 (personal English study)"}
BADV = re.compile(r"\b(live|cover|remix|karaoke|instrumental|acoustic|sped up|slowed|nightcore|8d)\b", re.I)
def _yts(q, n=4):
    Y = os.path.expanduser("~/.local/bin/yt-dlp")
    try:
        p = subprocess.run([Y, "--flat-playlist", "-J", f"ytsearch{n}:{q}"], capture_output=True, text=True, timeout=90)
        return [{"id": e["id"], "title": e.get("title"), "channel": e.get("channel") or e.get("uploader"), "duration": e.get("duration")}
                for e in (json.loads(p.stdout).get("entries") or []) if e.get("id") and "/shorts/" not in str(e.get("url"))]
    except Exception: return []
def lrc():   # song lyrics (lrclib.net, synced when possible) + YouTube candidates for the audio, music video and lyric video
    title, artist, q = (job.get("title") or "").strip(), (job.get("artist") or "").strip(), (job.get("q") or "").strip()
    rs = []
    if title and artist:
        try: rs = json.loads(fetch("https://lrclib.net/api/search?" + urllib.parse.urlencode({"track_name": title, "artist_name": artist}), UA, 30))
        except Exception: rs = []
    if not rs:
        try: rs = json.loads(fetch("https://lrclib.net/api/search?" + urllib.parse.urlencode({"q": q or f"{artist} {title}"}), UA, 30))
        except Exception as e: done(False, error=f"lyrics search failed ({type(e).__name__})")
    want_bad = bool(BADV.search(q + " " + title))
    rs = [r for r in rs if (r.get("plainLyrics") or r.get("syncedLyrics")) and not r.get("instrumental")]
    rs.sort(key=lambda r: ((bool(BADV.search(f"{r.get('trackName', '')} {r.get('albumName') or ''}")) and not want_bad), " - " in (r.get("trackName") or ""), not r.get("syncedLyrics")))
    if not rs: done(False, error="No lyrics were found for that song.")
    b = {k: rs[0].get(k) for k in ("id", "trackName", "artistName", "albumName", "duration", "plainLyrics", "syncedLyrics")}
    a, t = b["artistName"] or artist, b["trackName"] or title
    yt = {"audio": _yts(f"{a} {t} official audio"), "video": _yts(f"{a} {t} official music video"), "lyric": _yts(f"{a} {t} lyrics")} if job.get("yt", True) else {}
    done(True, {"lrc": b, "yt": yt}, title=t)
def gutenberg():   # a public-domain book from Project Gutenberg, split into chapters
    title, author = (job.get("title") or "").strip(), (job.get("author") or "").strip()
    q = f"{title} {author.split()[-1] if author else ''}".strip()
    j = json.loads(fetch("https://gutendex.com/books/?" + urllib.parse.urlencode({"search": q, "languages": "en"}), UA, 60))
    key = title.lower().split(":")[0].strip(); best = None
    for bk in j.get("results") or []:
        if key and key in (bk.get("title") or "").lower():
            f = bk.get("formats") or {}; u = next((v for m, v in f.items() if m.startswith("text/plain")), None)
            if u: best = (bk, u); break
    if not best: done(False, error="This book is not in Project Gutenberg (not public domain).")
    bk, u = best; raw = fetch(u, UA, 120).decode("utf-8", "ignore")
    s0 = raw.find("*** START"); e0 = raw.find("*** END"); body = raw[raw.find("\n", s0) + 1 if s0 >= 0 else 0: e0 if e0 > 0 else len(raw)]
    heads = list(re.finditer(r"^\s*((?:CHAPTER|Chapter|BOOK|Book|PART|Part|LETTER|Letter|STAVE|Stave)\s+[IVXLCDM\d]+[^\n]{0,80}|[IVXLC]+\.\s[^\n]{0,60})\s*$", body, re.M))
    chs = []
    for i, m in enumerate(heads):
        txt = body[m.end(): heads[i + 1].start() if i + 1 < len(heads) else len(body)].strip()
        if len(txt) > 800: chs.append({"name": m.group(1).strip()[:80], "text": txt[:60000]})
    if not chs: chs = [{"name": f"Section {i + 1}", "text": body[k:k + 15000]} for i, k in enumerate(range(0, min(len(body), 450000), 15000))]
    done(True, {"id": bk["id"], "title": bk.get("title"), "url": f"https://www.gutenberg.org/ebooks/{bk['id']}", "chapters": chs[:80]}, count=len(chs))
def tts():   # English voice note: edge-tts in parallel chunks -> one mono ogg/opus file in files/voice.ogg
    import asyncio, sys, site, importlib
    import shutil
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--user", "edge-tts"] + ([] if shutil.which("ffmpeg") else ["imageio-ffmpeg"]), capture_output=True, timeout=240)
    importlib.invalidate_caches(); sys.path.append(site.getusersitepackages()); import edge_tts
    ff = shutil.which("ffmpeg")
    if not ff: import imageio_ffmpeg; ff = imageio_ffmpeg.get_ffmpeg_exe()   # the runner image has no ffmpeg; this is a static build with libopus
    text, voice, rate = str(job.get("text") or "")[:6000], job.get("voice") or "en-US-AndrewNeural", job.get("rate") or "-5%"
    ch, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) > 600: ch.append(cur); cur = ""
        cur += line + "\n"
    if cur.strip(): ch.append(cur)
    if not ch: done(False, error="empty text")
    parts = [f"/tmp/vo_{i}.mp3" for i in range(len(ch))]
    async def go():
        sem = asyncio.Semaphore(4)
        async def one(t, f):
            async with sem: await edge_tts.Communicate(t, voice, rate=rate).save(f)
        await asyncio.gather(*(one(t, f) for t, f in zip(ch, parts)))
    asyncio.run(go())
    open("/tmp/vo.txt", "w").write("".join(f"file '{f}'\n" for f in parts)); os.makedirs(OUT + "/files", exist_ok=True)
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "/tmp/vo.txt", "-ac", "1", "-c:a", "libopus", "-b:a", "24k", "-application", "voip", OUT + "/files/voice.ogg"], check=True, timeout=300)
    done(True, {"path": f"jobs/{os.environ.get('CID', '')}/files/voice.ogg", "bytes": os.path.getsize(OUT + "/files/voice.ogg")})

try:
    {"subs": subs, "yt_search": yt_search, "notebook": notebook, "yt_captions": yt_captions, "lrc": lrc, "gutenberg": gutenberg, "tts": tts}[job.get("action")]()
except SystemExit: raise
except Exception as e:
    done(False, error=f"{type(e).__name__}: {str(e)[:200]}")
