#!/usr/bin/env python3
import glob
import os
import subprocess
import sys


def run(cmd):
    print("+", " ".join(cmd), flush=True)
    subprocess.check_call(cmd)


def main():
    clips = sorted(
        path
        for path in glob.glob("clips/*")
        if os.path.isfile(path) and os.path.getsize(path) > 0
    )
    if not clips:
        print("No clips found in clips/", file=sys.stderr)
        sys.exit(1)
    os.makedirs("out", exist_ok=True)
    list_path = os.path.join("clips", "list.txt")
    with open(list_path, "w", encoding="utf-8") as handle:
        for path in clips:
            handle.write("file '" + os.path.abspath(path) + "'\n")
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_path,
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "out/final.mp4",
        ]
    )


if __name__ == "__main__":
    main()
