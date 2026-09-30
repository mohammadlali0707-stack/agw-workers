> **Moved here from `Control-Room` (`Team/COMPANY_SCOPE.md`, commit `b82fe10`) on 2026-09-30, owner's instruction: Control-Room is frozen and superseded by `agw-workers`.** Kept verbatim as the governance record; where it says "this repo" it meant Control-Room. Account/host facts are as of 2026-09-18 -- verify before relying on them.

# Company Scope — who does what, across all 9 accounts

**Why this file exists.** Before any issue can be routed by topic, the topics
themselves have to be named: which account hosts which project, and which
account may NOT host project work at all. This file is the human-readable
account-scope reference; the topic router itself reads the equivalent facts
from `Team/company_scope.json`, its machine-readable twin.
`Tools/check_company_scope.py` mechanically checks the two agree (every
account/repo pair named in the table below also appears in the JSON, and
vice versa) so this file cannot drift from what the router actually does.
Change both files together, in the same commit.

## The rule, stated once

**2026-09-13 correction: ACC6 (`mohammadlali0707-stack`, this account) is
now the org's Control-Room, replacing ACC0.** See "What changed 2026-09-13"
below for the evidence this rests on. Unlike ACC0's old arrangement, ACC6
is **not** management-only: it hosts Control-Room (management) *and*
TEHRAN: BLIND SPOT (`Claud-Cloud-Project`, a real product) side by side.
The old "one account, management-only" invariant is retired along with
ACC0's role, not reassigned — no account in this org is exclusively
management-only anymore.

ACC6's repos doing substantive work are `Control-Room` itself (this repo),
`Claud-Cloud-Project` (TEHRAN: BLIND SPOT — unchanged, see the routing
table), `status-dashboard` (moved here from ACC0 alongside Control-Room —
the org-wide status feed/dashboard site at `status.airboxvip.top`), and its
own `agw-workers` runner fleet, which executes Control-Room's own tasks
(and `status-dashboard`'s AGY-output writes — see `Team/CHANGELOG.md`).

**Every other project has exactly one canonical host repo.** A project's
code, issues, and history live in ONE place. An account can additionally
serve as a compute worker for TBS's own internal domain ring (see below)
without that making it TBS's host — hosting and working are different roles,
and only one account holds the host role per project.

## Routing table

| Account | GitHub login | Nickname | Hosts (canonical repo) | Also works TBS ring domain |
|---|---|---|---|---|
| ACC0 | `Mohammadlali` | 1997 | — (no project hosted here as of 2026-09-13; see "What changed 2026-09-13") | — (left the ring permanently, 2026-09-10) |
| ACC1 | `momonakikugava-pixel` | Momona | `AirboxVIP_Coffeenet` | D — Verification and Integration/CI (primary) |
| ACC2 | `lali94m-max` | 94 | — (no project of its own yet) | I — Tools and audit (primary), D (secondary) |
| ACC3 | `ngocgminh5-debug` | ngocg | — (no project of its own yet) | A — Visual Assets and AI Generation (primary), I (secondary) |
| ACC4 | `hmmletssee7-design` | lets | — (no project of its own yet) | B — VN Engine and Flow (primary), A (secondary) |
| ACC5 | `kidding602` | Just | — (no project of its own yet) | C — Persian UI and Typography QA (primary), O (secondary) |
| ACC6 | `mohammadlali0707-stack` | 07 | **Control-Room** (this repo, moved here 2026-09-13) + **`status-dashboard`** (org status feed, moved here 2026-09-13) + **`Claud-Cloud-Project`** — the ONLY home of TEHRAN: BLIND SPOT (TBS), as of 2026-09-11 | O — Operations and Fleet Orchestration (primary), B (secondary) |
| ACC7 | `mohammad97okk` | M2 | — (no project of its own yet) | E — Android Build and Packaging (primary), C (secondary) |
| ACC8 | `moradzahra85-png` | zahra | — (no project of its own yet) | F — Narrative and Audio (primary), E (secondary) |

The "TBS ring domain" column is `Claud-Cloud-Project`'s OWN internal
dispatch, defined in that repo's `Team/org.json`. Control-Room's router
does not re-implement it: an issue identified as TBS-topic is forwarded
whole to ACC6's `Claud-Cloud-Project`, which does its own domain-ring
dispatch from there via its own `agy-issue-bot.yml`.

## What changed 2026-09-11, and why

- **TBS's host moved from ACC0 to ACC6 exclusively.** Until this date, a
  second live copy of `Claud-Cloud-Project` existed on ACC0
  (`Mohammadlali/Claud-Cloud-Project` — the very repo this Control-Room's
  sibling session runs in) and TBS development happened there directly. The
  owner ended that: ACC0 does management only, full stop. Any TBS change
  still made on ACC0's copy (because a live session is already there) must
  be merged into ACC6's copy immediately, not left to diverge — ACC0's copy
  is retiring, not authoritative.
- **The `Mohammadlali0707-stack/Control-Room` repo (a second Control-Room,
  on ACC6) is retired** — this repo, on ACC0, is the one and only Control-Room
  from now on. See `Team/CHANGELOG.md` for the deletion attempt and its
  outcome.
- Accounts ACC2-ACC5, ACC7, ACC8 currently host no project of their own —
  their only role today is as TBS ring workers. That is a fact about today,
  not a rule: if the owner starts a new project, its host account is
  whichever one the owner names, recorded here with a CHANGELOG line, same
  as the AirboxVIP/TBS assignments were.

## What changed 2026-09-12

- **`status-dashboard` added as a routable project**, hosted on ACC0
  alongside Control-Room (see the ACC0 row above and the rule text at the
  top of this file). It was created 2026-09-07/08 (Tasks in
  `Team/CHANGELOG.md`) but was never added to this file or
  `Team/company_scope.json` until now -- a real gap: any `@agy` question
  specifically about the dashboard site had nowhere correct to route to
  and would have fallen through to `control-room` by default (harmless,
  since both are ACC0-hosted and worked by the same fleet, but not
  correctly attributed).
- **Owner temporarily made `Control-Room` and `status-dashboard` PUBLIC**
  (ACC0 hit its GitHub Actions minutes quota for private repos). Plan: keep
  public for roughly three weeks until the quota resets, then make both
  private again -- see `Team/WHERE_WE_ARE.md` for the live status and risk
  assessment. `AirboxVIP_Coffeenet` and `Claud-Cloud-Project` (TBS, on
  ACC6) remain private throughout; only ACC0's own two repos are affected.
  All 9 accounts' `agw-workers` repos are PUBLIC on purpose (unlimited
  Actions minutes, no project code lives there) and this is unrelated to
  the temporary condition above.
