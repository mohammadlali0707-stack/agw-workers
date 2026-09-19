# Notion -> n8n -> GitHub, event-driven

The owner's requirement, 2026-09-19: **"باید event-driven باشند تا تاخیر صفر
شود"** -- a task ticked in Notion should reach GitHub in seconds, not whenever
a cron happens to be scheduled.

This directory holds the n8n half. `notion-task-to-dispatch.json` is an
importable n8n workflow; everything else here is the wiring around it and the
measurements that justify it.

## What the delay actually is, measured

`notion-task-poller.yml` declares `cron: '*/10 * * * *'`. It does not poll
every ten minutes. Measured 2026-09-19 by `Tools/probe_notion_path_attribution.py`
over 11 real runs of that workflow:

| | claimed | measured |
|---|---|---|
| gap between polls | 10 min | **median 188 min, worst 303 min** |

GitHub deprioritises `schedule` under load and there is no setting that
changes it. So the schedule is a floor -- a guarantee the task is *eventually*
picked up -- and cannot be the mechanism for "zero delay". That needs a push,
and `repository_dispatch` is the only push door GitHub offers a workflow.

The push door was then measured rather than assumed. A real dispatch fired at
2026-09-19 08:15:05Z against this repository:

| | |
|---|---|
| dispatch accepted | HTTP 204 |
| dispatch -> run queued | **4.6 s** |
| dispatch -> Notion database actually read | **~12 s** (run 35431496136, log at 08:15:17Z) |

Against a 188-minute median, that is the whole argument for this directory.
The run found `0 task(s) with Plan ticked and no issue yet` and created
nothing, so the door was exercised end to end at zero cost.

**One thing that measurement also settled, and it is why the probe exists:**
a dispatch carrying an `event_type` that NO workflow subscribes to was fired
in the same session and GitHub answered **204 No Content** -- the same status
as the one that worked. GitHub does not validate `event_type` against
anything. A severed chain and a working chain are byte-identical at the HTTP
layer, n8n records both as successful executions, and nothing anywhere
reports the difference. That failure can only be caught by comparing the
strings across the three systems, which is what
`Tools/probe_notion_event_chain.py` does.

## Why n8n is in the path at all, and not cut out of it

Notion's own automations can send a webhook. They cannot send an
`Authorization: Bearer <github-pat>` header to `api.github.com` with a JSON
body GitHub's dispatch API accepts -- and even if a future Notion release
could, pointing Notion straight at GitHub would mean **storing a GitHub push
credential inside the Notion workspace**, where every workspace member with
automation rights can read and re-aim it.

n8n belongs in this path precisely because it holds the credential Notion
should not. Notion's automation carries no secret at all: it POSTs an unsigned
`{source, page_id}` to a public webhook URL. n8n is where that turns into an
authenticated GitHub call. The worst a leaked Notion webhook URL can do is
make the poller run early against a Notion database it was already reading.

That is also why the n8n workflow's second node exists. It checks
`source == "notion"` and routes anything else to a dead-end "Refuse anything
else" node rather than to GitHub. The webhook is public; the dispatch is not.

## The chain, end to end

```
Notion row: Plan checkbox ticked
        |
        |  Notion database automation  (UI only -- see below)
        v
POST https://n8n.airboxvip.top/webhook/notion-task
     {"source": "notion", "page_id": "<uuid>"}
        |
        |  n8n: webhook -> IF source == notion -> HTTP Request
        v
POST https://api.github.com/repos/mohammadlali0707-stack/agw-workers/dispatches
     {"event_type": "notion-task", "client_payload": {...}}
        |
        v
notion-task-poller.yml runs (seconds), queries the Notion DB for rows with
Plan ticked and no `Github Issue URL`, opens the issue, writes the URL back.
```

The cron stays enabled underneath all of this. If n8n is down, the task is
still picked up -- late, but picked up. The two legs share one lock: a
non-empty `Github Issue URL` on the row means "already handled", so a webhook
firing while a scheduled run is in flight costs one duplicate skip and
nothing else.

## Part 1 -- the Notion automation. This CANNOT be created from here.

**Notion's public API has no automations endpoint.** Database automations,
including "send webhook", exist only in the Notion UI; there is no REST route
and no MCP tool that creates one. Checked 2026-09-19 against the full Notion
tool surface available to this session -- `notion-create-database`,
`notion-update-data-source`, `notion-update-view`, `notion-update-page` and
the rest can create and edit the database, its schema and its rows, and none
of them can create an automation on it.

So this part is eleven clicks by a human, once. It is written out exactly
rather than summarised, because "set up a webhook automation" is the kind of
instruction that gets done slightly differently and then debugged for an hour.

