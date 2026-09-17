#!/usr/bin/env python3
"""Check that Gate 1's CHECK-2 still catches the bug it exists for, and no
longer reports valid files.

WHY
---
CHECK-2 guards a real failure: a `python3 -c "` heredoc whose continuation
lines are indented less than the command itself terminates the YAML block
early, which is what produced the name=path registration bug this gate is
named after. But it assumed every `python3 -c "` OPENS such a block. A command
that closes its own quote on the same line opens nothing, so the scan ran to
the end of the file and reported every later line that happened to be less
indented. One valid line in test-agy-chat-live.yml -- untouched since d0972d0 --
produced twelve findings against `fi`, `done` and `sleep 15`, and Gate 1 was
red on every push for work that had nothing to do with it.

A gate that cries wolf on correct files gets ignored, which costs more than the
bug it was guarding. So this probe pins BOTH directions: the real file must be
clean, and a genuinely broken block must still fail.

The function under test is read out of the workflow file itself, so this cannot
drift from what actually runs in CI.
"""
import json
import os
import re
import sys
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "..", ".github", "workflows", "gate-yaml-lint.yml")


def load_check2():
    """Pull check_2_python_inline_yaml_indent out of the gate and compile it."""
    src = open(GATE, encoding="utf-8").read()
    m = re.search(r"\n(\s*)def check_2_python_inline_yaml_indent\(.*?\n\1    return errors\n",
                  src, re.S)
    if not m:
        raise SystemExit("could not find check_2 in gate-yaml-lint.yml -- the "
                         "gate changed shape and this probe must be updated")
    ns = {}
    exec(textwrap.dedent(m.group(0)), ns)      # noqa: S102 -- the repo's own code
    return ns["check_2_python_inline_yaml_indent"]


def main():
    check2 = load_check2()
    fails = []

    def check(name, cond, detail=""):
        print(f"  {'PASS' if cond else 'FAIL'}  {name}{' -- ' + detail if detail else ''}")
        if not cond:
            fails.append(name)

    # 1. The real file that was being wrongly reported.
    real = os.path.join(HERE, "..", ".github", "workflows", "test-agy-chat-live.yml")
    found = check2(real, open(real, encoding="utf-8").read().splitlines(True))
    check("test-agy-chat-live.yml is clean", not found,
          f"{len(found)} finding(s), first: {found[0][1][:70] if found else ''}")

    # 2. Every workflow in the repo: none should report, since none is known bad.
    wf_dir = os.path.join(HERE, "..", ".github", "workflows")
    noisy = {}
    for fn in sorted(os.listdir(wf_dir)):
        if not fn.endswith((".yml", ".yaml")):
            continue
        p = os.path.join(wf_dir, fn)
        f = check2(p, open(p, encoding="utf-8").read().splitlines(True))
        if f:
            noisy[fn] = len(f)
    check("no workflow in the repo reports", not noisy, json.dumps(noisy))

    # 3. NEGATIVE CONTROL: the real bug must still be caught. A multi-line
    #    python3 -c block whose body is indented LESS than the command.
    bad = [
        "      - name: broken\n",
        "        run: |\n",
        "          python3 -c \"\n",
        "      import sys\n",
        "      print('this line left the YAML block')\n",
        "          \"\n",
    ]
    found_bad = check2("<synthetic>", bad)
    check("a genuinely under-indented block IS still caught", bool(found_bad),
          f"{len(found_bad)} finding(s)")

    # 4. A correctly indented multi-line block must not report.
    ok = [
        "      - name: fine\n",
        "        run: |\n",
        "          python3 -c \"\n",
        "          import sys\n",
        "          print('still inside')\n",
        "          \"\n",
    ]
    check("a correctly indented block does not report", not check2("<ok>", ok))

    # 5. A one-liner with nested quotes -- the exact shape that broke it.
    oneliner = [
        "            if [ x ]; then\n",
        "              N=$(python3 -c \"import json; print(json.load(open('/tmp/a.json')))\")\n",
        "            fi\n",
        "          done\n",
    ]
    check("a self-closing one-liner reports nothing",
          not check2("<oneliner>", oneliner),
          str(check2("<oneliner>", oneliner)[:1]))

    ok_all = not fails
    print('##TBS##' + json.dumps(
        {"data": {"failed": len(fails), "names": fails},
         "probe": "gate_yaml_lint_check2",
         "status": "pass" if ok_all else "fail", "v": 1}, sort_keys=True))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
