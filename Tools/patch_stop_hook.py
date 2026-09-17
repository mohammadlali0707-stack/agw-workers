#!/usr/bin/env python3
"""Re-point the launcher's stop hook at the repository's DEFAULT branch.

WHY THIS EXISTS
---------------
The CCR launcher writes `~/.claude/stop-hook-git-check.sh` and
`~/.claude/launcher-settings.json` fresh into every container. That script
decides "is this session's work delivered?" by measuring
`origin/$current_branch..HEAD` -- the remote branch with the same name as the
local one.

This fleet does not work that way. Per the owner's 2026-09-14 instruction,
made uniform across all five repos on 2026-09-17, every repo is committed to
straight on its default branch: no feature branch, no PR. The session branch
(`claude/<name>`) is a stale ref nobody pushes to, so the launcher's check
reported work that HAD been pushed to main as unpushed, once per session,
every session.

Measuring against the default branch is also the STRICTER check, deliberately.
The weaker alternative -- "contained in any remote ref" -- would call a commit
delivered while it sits on a branch that was never merged. That is the failure
this fleet already paid for once: a fix to `agy-issue-bot.yml` sat inert on a
branch because GitHub only evaluates issue/issue_comment triggers from a
repo's actual default branch.

WHY A PATCH AND NOT A REPLACEMENT
---------------------------------
The launcher's script also contains a commit-signature check (commits whose
committer email is not noreply@anthropic.com, or that carry no gpgsig header,
show as "Unverified" on GitHub). That block is worth keeping and worth
receiving upstream updates to. So this rewrites ONLY the upstream-resolution
block and leaves everything else untouched. If the anchor text is ever gone --
because the launcher's script changed -- this does nothing, says so, and exits
0 rather than guessing or blocking the session.

Idempotent: re-running is a no-op once the marker is present.
"""
import json
import os
import pathlib
import sys

HOOK = pathlib.Path(os.path.expanduser("~/.claude/stop-hook-git-check.sh"))
MARKER = "# [tbs] delivery measured against the default branch"

ANCHOR = """  if git rev-parse "origin/$current_branch" >/dev/null 2>&1; then
    upstream="origin/$current_branch"
  else
    upstream="origin/HEAD"
  fi
"""

REPLACEMENT = '''  if git rev-parse "origin/$current_branch" >/dev/null 2>&1; then
    signing_upstream="origin/$current_branch"
  else
    signing_upstream="origin/HEAD"
  fi

''' + MARKER + """ (Tools/patch_stop_hook.py).
  # This fleet pushes straight to the default branch, so a same-named remote
  # branch is not evidence of delivery. See that file's docstring.
  default_branch=""
  if ref=$(git symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null); then
    default_branch="$ref"
  fi

  # origin/HEAD is not always the real default branch: it reflects whatever
  # the remote's HEAD was at clone time, so a container that clones with HEAD
  # already on the agent's session branch resolves it to origin/claude/<name>
  # -- the very ref this patch exists to stop trusting. Measured 2026-09-17:
  # cloning from a local repo checked out on claude/gracious-wozniak-jjwprj
  # produced exactly that. Treat an agent-branch default as unset.
  case "$default_branch" in
    origin/claude/*) default_branch="" ;;
  esac

  if [[ -z "$default_branch" ]]; then
    for cand in origin/main origin/master; do
      if git rev-parse -q --verify "$cand" >/dev/null 2>&1; then
        default_branch="$cand"
        break
      fi
    done
  fi

  if [[ -n "$default_branch" ]]; then
    upstream="$default_branch"
  else
    upstream="$signing_upstream"
  fi
"""

MSG_ANCHOR = '''    if [[ "$upstream" == "origin/$current_branch" ]]; then
      echo "There are $unpushed unpushed commit(s) on branch '$current_branch'. Please push these changes to the remote repository." >&2
    else'''

MSG_REPLACEMENT = '''    if [[ "$upstream" == "$default_branch" ]]; then
      echo "There are $unpushed commit(s) on branch '$current_branch' not yet contained in $upstream. Push them to the default branch: git push origin HEAD:${upstream#origin/}" >&2
      echo "(If they were already pushed elsewhere, they are still undelivered: only the default branch is evaluated for workflow triggers.)" >&2
    elif [[ "$upstream" == "origin/$current_branch" ]]; then
      echo "There are $unpushed unpushed commit(s) on branch '$current_branch'. Please push these changes to the remote repository." >&2
    else'''


def digest(status, **data):
    print("##TBS##" + json.dumps(
        {"data": data, "probe": "patch_stop_hook", "status": status, "v": 1},
        sort_keys=True))


def main():
    if not HOOK.exists():
        print(f"stop hook not present at {HOOK}; nothing to patch")
        digest("pass", patched=False, reason="hook_absent")
        return 0

    text = HOOK.read_text(encoding="utf-8")

    if MARKER in text:
        digest("pass", patched=False, reason="already_patched")
        return 0

    missing = [name for name, anchor in
               (("upstream_resolution", ANCHOR), ("message_block", MSG_ANCHOR))
               if anchor not in text]
    if missing:
        # The launcher's script changed shape. Do not guess: a wrong patch to
        # a delivery gate is worse than an unpatched one.
        print(f"WARNING: stop hook anchors not found ({', '.join(missing)}); "
              f"left unpatched. Re-derive the patch against the new script.",
              file=sys.stderr)
        digest("fail", patched=False, reason="anchor_missing", missing=missing)
        return 0

    patched = text.replace(ANCHOR, REPLACEMENT).replace(MSG_ANCHOR, MSG_REPLACEMENT)
    HOOK.write_text(patched, encoding="utf-8")
    HOOK.chmod(0o755)
    print("stop hook re-pointed at the repository default branch")
    digest("pass", patched=True, reason="applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
