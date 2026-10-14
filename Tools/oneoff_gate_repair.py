#!/usr/bin/env python3
"""One-off repair of two red gates from Reports/morning/2026-10-14.md.

Runs inside a full CCP checkout (CCP_DIR). Never fabricates an artifact:
  * an orphan ##TBS## line (no tracked artifact carries it) is DEMOTED to
    ##TBS-UNVERIFIED## with a note under it; the rest of the report is kept.
  * a report left with no ##TBS## line gets a "Provenance repair" section
    quoting a digest from a REAL run captured to Reports/provenance_repair/.
Then runs team_lint, check_digest_provenance and run_gates, commits, records
the changelog, pushes, and posts the outputs to the public issue.
"""
import os, re, subprocess, sys, json, datetime

CCP = os.path.abspath(os.environ.get("CCP_DIR", "ccp"))
MARK = "##TBS##"
DEMOTED = "##TBS-UNVERIFIED##"
NOTE = ("> provenance (2026-10-14): `python3 Tools/check_digest_provenance.py` "
        "found no tracked artifact carrying the line above, so its marker "
        "was demoted -- self-asserted, its numbers are unverified. Not "
        "back-filled: an artifact for a run nobody witnessed would be fabrication.")
ORPHAN_REPORTS = ["finish58_acc5", "fleet58lock_acc1", "fleet58lock_acc5",
                  "hold58_acc7", "keep58_acc1", "keep58_acc7", "post58_acc7",
                  "proveloop_acc4", "proveloop_acc5", "proveloop_acc7"]
LINT_REPORTS = ["fleet58lock_acc5", "godot3d_kaveh_export_delivery",
                "queue_budget_acc2"]
LOG = []


def sh(cmd, check=True, cwd=CCP):
    p = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str),
                       capture_output=True, text=True)
    if check and p.returncode:
        raise SystemExit("FAILED %s\n%s\n%s" % (cmd, p.stdout, p.stderr))
    return p


def rpath(name):
    return os.path.join(CCP, "Team", "reports", name + ".md")


def capture_real_digest(name, cmd, art_rel):
    """Run a real tool, tee it to a tracked file, return its ##TBS## line."""
    p = sh(cmd, check=False)
    out = p.stdout + p.stderr
    os.makedirs(os.path.dirname(os.path.join(CCP, art_rel)), exist_ok=True)
    with open(os.path.join(CCP, art_rel), "w") as f:
        f.write("$ " + cmd + "\n" + out + ("" if out.endswith("\n") else "\n")
                + "rc=%d\n" % p.returncode)
    sh(["git", "add", art_rel])
    lines = [l.strip() for l in out.splitlines() if l.strip().startswith(MARK)]
    if not lines:
        raise SystemExit("no digest from " + cmd)
    return lines[-1], p.returncode


def add_repair_section(name, extra=""):
    path = rpath(name)
    text = open(path).read()
    art = "Reports/provenance_repair/%s.txt" % name
    cmd = "python3 Tools/check_review_guild.py Team/reports/%s.md" % name
    line, rc = capture_real_digest(name, cmd, art)
    text = text.rstrip("\n") + "\n\n## Provenance repair 2026-10-14\n\n" + extra + (
        "Machine-readable line from a real run, captured byte-identical to "
        "`%s` (rc=%d):\n\n    %s\n\n    %s\n" % (art, rc, cmd, line))
    open(path, "w").write(text)
    LOG.append("%s: repair section, digest from `%s` (rc=%d)" % (name, cmd, rc))


