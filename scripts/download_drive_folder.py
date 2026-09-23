#!/usr/bin/env python3
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}


def die(msg: str) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(1)


def folder_id(url: str) -> str:
    url = (url or "").strip()
    match = re.search(r"/folders/([a-zA-Z0-9_-]+)", url)
    if match:
        return match.group(1)
    match = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url)
    if match:
        return match.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_-]+", url):
        return url
    die("Could not parse Drive folder id from: " + url)
    return ""


def make_opener():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def fetch(opener, url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with opener.open(req, timeout=180) as resp:
        return resp.read(), (resp.headers.get("Content-Type") or ""), resp.geturl()


def list_embedded(opener, fid: str):
    body, _, _ = fetch(
        opener, "https://drive.google.com/embeddedfolderview?id=" + fid
    )
    text = body.decode("utf-8", "replace")
    ids = re.findall(r'id="entry-([^"]+)"', text)
    titles = re.findall(r'class="flip-entry-title">([^<]+)', text)
    files = [
        (title.strip(), file_id)
        for title, file_id in zip(titles, ids)
        if title.strip()
    ]
    if files:
        return files
    pairs = re.findall(
        r"/file/d/([a-zA-Z0-9_-]+)/[^\"']*\"[^>]*>\s*([^<]{1,120})",
        text,
    )
    return [(name.strip(), file_id) for file_id, name in pairs if name.strip()]


def list_folder_page(opener, fid: str):
    body, _, _ = fetch(
        opener, "https://drive.google.com/drive/folders/" + fid + "?usp=sharing"
    )
    text = body.decode("utf-8", "replace")
    files = []
    for match in re.finditer(
        r'\["([a-zA-Z0-9_-]{20,})",\["([^"\\]+\.(?:mp4|mov|webm|mkv|m4v))"',
        text,
        re.I,
    ):
        files.append((match.group(2), match.group(1)))
    return files


def is_html(body: bytes, ctype: str) -> bool:
    if "text/html" in ctype.lower():
        return True
    head = body[:200].lstrip().lower()
    return head.startswith(b"<!doctype") or head.startswith(b"<html")


def download_id(opener, file_id: str, dest: str) -> None:
    url = (
        "https://drive.google.com/uc?export=download&id="
        + file_id
        + "&confirm=t"
    )
    body, ctype, _ = fetch(opener, url)
    if is_html(body, ctype):
        text = body.decode("utf-8", "replace")
        confirm = re.search(r"confirm=([0-9A-Za-z_-]+)", text)
        uuid = re.search(r'name="uuid"\s+value="([^"]+)"', text)
        query = {"id": file_id, "export": "download"}
        if confirm:
            query["confirm"] = confirm.group(1)
        else:
            query["confirm"] = "t"
        if uuid:
            query["uuid"] = uuid.group(1)
        url = "https://drive.google.com/uc?" + urllib.parse.urlencode(query)
        body, ctype, _ = fetch(opener, url)
    if is_html(body, ctype):
        die(
            "Drive returned a webpage instead of a video for "
            + file_id
            + ". Folder must be Anyone with the link, and files must be videos."
        )
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "wb") as handle:
        handle.write(body)
    if os.path.getsize(dest) < 2048:
        die("Downloaded file too small: " + dest)


def download_http(opener, url: str, dest: str) -> None:
    body, ctype, _ = fetch(opener, url)
    if is_html(body, ctype):
        die("URL returned HTML, not a video: " + url)
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "wb") as handle:
        handle.write(body)
    if os.path.getsize(dest) < 2048:
        die("Downloaded file too small: " + dest)


def sort_key(name: str):
    stem = os.path.splitext(name)[0]
    match = re.fullmatch(r"(\d+)", stem)
    if match:
        return (0, int(match.group(1)))
    match = re.search(r"(\d+)", stem)
    if match:
        return (1, int(match.group(1)), stem.lower())
    return (2, stem.lower())


def looks_like_video(name: str) -> bool:
    ext = os.path.splitext(name)[1].lower()
    if ext in VIDEO_EXT:
        return True
    return ext == "" and bool(re.search(r"\d+", name))


def gdown_folder(url: str):
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "gdown"])
    os.makedirs("drive_dl", exist_ok=True)
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "gdown",
            "--folder",
            "--remaining-ok",
            "-O",
            "drive_dl",
            url,
        ]
    )
    found = []
    for root, _, files in os.walk("drive_dl"):
        for name in files:
            path = os.path.join(root, name)
            if os.path.isfile(path) and os.path.getsize(path) > 2048:
                found.append((name, path))
    return found


def write_ordered(items):
    videos = [(name, source) for name, source in items if looks_like_video(name)]
    if not videos:
        videos = items
    videos.sort(key=lambda item: sort_key(item[0]))
    if not videos:
        die("No files found in the Drive folder.")
    os.makedirs("clips", exist_ok=True)
    opener = make_opener()
    for index, (name, source) in enumerate(videos, start=1):
        dest = os.path.join("clips", f"{index:03d}.bin")
        print("Clip " + str(index) + ": " + name, flush=True)
        if os.path.isfile(source):
            with open(source, "rb") as src, open(dest, "wb") as dst:
                dst.write(src.read())
        else:
            download_id(opener, source, dest)
    print("Saved " + str(len(videos)) + " clips", flush=True)


def download_folder(url: str) -> None:
    fid = folder_id(url)
    opener = make_opener()
    files = list_embedded(opener, fid) or list_folder_page(opener, fid)
    if files:
        write_ordered(files)
        return
    print("Folder page listing failed; trying gdown", flush=True)
    found = gdown_folder(url)
    write_ordered(found)


def download_url_list(raw: str) -> None:
    opener = make_opener()
    os.makedirs("clips", exist_ok=True)
    index = 0
    for line in raw.splitlines():
        url = line.strip().strip("\ufeff")
        if not url:
            continue
        index += 1
        dest = os.path.join("clips", f"{index:03d}.bin")
        print("Downloading " + str(index), flush=True)
        download_http(opener, url, dest)
    if index < 1:
        die("No clip URLs given")


def main() -> None:
    folder = (os.environ.get("FOLDER_URL") or "").strip()
    urls = (os.environ.get("CLIP_URLS") or "").strip()
    if folder:
        download_folder(folder)
        return
    if urls:
        download_url_list(urls)
        return
    die("Need FOLDER_URL or CLIP_URLS")


if __name__ == "__main__":
    main()