1. Open the **Task Board** database in Notion (the one whose id is
   `NOTION_TASKS_DB_ID`).
2. Top right of the database view: the **lightning-bolt** icon (Automations).
3. **New automation**.
4. Name it `Plan ticked -> agw-workers`.
5. Trigger: **Property edited** -> pick the **`Plan`** property.
   (Not "Page added". A row is usually created before Plan is ticked, and a
   page-added trigger would fire on the empty row and deliver nothing.)
6. Add a condition: **`Plan` is checked**. Without it the automation also
   fires when someone *un*-ticks the box.
7. Action: **Send webhook**.
8. URL: `https://n8n.airboxvip.top/webhook/notion-task`
9. Body: switch to the JSON/custom editor and send exactly these two keys --
   ```json
   {"source": "notion", "page_id": "{{page.id}}"}
   ```
   `source` is what n8n's IF node checks; a body without it is refused.
   `page_id` is carried through to GitHub as `client_payload.page_id`, which
   is how a delivery gets attributed later. The poller re-queries Notion for
   ticked rows regardless, so a missing `page_id` degrades attribution, not
   delivery.
10. **Save**, then **turn the automation on** (it is created disabled).
11. Tick `Plan` on any row and watch n8n's Executions tab.

## Part 2 -- the n8n workflow

1. n8n -> **Workflows** -> **Import from File** -> `notion-task-to-dispatch.json`.
2. Open the **GitHub repository_dispatch** node -> Credential for Header Auth
   -> **Create New**:
   - Name: `GitHub PAT (agw-workers dispatch)`
   - Header name: `Authorization`
   - Header value: `Bearer <PAT>`

   The node already sends `Content-Type: application/json` explicitly. Leave
   it: a dispatch POST without that header is rejected with **415**, measured
   2026-09-19, and 415 is not a status anyone reading an n8n execution list
   expects to have to look for.

   The PAT needs **one** scope: `repo` (classic) or **Contents: read-write**
   on `mohammadlali0707-stack/agw-workers` (fine-grained). Nothing else.
   `repository_dispatch` is the only call it ever makes. Do not reuse a
   fleet-wide PAT here; if this one leaks, the blast radius should be "someone
   can make the poller run early".
3. **Activate** the workflow. An inactive n8n workflow answers its test URL
   and not its production URL, which is the single most common way this path
   looks configured and delivers nothing.
4. Note which URL you activated. n8n serves
   `/webhook-test/notion-task` only while the editor is open with "Listen for
   test event" running, and `/webhook/notion-task` once the workflow is
   active. Notion must be pointed at the **`/webhook/`** one.

## Verifying it, without believing anyone's report

Three checks, in the order that isolates a failure fastest:

```bash
# 1. Does the n8n webhook accept and route? (no GitHub involved if the
#    IF node refuses -- an HTTP 200 with "refused" tells you the guard works)
curl -sS -X POST https://n8n.airboxvip.top/webhook/notion-task \
     -H 'Content-Type: application/json' \
     -d '{"source":"notion","page_id":"00000000-0000-0000-0000-000000000000"}'

# 2. Did GitHub receive a dispatch? A run of the poller with
#    event == repository_dispatch, created within seconds of the curl.
python3 Tools/probe_notion_path_attribution.py

# 3. Do the three ends of the chain still agree with each other?
python3 Tools/probe_notion_event_chain.py
```

Check 3 is the one that matters over time. The chain is held together by two
string literals that live in different systems -- the webhook path
(`notion-task`, in Notion's automation and in the n8n JSON) and the dispatch
event type (`notion-task`, in the n8n JSON and in the workflow's `on:`). Both
can be renamed on one side and stay green on the other, delivering nothing
and reporting nothing. `probe_notion_event_chain.py` reads all three ends and
fails when they stop agreeing; this README is one of the ends it reads, so
editing the URL here without editing the JSON is itself a failure.

## What this does not do

- It does not make the ticked task *start work*. It makes the poller run,
  which opens a GitHub issue, which `agy-plan-bot.yml` answers with a plan.
  The plan still needs `@agy-plan-approved` from the owner.
- It cannot be verified from inside this repository. n8n runs on
  `n8n.airboxvip.top` and its execution log is the only place a delivery that
  never reached GitHub is visible. `probe_notion_path_attribution.py` can say
  "no issue is attributable to n8n"; it cannot distinguish "n8n never fired"
  from "n8n fired and the GitHub call failed".
- It does not remove the cron. Deleting the schedule would turn every n8n
  outage into a silently stalled fleet.