def main():
    sys.path.insert(0, os.path.join(CCP, "Tools"))
    import check_digest_provenance as cdp
    artifacts = cdp.artifact_digests(CCP)

    # 1. demote orphans
    for name in ORPHAN_REPORTS:
        path = rpath(name)
        lines = open(path).read().split("\n")
        out, demoted, kept = [], 0, 0
        for ln in lines:
            m = cdp.DIGEST.search(ln)
            if m and m.group(0).strip() not in artifacts:
                out.append(ln.replace(MARK, DEMOTED, 1))
                out.append(NOTE)
                demoted += 1
                continue
            if m:
                kept += 1
            out.append(ln)
        open(path, "w").write("\n".join(out))
        LOG.append("%s: demoted %d orphan line(s), kept %d verified" % (name, demoted, kept))
        if kept == 0 and name not in LINT_REPORTS:
            add_repair_section(name)

    # 2. team_lint fixes
    # fleet58lock_acc5: the numbers had no command shown
    path = rpath("fleet58lock_acc5")
    t = open(path).read()
    t = t.rstrip("\n") + (
        "\n\n## Commands behind the numbers (added 2026-10-14)\n\n"
        "The `review_guild` digest quoted above came from "
        "`python3 Tools/check_review_guild.py Team/reports/fleet58lock_acc5.md`, "
        "run before this file existed -- hence its read error. The byte counts, "
        "SHA-256 values and section numbers (1, 8.4) were typed without any "
        "command; treat them as unverified.\n")
    open(path, "w").write(t)
    add_repair_section("fleet58lock_acc5")

    # godot3d_kaveh_export_delivery: two missing sections + digest
    path = rpath("godot3d_kaveh_export_delivery")
    t = open(path).read().rstrip("\n")
    t += ("\n\n## What would have falsified this\n\n"
          "- `godot --headless --export-release` failing, or `apksigner verify` "
          "rejecting either APK.\n"
          "- `zz_autotest.gd` under Xvfb crashing, or `emulator_result.json` "
          "reporting `crashes_detected: true`.\n"
          "- `res://models/kaveh.glb` failing to load so the player falls back "
          "to the capsule or to `Soldier.glb`.\n"
          "- Any binary (APK, GLB, image) appearing in `git ls-files`.\n"
          "\n## What was not done\n\n"
          "- No install on a physical phone; device evidence came later from the "
          "CI emulator job, not from this report.\n"
          "- The APKs, GLBs and screenshots were not committed (by rule), so the "
          "sizes quoted above cannot be re-checked from git alone.\n"
          "- The original report carried no machine-readable line; nothing "
          "was back-filled for the runs it describes.\n")
    open(path, "w").write(t + "\n")
    add_repair_section("godot3d_kaveh_export_delivery")

    # queue_budget_acc2: quote the real digest already captured in run.txt
    path = rpath("queue_budget_acc2")
    run_rel = "Reports/queue_budget/run.txt"
    real = []
    if os.path.isfile(os.path.join(CCP, run_rel)):
        real = [m.group(0).strip() for m in
                cdp.EMITTED.finditer(open(os.path.join(CCP, run_rel)).read())]
    if real:
        t = open(path).read().rstrip("\n")
        t += ("\n\n## Provenance repair 2026-10-14\n\n"
              "Machine-readable line(s) already captured in `%s` by "
              "`python3 Tools/check_queue_budget.py` (quoted byte-identical):\n\n"
              % run_rel) + "".join("    %s\n" % l for l in real)
        open(path, "w").write(t + "\n")
        LOG.append("queue_budget_acc2: quoted %d digest(s) from %s" % (len(real), run_rel))
    else:
        add_repair_section("queue_budget_acc2")

    # 3. measure
    res = {}
    for cmd in ["python3 Tools/team_lint.py", "python3 Tools/check_digest_provenance.py"]:
        p = sh(cmd, check=False)
        tail = "\n".join((p.stdout + p.stderr).strip().splitlines()[-6:])
        res[cmd] = (p.returncode, tail)
    if any(rc for rc, _ in res.values()):
        report(res, None, None, ok=False)
        raise SystemExit(1)

    # 4. commit, changelog, then gates
    sh(["git", "add", "-A", "Team/reports", "Reports/provenance_repair"])
    sh(["git", "commit", "-q", "-m",
        "gates: demote orphan digest lines in 10 reports + fix 3 team_lint rejections (no artifacts back-filled)"])
    h = sh(["git", "rev-parse", "--short=8", "HEAD"]).stdout.strip()
    cl = os.path.join(CCP, "Team", "CHANGELOG.md")
    s = open(cl).read()
    anchor = "retroactively. Everything after it is.\n"
    dup = ("- `d159ee89` -- `Team/RULES.md`, `Tools/probe_check_task_quality.py`: "
           "regenerate Team/RULES.md via rules_index --write and update "
           "probe_check_task_quality to find task 041 in pending/ or done/.\n")
    s = s.replace(dup, "", 1)  # duplicate auto-recorded line; the full entry stays
    entry = ("- `%s` -- `Team/reports/{%s}.md`, `Reports/provenance_repair/`: "
             "red gates from `Reports/morning/2026-10-14.md`. `check_digest_provenance`: "
             "every quoted ##TBS## line that no tracked artifact carries was demoted "
             "to `##TBS-UNVERIFIED##` with a note under it (self-asserted, unverified); "
             "nothing was back-filled and GRANDFATHERED was not extended. Reports "
             "left without a digest got a `Provenance repair 2026-10-14` section "
             "quoting a real `python3 Tools/check_review_guild.py <report>` run "
             "captured to `Reports/provenance_repair/`. `team_lint`: "
             "`fleet58lock_acc5` names the command behind its digest and flags the "
             "rest as unverified; `godot3d_kaveh_export_delivery` gained "
             "falsification / not-done sections; `queue_budget_acc2` quotes the "
             "digest already in `Reports/queue_budget/run.txt`. After: team_lint "
             "rc=%d, check_digest_provenance rc=%d. Gate logic unchanged.\n" % (h, ",".join(sorted(set(ORPHAN_REPORTS + LINT_REPORTS))),
                               res["python3 Tools/team_lint.py"][0],
                               res["python3 Tools/check_digest_provenance.py"][0]))
    s = s.replace(anchor, anchor + entry, 1)
    open(cl, "w").write(s)
    sh(["git", "add", "Team/CHANGELOG.md"])
    sh(["git", "commit", "-q", "-m", "changelog: record " + h])
    for i in range(4):
        if sh("git pull -q --rebase && git push -q", check=False).returncode == 0:
            break
    else:
        raise SystemExit("push failed")
    p = sh("GIT_TERMINAL_PROMPT=0 timeout 1800 python3 Tools/run_gates.py", check=False)
    gates = [l for l in (p.stdout + p.stderr).splitlines() if " gates: " in l][-1:] or ["(no summary)"]
    res["python3 Tools/run_gates.py"] = (p.returncode, gates[0])
    report(res, h, sh(["git", "rev-parse", "--short=8", "HEAD"]).stdout.strip(), ok=True)


def report(res, h, ch, ok):
    body = ["Owner-side repair run (worker ACC1 never reported).", ""]
    if h:
        body.append("CCP commit `%s` + changelog `%s`." % (h, ch))
    body += ["", "Actions:"] + ["- " + l for l in LOG] + ["", "Measured:"]
    for cmd, (rc, tail) in res.items():
        body += ["", "`%s` rc=%d" % (cmd, rc), "```", tail, "```"]
    if not ok:
        body += ["", "Not committed: a gate still fails."]
    open(os.environ.get("BODY_FILE", "/tmp/body.md"), "w").write("\n".join(body))


if __name__ == "__main__":
    main()
