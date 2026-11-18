# Instagram Reels Publisher — session route (RUNBOOK)

> **Agent-independence:** this file is the whole manual. Any agent with
> access to the repos/server below can operate, debug, and rebuild this
> system without Muse or any prior conversation. If anything here is
> out of date, the code in this repo wins over this file — fix the file.

## What it is

Posts Instagram Reels from the Notion queue **without the Meta Graph API**
(that route was abandoned 2026-11-18: Meta demanded phone verification
that failed). Instead, a GitHub runner logs into **instagram.com as the
user** with a **saved browser session** (cookies + localStorage) and
uploads the Reel through the website's own Create flow — the exact
pattern the freelance automation already uses for KarLancer/ParsCoders
(`SITE_SESSION_<SITE>` secrets + Playwright).

## Privacy rule (user order, 2026-11-18 — non-negotiable)

The Instagram session is used **ONLY for posting his Reels — nothing else
on the account, ever.** No reading DMs or messages, no reading the feed,
no follows, likes, comments, or browsing, no profile changes. The session
stays completely private: sealed as a GitHub secret, never printed, never
logged, never copied anywhere else, never shared with anyone. The runner
script (`instagram_reel_post.py`) enforces this with a navigation
allowlist (home page, the Create dialog, the published post URL) — any
other Instagram URL is refused by the code.

## Where everything lives

| Piece | Location |
|---|---|
| Runner script | `mohammadlali0707-stack/agw-workers` → `scripts/site_proposal/instagram_reel_post.py` |
| GitHub workflow | same repo → `.github/workflows/instagram-reel-post.yml` (name: **Instagram Reel Post**) |
| Session sealer (server) | Arvan `185.194.188.11` → `/opt/hq/set_instagram_cookies.py` (mirror in this repo dir: `workflow/instagram/`) |
| n8n workflow | **Instagram Reels Publisher (Session)**, id `P6gFqEeibezobCM6`, **INACTIVE** until the login probe passes |
| n8n webhook | `POST https://n8n.airboxvip.top/webhook/publish-reel-session` (404s while the workflow is inactive — expected) |
| Notion queue | **Instagram Reels Queue**, data source id `41cbefc7-ddfb-8189-b454-000b408d09a3` (Company HQ → 03 · Content & Channels). Status: Ready / Posting / Posted / Failed. **Zero rows as of 2026-11-18 — never seed rows without his explicit say-so.** |
| Videos | `https://n8n.airboxvip.top/reels/succession-s01e01-words-1-5-reel-v8.mp4` and `...-words-6-10-reel-v8.mp4` (~108 s each, on Arvan nginx `/var/www/html/reels/`) |
| Captions | In the Notion row (Caption field). The two known captions (Reel 1 words 1–5, Reel 2 words 6–10) are in the project notes; never invent a caption. |
| Run results | `mohammadlali0707-stack/hq-ai-results` → `jobs/<job_id>/result.json` (+ screenshots `home.png`, `create_dialog.png`, `upload.png`, `caption.png`, `published.png` in the same dir) |

## Secrets (where they live, what they are)

| Secret | Where | Notes |
|---|---|---|
| `SITE_SESSION_INSTAGRAM` | GitHub secret in `mohammadlali0707-stack/agw-workers` | The Instagram login session: `{"cookies": [...], "localStorage": {...}}`. Sealed (NaCl sealed box) via `/opt/hq/set_instagram_cookies.py` on 2026-11-18T17:40:59Z (10 cookies). **Login probe PASSED the same day** (run 44361625139, `english_class_0121`, no challenge). |
| `SITE_SESSIONS` | Same repo (fallback) | Combined JSON keyed by lowercase site name (`{"instagram": {...}}`). Per-site secret wins. |
| n8n credential `GitHub PAT (agw-workers dispatch)` | n8n, id `AM57VL09BsEKihYS` (httpHeaderAuth) | Used by the n8n workflow to dispatch the GitHub workflow and poll `result.json`. |
| n8n credential `Notion API Key` | n8n, id `PfrA8Rmtrae2odET` (notionApi) | Used by the n8n workflow to read/update the Notion queue rows. The Notion DB must be shared with this integration or the nodes cannot see it. |

## How it works (flow)

```
Notion row Status -> Ready
  -> (Notion automation, built in the Notion UI — NOT built yet) POSTs the row id
     to https://n8n.airboxvip.top/webhook/publish-reel-session
  -> n8n reads Video URL + Caption from the row, marks it Posting
  -> n8n dispatches GitHub workflow instagram-reel-post.yml
     (mode=post, dry_run=false, job_id=ig-YYYYMMDD-xxxxxx)
  -> GitHub runner (ubuntu-latest): installs Playwright + Chromium,
     loads SITE_SESSION_INSTAGRAM, opens instagram.com, uploads the Reel,
     writes jobs/<job_id>/result.json into hq-ai-results and pushes it
  -> n8n polls result.json (max 20 x 30 s)
  -> row marked Posted (+ Posted URL) or Failed (+ Error text)
```

