# agw-workers

This same repo exists, byte-identical in its `.github/workflows/`, on eight
GitHub accounts (`ACC1`..`ACC8`; see the owner mapping below). Each account's
copy runs entirely on its own Actions minutes -- nothing here calls across
accounts except the relay tools in the last section, which only work from
`mohammadlali0707-stack` (`ACC6`), the relay host.

## Three entry points, mutually exclusive by prefix

Every mechanism below is triggered by how a GitHub Issue's body (or, for
delivery, a comment on one) starts. An issue matches at most one of these,
by design -- see `agw-claude.yml`'s condition, which explicitly excludes the
other two prefixes.

### `@agy` -- general coding/agent tasks (Gemini/Antigravity)

Open an Issue starting with `@agy` followed by the task, as the repo owner.
`agy-issue-bot.yml` acks on the issue and dispatches `agw-worker.yml` in this
same repo (no cross-account relay). Each of the 8 GitHub accounts has
**exactly one** Antigravity/Gemini identity (one Gmail) of its own; up to 11
concurrent runner slots within an account (`agw-ACC{N}-w{X}` naming) all
share that same identity.

`agw-worker.yml` resolves its own identity from `GITHUB_REPOSITORY_OWNER`
(same owner<->index table as the relay tools, inverted) and uses it first,
always -- no random pick among accounts. **Only** if that identity's own run
hits a rate-limit/quota-shaped error in `agy`'s output (`too many requests`,
`rate limit`, `429`, `quota`, case-insensitive) does it retry once with a
different identity chosen at random from the *other* 7 -- same c1-then-c2
shape as `agw-claude.yml`'s OAuth failover, for agy instead of Claude. Any
other kind of failure is not retried. Deciding *which of the 8 accounts*
handles a request in the first place is the relay layer's job (see below),
not this file's.

**`ACC9` is retired and never used here** -- not as a default, not as a
manual `acc_index`, not in the random fallback pool. `restore_token` in
`agw-worker.yml` has no case for it at all, deliberately: a run that somehow
still asks for it gets no token and fails loudly on auth, rather than
silently doing something with a stale credential.

`agw-worker.yml` runs the `agy` CLI with the task as its prompt, optionally
checks out and pushes to one of a handful of hardcoded target repos, and
uploads the raw output as a build artifact + Job Summary -- **not** as an
issue comment.

### `@media` -- image/video requests (always human-fulfilled)

Open an Issue starting with `@media` followed by a description of the
image/video needed. `media-request-bot.yml` acks it. Whoever generates the
asset (a person, with whatever AI tool) comments the resulting link
(`http://` or `https://`) on that same issue. The bot extracts the link,
appends `{issue, title, link, delivered_at}` to `media/manifest.jsonl`,
commits it, confirms, and closes the issue. No API keys, no auto-download --
the link itself is the durable record; `media/README.md` has the format.

**This is deliberately never automated end-to-end.** The *opener* can be a
human or `agy` (see below); the *deliverer* -- the one who posts the actual
link -- must always be a real person (`author_association == OWNER`,
checked on the comment, not just the issue).

### `@claude` -- general issue/comment responses (Claude Code)

Mention `@claude` anywhere in an issue or comment, as the owner or a
collaborator. `agw-claude.yml` runs `anthropics/claude-code-action@v1`
against `CLAUDE_CODE_OAUTH_TOKEN`, with automatic failover to
`CLAUDE_CODE_OAUTH_TOKEN2` (and a working-tree cleanup in between) if the
first account errors or is out of quota. Adapted from
`Claud-Cloud-Project`'s `tbs-claude.yml`, trimmed of that repo's own branch
pin and tool allowlist.

## How `agy` asks for media without ever holding a credential

`agy` runs with `--dangerously-skip-permissions` and full shell access, and
its prompt is built from issue/comment text it does not control -- so it
never gets a live GitHub token, not even a narrowly-scoped one. Instead:

1. Its prompt (built in `agy-issue-bot.yml`) instructs it: if the task needs
   an image or video, print a line starting exactly with `##MEDIA_REQUEST##`
   followed by a one-line description, and continue without waiting.
2. After `agy` finishes, a separate step in `agw-worker.yml` -- outside
   `agy`'s own process entirely -- greps its output for that marker. If
   found, **that step**, using this repo's own default `GITHUB_TOKEN`, opens
   the `@media` issue itself.
3. `media-request-bot.yml`'s ack accepts an issue opened by the
   `github-actions[bot]` identity this produces (that login can't be spoofed
   by an outside actor -- it only appears when a workflow in this same repo
   opened it), so the request gets acked exactly as if a human had opened it.
   Delivery still requires a real person, as above.

`agy` itself is never in this loop: it only ever prints plain text.

## Cross-account relay (only usable from `mohammadlali0707-stack`/ACC6)

A cloud coding session's own GitHub access reaches only one owner at a time
-- `add_repo` refuses to attach a repo under a different owner
(`cross-tier adds are not supported`), confirmed live, not assumed. To read
or act on the other 7 accounts from a session opened on ACC6, two workflows
here use each account's own `ACC{N}_PAT` secret (all 9, `ACC0`..`ACC8`,
confirmed present on this repo -- see `check-secrets.yml`):

- **`relay-open-issue.yml`** (`workflow_dispatch`): `open_issue`, `comment`,
  or `view` an Issue on `<owner>/agw-workers` for a chosen account
  (`account_index` 1,2,3,4,5,7,8 -- ACC6 needs no relay, act on it
  directly). `view` exists because even a plain `curl` to another account's
  *public* repo gets 403'd by a session's own proxy; reading another
  account's state has the same access problem as writing to it.
- **`relay-deploy-file.yml`** (`workflow_dispatch`, no inputs): copies a
  fixed file list (currently `media-request-bot.yml`, `agw-claude.yml`,
  `agy-issue-bot.yml`, `agw-worker.yml`, `media/README.md`, this file) from
  this repo to each of the other 7, one runner boot for all seven, skipping
  an account cleanly if its PAT is missing, the clone fails, or the content
  is already identical.

Owner mapping (`ACC1`..`ACC8`, confirmed by the owner directly, not from any
file):

| index | owner |
|---|---|
| 1 | momonakikugava-pixel |
| 2 | lali94m-max |
| 3 | ngocgminh5-debug |
| 4 | hmmletssee7-design |
| 5 | kidding602 |
| 6 | mohammadlali0707-stack -- relay host, run `relay-*.yml` from here |
| 7 | mohammad97okk |
| 8 | moradzahra85-png |

`ACC9` (`stranger77777777`) is not in this table -- retired for good
(Actions disabled there, never recovered), out of every cycle: relay,
`@agy`'s own identity pool, and its quota fallback.

Both relay workflows do almost no work themselves (no checkout in
`relay-open-issue.yml`; one runner boot, not seven, in
`relay-deploy-file.yml`) -- the actual heavy work always runs on the target
account's own Actions, never ACC6's.
