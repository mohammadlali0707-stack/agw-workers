> **Moved here from `Control-Room` (`Team/CROSS_ACCOUNT_LESSONS.md`, commit `b82fe10`) on 2026-09-30, owner's instruction: Control-Room is frozen and superseded by `agw-workers`.** Kept verbatim as the governance record; where it says "this repo" it meant Control-Room. Account/host facts are as of 2026-09-18 -- verify before relying on them.

# Cross-account operating lessons

Ported here 2026-09-11 from `Claud-Cloud-Project/Team/NEW_SESSION_PROMPT.md`,
which used to carry the whole organization's structure even though it is a
single project's (TEHRAN: BLIND SPOT's) own session-boot file. That file now
only orients a session on TBS; anything about running the ORG belongs here.
See `Team/COMPANY_SCOPE.md` for the account/project map itself -- this file
is the operating lessons, not the map.

## How new work reaches an account

Because token cost is watched, work is not done inline in a supervising
session -- it is dispatched. Name a task, get the owner's confirmation in
chat, write the prompt, and open it as an issue in the target account's own
repo with the issue body starting `@agy` -- exactly that string as the
first characters, no leading blank line or other text. That prefix is what
lets the Antigravity bot on that repo pick it up; the actual gate (see any
`*-issue-bot.yml` / `control-agy.yml`) is
`startsWith(issue.body, '@agy') && issue.author_association == 'OWNER'` --
so the issue also has to be CREATED BY that repo's own owner account, not
just mention `@agy` somewhere. **Batch Dispatch** spreads up to 99 tasks at
once, one per runner, across the org's accounts. The other path is naming a
task for the owner to run themselves through the Antigravity IDE with
`@agy` directly, then reviewing what comes back once it is reported.

## Reaching a repo your own git/GitHub access does not cover

A session's direct GitHub access is normally scoped to one repo. The proven
workaround: write a `workflow_dispatch` job IN A REPO YOU DO HAVE that uses
the target account's own `ACC{N}_PAT` as `GH_TOKEN` inside the job step
(never in the session's own git config) and runs `gh`/`git` against that
account's repos from inside Actions -- that job runs AS that account, with
full read/write on everything it owns, because the PAT is that account's
own credential. `Claud-Cloud-Project`'s `inspect-control-room.yml` is a
live, reusable example. Reuse this pattern rather than trying to attach a
repo under a different owner to a session directly -- that has been tested
and refused (an owner-identity lock on the session tooling, not a repo
visibility setting: making both repos public did not change it).

## Default to delegating, not doing

A token-cost policy, not a suggestion. A supervising session should mostly
SUPERVISE, not implement. For any substantive piece of work -- a new tool,
a workflow, a fix, an investigation with real digging -- default to opening
it as an `@agy`-prefixed issue (via the topic router here, or the
`workflow_dispatch`-with-`ACC{N}_PAT` pattern above for a specific account)
or a Batch Dispatch, rather than reading/writing/testing it yourself in the
supervising session. The `@agy` accounts have their own quota and their own
compute; spending it is the point. A supervising session's own tokens are
the scarce resource -- reserve them for: deciding WHAT to delegate and to
WHOM, writing the task prompt, and verifying what comes back (see below for
why verifying still means a real read, not trusting the reply). Small,
mechanical edits confined to files already open in front of you are still
fine to make directly -- the policy is about not doing another account's
substantive work FOR it when that account has its own `@agy` to ask.

## Trust nothing an `@agy` reply claims without a second, independent read

Measured 2026-09-09: two issues dispatched one second apart on two
different repos came back with BYTE-IDENTICAL completion comments -- same
commit hash, same worker run URL, both citing artifacts named after one
issue's own number. At least one of those two reports was wrong about what
it actually did. The fix is never to accept a completion comment's claim
about which repo got which change -- dispatch a small read-only check (own
PAT, `gh api repos/OWNER/REPO/commits` and a direct `contents` lookup for
whatever file was supposedly created) and read the real repo state back.

## Trust nothing an `@agy` reply claims was pushed, either

A second, distinct failure mode, measured the same day: a worker produced a
real, detailed report and even printed a "push line", but its commits never
actually reached the target branch (`git log --all` + `git ls-remote` found
neither hash anywhere). Unlike the duplicate-comment bug above, this one
had no error text at all -- the reply read exactly like a success. The
likely cause was a push conflict with concurrent activity on the same
branch (a plain `git push`, no fetch/rebase/retry on a non-fast-forward
rejection). Verify a claimed push with `git log`/`git ls-remote` on the
actual hash, every time, not just for tasks that came back with an error.

## Morning reports: use `@agy`, not a copied workflow

Any new morning-report setup on another account's repo should go through
that account's own `@agy` bot, asked to build it adapted to what that repo
actually contains, rather than a literal copy of another repo's morning
workflow.