Manual trigger (no Notion needed): GitHub repo → Actions →
**Instagram Reel Post** → Run workflow → fill `video_url`, `caption`,
`mode`, `dry_run`, `job_id`.

## Safety gates (in this order — a run can fail at any of them)

1. **No session** → status `no_session`, error `no Instagram session configured`.
   Nothing is downloaded, no browser launches. (Proven by the dry-run test.)
2. **dry_run** → mode `post` with `dry_run=true` is refused before any
   browser work (status `dry_run`). Publish needs `mode=post` **and**
   `dry_run=false`.
3. **Login check** → `login_required` (login wall) or `checkpoint`
   (Instagram challenged the session). Both mean: re-capture the session.
4. **Probe first** → `mode=probe` (default) only inspects: it opens the
   Create dialog, picks **Reel** in the Create menu, attaches the video,
   walks Crop/Edit to the caption screen, verifies the caption field is
   findable and fillable, reports the duration the uploader shows and
   any length-limit error, then closes the dialog with Discard. It
   submits nothing — Share is never clicked in probe mode. Always run
   probe before the first post.
5. **Caption (verbatim, required for post)** → the poster NEVER writes,
   rewords, or invents a caption. It pastes the exact caption it is
   handed (workflow `caption` input / Notion row Caption field, written
   by Muse together with the video) into Instagram's caption field and
   posts. **A post with no caption is refused** before any browser work
   (status `error`, `no caption supplied`). In probe mode the field is
   filled with the supplied caption, or a clearly-marked TEST string
   when none is supplied — and the dialog is then discarded, never shared.
6. **Post** → upload → Reel → Next → caption (verbatim) → Share →
   verify the post URL loads. Result `posted` carries `post_url`.

`result.json` fields: `ok`, `status`, `job_id`, `mode`, `dry_run`,
`video_url`, `video_bytes`, `video_seconds`, `logged_in`, `username`,
`checkpoint`, `error`, `error_message`, `post_url`, `steps[]`
(each step: `{step, ok, detail}`), plus on probe runs `dialog_text` /
`upload_dialog_text` / `duration_shown` / `limit_error` / `reel_choice`
(`"Reel"` when the Create menu pick succeeded) / `file_input_found` /
`attach_method` / `caption_field_found` / `caption_filled` /
`caption_selector` / `create_attempts` (per-selector diagnostics) /
`create_menu_text`.

## The web upload flow (as fixed 2026-11-18 — what the script does)

Why the first probe failed: the old script clicked the bare Create
`<svg>` at a 1280×800 viewport and waited for a `[role=dialog]` —
but at that width Instagram collapses the sidebar (the + button is not
clickable), and clicking Create opens a **menu** (Post vs Reel), not a
dialog. The fixed flow, verified against a working Playwright uploader:

1. Browser viewport **1920×1080** (narrower collapses the sidebar).
2. Dismiss "Save login info?" / "Turn on Notifications" popups (Not Now).
3. Click Create (+) — the clickable **ancestor** of the
   `svg[aria-label='New post']`, not the svg itself — with fallbacks.
4. In the Create menu, click **Reel**. If the menu has no Reel option,
   the run FAILS — it never falls back to a regular Feed Post.
5. In the upload dialog, attach the video: the `input[type=file]` is
   created **on demand**, so if no input exists yet the script clicks
   **"Select from computer"** and catches the native file chooser.
6. Dismiss the Reels-info popup if shown; pick **9:16** on the crop
   screen; click Next through Crop → Edit to the caption screen.
7. Caption field is a **contenteditable div**
   (`div[role=textbox][contenteditable=true]`, aria-label contains
   "caption") — NOT a `<textarea>`. Fill (or select-all + type).
8. Post mode only: click **Share**, wait for "Your reel has been
   shared", capture the `/reel/...` URL and verify it loads.

## Capturing / rotating the session (the user's one action)

The session is captured **once by the user in his own browser**
— no password is ever typed into any automation or chat. (The live
session was actually captured on his **phone's Firefox** with the free
**Cookie-Editor** extension: Export → JSON → `site_instagram.json`.
The desktop steps below are the fallback, not what was used.)

1. On his computer, open Chrome/Edge, log into instagram.com as himself.
2. Install the free **EditThisCookie** extension (Chrome Web Store).
3. On instagram.com, click the extension icon → **Export** (copies a JSON
   array of cookies).
4. Paste into a file, save as `site_instagram.json`, and send it to the
   agent in the chat (file upload).
5. The agent copies it to `/root/incoming/site_instagram.json` on the
   Arvan server and runs `python3 /opt/hq/set_instagram_cookies.py`.
   The script validates it (must contain a `sessionid` cookie for
   instagram.com), seals it into the `SITE_SESSION_INSTAGRAM` secret in
   `mohammadlali0707-stack/agw-workers`, and **shreds the file**.
   The chat copy is shredded too. Values are never printed.
