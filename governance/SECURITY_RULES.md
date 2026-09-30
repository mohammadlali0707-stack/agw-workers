> **Moved here from `Control-Room` (`Team/SECURITY_RULES.md`, commit `b82fe10`) on 2026-09-30, owner's instruction: Control-Room is frozen and superseded by `agw-workers`.** Kept verbatim as the governance record; where it says "this repo" it meant Control-Room. Account/host facts are as of 2026-09-18 -- verify before relying on them.

# GitHub Actions + AI Agent Security Rules

Security rules learned from real token leakage incidents. These apply to
ANY AI agent (AGY, Claude Code, Gemini, etc.) working with GitHub Actions workflows.

## 1. Never Use Inline Expression for Agent Prompts (Bash Double-Expansion)

WRONG:
  run: |
    PROMPT="${{ inputs.prompt }}"

GitHub substitutes inputs.prompt inline first, then bash expands any ${VAR}
inside it. If that variable is set in the environment, its real value leaks
into the prompt text.

CORRECT:
  env:
    PROMPT_INPUT: ${{ inputs.prompt }}
  run: |
    PROMPT="$PROMPT_INPUT"   # bash does NOT double-expand variable contents

## 2. Never Put a Token Inside an Agent Prompt

If an agent needs repo access:
- Add the token to env: directly from secrets
- Clone the repo in a SEPARATE step before running the agent
- The agent receives an already-cloned repo, not a clone command with a token

## 3. Never Write a Secret to Logs, Files, or Artifacts

WRONG:
  echo "TOKEN=${{ secrets.MY_PAT }}"
  VALUE=${{ secrets.TOKEN }}

GitHub Actions masks secrets in stdout only. Artifacts, files, and
AI agent prompt/output have NO such protection.

## 4. Secret Rotation Procedure

When a token leaks:
1. Immediately regenerate it in GitHub Settings → Personal Access Tokens
2. Update it in ALL repos where it is stored as a secret (can be 20+ repos)
3. Use tweetsodium encryption + PUT /repos/{owner}/{repo}/actions/secrets
4. Delete related logs and artifacts that may contain the exposed value

## 5. Multi-Account Dispatch -- Correct acc_index

CORRECT:   --field "acc_index=$(( ACC_IDX + 1 ))"   # 1-indexed
WRONG:     --field "acc_index=0"                      # always random

## 6. Updating Secrets Across Multiple Repos

Always use an automated script (tweetsodium + GitHub API), never manually.
Especially required when the same secret exists in 9+ repos.

## 7. Branch Check Gate (check_working_branch)

Add this gate to every repo where an AI agent commits:
- Use ##TBS## status markers to distinguish "unmeasured" from "fail"
- Detached HEAD must report "unmeasured", not "fail"
- run_gates.py must call digest_status(output), not just check exit code