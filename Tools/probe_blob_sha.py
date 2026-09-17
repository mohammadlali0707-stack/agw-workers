#!/usr/bin/env python3
"""Check sync_repos_to_notion.blob_sha() against git's own hash-object.

blob_sha() is what decides whether a file is re-uploaded to Notion or skipped.
If it disagreed with git, the mirror would either churn every file on every run
or -- much worse -- go permanently stale on a file it believes unchanged. The
failure is silent either way, so it gets a probe.

Includes a deliberate negative control: a byte appended to the content must
change the hash. A function that returned a constant would pass a
"matches itself" test and fail this one.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "sync", os.path.join(HERE, "sync_repos_to_notion.py"))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def git_hash(path):
    return subprocess.run(["git", "hash-object", path],
                          capture_output=True, text=True, check=True).stdout.strip()


def main():
    checked, mismatched = 0, []

    # Real files from this repo, plus the shapes a repo walk hits and a naive
    # implementation gets wrong: empty, no trailing newline, UTF-8, binary.
    targets = []
    for dirpath, dirnames, filenames in os.walk(os.path.dirname(HERE)):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
        for fn in filenames:
            targets.append(os.path.join(dirpath, fn))
        if len(targets) > 120:
            break

    tmp = tempfile.mkdtemp()
    for name, payload in [("empty", b""),
                          ("no_newline", b"abc"),
                          ("utf8", "سلام دنیا\n".encode("utf-8")),
                          ("binary", bytes(range(256))),
                          ("crlf", b"a\r\nb\r\n")]:
        p = os.path.join(tmp, name)
        with open(p, "wb") as fh:
            fh.write(payload)
        targets.append(p)

    for path in targets:
        if not os.path.isfile(path) or os.path.islink(path):
            continue
        ours, theirs = sync.blob_sha(path), git_hash(path)
        checked += 1
        if ours != theirs:
            mismatched.append((path, ours, theirs))

    # Negative control: the probe must be able to fail.
    ctl = os.path.join(tmp, "ctl")
    with open(ctl, "wb") as fh:
        fh.write(b"one")
    a = sync.blob_sha(ctl)
    with open(ctl, "wb") as fh:
        fh.write(b"two")
    b = sync.blob_sha(ctl)
    discriminates = a != b

    for path, ours, theirs in mismatched[:5]:
        print(f"MISMATCH {path}\n  ours={ours}\n  git ={theirs}", file=sys.stderr)
    if not discriminates:
        print("NEGATIVE CONTROL FAILED: different content hashed the same",
              file=sys.stderr)

    ok = not mismatched and discriminates and checked > 20
    print(f"{checked} file(s) checked, {len(mismatched)} mismatched, "
          f"discriminates={discriminates}")
    print('##TBS##' + json.dumps(
        {"data": {"checked": checked, "mismatched": len(mismatched),
                  "discriminates": discriminates},
         "probe": "blob_sha", "status": "pass" if ok else "fail", "v": 1},
        sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