6. Rotate the same way whenever Instagram logs the session out or
   challenges it (status `login_required` / `checkpoint`).

## Debugging

| Symptom | Meaning / fix |
|---|---|
| `no_session` | Secret not set (or empty). Capture a session (above). |
| `login_required` | Session expired/logged out. Re-capture. |
| `checkpoint` | Instagram challenged the runner login (likely: datacenter IP). Re-capture a fresh session; if it recurs, the runner-IP route may not survive — fall back to posting from inside the Instagram app. |
| `dry_run` | Dispatch used `dry_run=true` with `mode=post`. Intended. |
| `probe_failed` / `create dialog did not open` | Instagram changed its web UI. Open the screenshots in `jobs/<job_id>/`, read `create_attempts` / `create_menu_text` in `result.json`, update the selectors in `instagram_reel_post.py` (`CREATE_CLICK_TARGETS`, caption/share selectors), re-run probe. |
| `create menu has no Reel option` | Instagram dropped the Reel choice from the Create menu. STOP — do not force a regular Post. Report it; the Reel route needs a new approach. |
| `no caption supplied` | A `mode=post` dispatch had an empty `caption` input. The poster never invents captions — re-dispatch with the caption filled. |
| `post_failed: uploader rejected the video` | The web uploader refused the file (length/format). The probe's `upload_dialog_text` / `limit_error` shows the exact message. ~108 s videos are near the web uploader's limits — this is the main open risk (see below). |
| n8n marks Failed with `Timed out` | Runner took >10 min or crashed before writing `result.json`. Check the GitHub Actions run log (repo → Actions → Instagram Reel Post → the run). |
| Webhook 404s | The n8n workflow is inactive (expected until activation). Activate it in the n8n UI only after the login probe passes. |
| Notion nodes error | The queue DB is not shared with the `Notion API Key` integration (Notion: database ••• → Add connections). |

GitHub Actions run logs: repo → Actions → Instagram Reel Post → run →
job log. Cookie values are masked in logs/results (the script masks
session values before writing anything).

## Known risks / unverified items (2026-11-18)

1. **Web-uploader length limit — no rejection observed at 107.8 s.**
   The dry-run probe `ig-probe-20261118-1830` carried the ~108 s Reel
   all the way to the caption screen with no length error
   (`limit_error: null`), so the web uploader accepts it at least that
   far. The final Share step is still unproven until the first live post.
2. **Upload selectors — PROVEN 2026-11-18 by dry-run probe
   `ig-probe-20261118-1830`** (run 44363624822): Create dialog opened,
   video attached, walked Crop → Edit to the caption screen, caption
   field found + filled, dialog discarded, nothing posted
   (`jobs/ig-probe-20261118-1830/result.json`). Note: on this account
   clicking Create opens the upload dialog directly (no Post/Reel menu);
   the vertical video is auto-classified as a Reel ("New reel" on the
   share screen). The script still picks Reel explicitly if a menu ever
   appears, and fails rather than posting as a Feed Post. Instagram's
   web UI changes often, so selectors may still need another round.
3. **Datacenter-IP checkpoint.** A GitHub runner IP is not his phone.
   Instagram will likely challenge the first login(s) from a new IP
   (code to his email/SMS). The script reports `checkpoint` and stops;
   it never tries to solve a challenge.
4. **Session lifetime.** Instagram sessions expire or get invalidated
   (e.g. if he changes his password or Instagram forces re-login).
   Re-capture with the steps above.
5. **n8n GitHub credential validity is unverified.** The dispatch
   credential exists (`AM57VL09BsEKihYS`) but has never been used by this
   workflow. The first real Notion-triggered run will prove it; the
   dry-run test used the server PAT instead.
6. **Poll-counter self-reference.** The n8n poll loop counts attempts by
   reading the Poll Counter node's own previous output. If that ever
   fails to increment, the loop still ends at n8n's execution timeout
   (the row would stay `Posting` — check manually).

## What is NOT built (deliberately)

- The **old Graph API workflow** (`Instagram Reels Publisher (Webhook)`,
  id `SAO5WmicWydrLwTS`) is left INACTIVE and untouched — the token
  route is abandoned.
- The **Notion automation** (Status → Ready → webhook) must be built in
  the Notion UI (no API exists for it). Webhook URL is above.
- **No Notion rows are seeded.** The queue is empty until he says so.
- **Login is PROVEN** (probe run 44361625139, 2026-11-18:
  `english_class_0121`, no challenge). **The upload path is PROVEN to
  the caption screen** by dry-run probe `ig-probe-20261118-1830` (run
  44363624822, 2026-11-18): file input found, video attached, Crop →
  Edit walked, caption field found + filled, dialog discarded, nothing
  posted. **The actual publish step (Share → live Reel) is unproven**
  until the first live post.