- **Fixed a real, live bug in the fleet-dispatch path**: `control-agy.yml`
  referenced `ACC0_PAT`..`ACC8_PAT` (a naming convention that belongs to
  `status-dashboard`'s own secrets) when this repo's actual secrets are the
  bare `ACC0`..`ACC8`. Every dispatch to the agw-workers fleet was silently
  failing until this was caught and fixed live (see `Team/CHANGELOG.md`,
  commit `0cdb146`) -- if you are debugging "the bot never replies," check
  this is still fixed before assuming something else broke it.

## What changed 2026-09-13 — Control-Room's host moved from ACC0 to ACC6

**Owner-confirmed: ACC6 (`mohammadlali0707-stack`) is now the org's one
Control-Room, replacing ACC0.** This was caught as a live discrepancy
between this file and the actual git/CI state, not taken on the owner's
word alone — three independent checks agreed before the owner was asked to
confirm it:

1. This repo's own git remote is `mohammadlali0707-stack/Control-Room`, not
   `Mohammadlali/Control-Room` — the account this file itself, until this
   edit, said was the only real one.
2. `Mohammadlali/control-room` (ACC0) and `mohammadlali0707-stack/
   Control-Room` (ACC6) are both live, separate GitHub repos and share the
   exact same HEAD commit (`de0143e6`, 2026-09-13) — same for
   `status-dashboard` (`67d6064`, 2026-09-13). Neither is stale, so ACC0 is
   being kept in sync FROM ACC6, the reverse of what the 2026-09-11 entry
   above assumed.
3. `agw-workers`' own `agw-worker.yml` (the fleet worker every dispatch
   actually runs) has no checkout/push case arm for `Mohammadlali/
   Control-Room` at all — only `mohammadlali0707-stack/Control-Room` maps
   to a real token (`ACC6_PAT`). `control-agy.yml` was still dispatching
   with `target_repo=Mohammadlali/Control-Room`, a target the worker fleet
   no longer recognized (it would hit "Unknown target_repo -- not
   pushing.") — fixed in the same change; see `Team/CHANGELOG.md`.

**`status-dashboard` moves with it**, on the same identical-HEAD evidence —
it was already described as coupled to Control-Room's own management
function, not a separate product.

**What did NOT change:** TBS's host (`Claud-Cloud-Project` on ACC6) was
already correct. AirboxVIP's host (ACC1) was not part of what the owner
confirmed and is left as-is here. The ring-worker domain assignments
(ACC1-ACC8's TBS ring roles) are unaffected.

## Two hard rules the router enforces

1. **No project work is ever dispatched to ACC0's own `agw-workers`.**
   Anything ACC0's fleet runs must resolve to a Control-Room task, never a
   forwarded TBS or AirboxVIP task — the account-scope rule above is a
   platform invariant, not a preference to route around when convenient.
2. **An issue with no determinable topic is never guess-routed.** The router
   posts back asking for a `project:` label rather than picking a target on
   a coin flip — a wrong forward is a wasted worker run on the wrong
   account's quota, and worse, gives that account's `@agy` context about a
   project it may not otherwise touch.
