#!/usr/bin/env python3
import glob
import json
import os
import subprocess
import sys

WIDTH = 1920
HEIGHT = 1080
FPS = 24
XFADE = 0.3
CRF = 20
NAVY = "0x0A1628"


def run(cmd):
    print("+", " ".join(cmd), flush=True)
    subprocess.check_call(cmd)


def probe_duration(path):
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            path,
        ],
        text=True,
    )
    return float(json.loads(out)["format"]["duration"])


def has_audio(path):
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            path,
        ],
        text=True,
    ).strip()
    return bool(out)


def normalize(src, dst):
    vf = (
        "scale="
        + str(WIDTH)
        + ":"
        + str(HEIGHT)
        + ":force_original_aspect_ratio=decrease,"
        + "pad="
        + str(WIDTH)
        + ":"
        + str(HEIGHT)
        + ":(ow-iw)/2:(oh-ih)/2:color="
        + NAVY
        + ","
        + "fps="
        + str(FPS)
        + ",setsar=1,format=yuv420p,"
        + "eq=contrast=1.06:saturation=1.08:brightness=0.02"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        src,
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        str(CRF),
        "-pix_fmt",
        "yuv420p",
    ]
    if has_audio(src):
        cmd += [
            "-af",
            "loudnorm=I=-14:TP=-1.5:LRA=11",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            dst,
        ]
        run(cmd)
        return
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        src,
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        str(CRF),
        "-c:a",
        "aac",
        "-shortest",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-movflags",
        "+faststart",
        dst,
    ]
    run(cmd)


def hard_concat(norms, out_path):
    list_path = os.path.join("norm", "list.txt")
    with open(list_path, "w", encoding="utf-8") as handle:
        for path in norms:
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
            out_path,
        ]
    )


def xfade_concat(norms, out_path):
    durs = [probe_duration(p) for p in norms]
    fade = XFADE
    if any(duration <= fade + 0.2 for duration in durs):
        print("A clip is too short for xfade; using hard cuts", flush=True)
        hard_concat(norms, out_path)
        return

    args = ["ffmpeg", "-y"]
    for path in norms:
        args += ["-i", path]

    filters = []
    last_v = "[0:v]"
    last_a = "[0:a]"
    offset = durs[0] - fade
    last_index = len(norms) - 1
    for i in range(1, len(norms)):
        v_out = "[vout]" if i == last_index else "[v" + str(i) + "]"
        a_out = "[aout]" if i == last_index else "[a" + str(i) + "]"
        filters.append(
            last_v
            + "["
            + str(i)
            + ":v]xfade=transition=fadeblack:duration="
            + str(fade)
            + ":offset="
            + "{:.4f}".format(offset)
            + v_out
        )
        filters.append(
            last_a
            + "["
            + str(i)
            + ":a]acrossfade=d="
            + str(fade)
            + a_out
        )
        last_v = v_out
        last_a = a_out
        offset = offset + durs[i] - fade

    total = sum(durs) - fade * last_index
    fade_out_start = max(total - 0.25, 0)
    filters.append(
        "[vout]fade=t=in:st=0:d=0.12,fade=t=out:st="
        + "{:.4f}".format(fade_out_start)
        + ":d=0.25[vfinal]"
    )
    filters.append(
        "[aout]afade=t=in:st=0:d=0.12,afade=t=out:st="
        + "{:.4f}".format(fade_out_start)
        + ":d=0.25,loudnorm=I=-14:TP=-1.5:LRA=11[afinal]"
    )

    args += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[vfinal]",
        "-map",
        "[afinal]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        str(CRF),
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        out_path,
    ]
    run(args)


def main():
    clips = sorted(
        path
        for path in glob.glob("clips/*")
        if os.path.isfile(path) and os.path.getsize(path) > 0
    )
    if not clips:
        print("No clips found in clips/", file=sys.stderr)
        sys.exit(1)
    os.makedirs("norm", exist_ok=True)
    os.makedirs("out", exist_ok=True)
    norms = []
    for i, src in enumerate(clips, start=1):
        dst = os.path.join("norm", "{:03d}.mp4".format(i))
        normalize(src, dst)
        norms.append(dst)
    out_path = "out/final.mp4"
    if len(norms) == 1:
        hard_concat(norms, out_path)
        return
    xfade_concat(norms, out_path)


if __name__ == "__main__":
    main()
