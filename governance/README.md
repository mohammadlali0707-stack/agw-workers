# governance/

Control-Room's rules and company map, consolidated into `agw-workers` on
2026-09-30 (owner's instruction). Control-Room is frozen: no new issues,
workflows or gates there. Open fleet / governance issues on **this** repo --
`status.airboxvip.top` chat already files them here.

| File | What it is |
|---|---|
| `AGENTS.md` | Control-Room's agent laws: work on `main`, record every commit, gates before stopping, no claim without a probe, issue response protocol |
| `COMPANY_SCOPE.md` / `company_scope.json` | Which account hosts which project (machine-readable twin used by the old `route_topic.py`) |
| `CROSS_ACCOUNT_LESSONS.md` | Lessons from cross-account dispatch |
| `SECURITY_RULES.md` | Secret-handling rules (same text CCP carries) |

Fleet shape since 2026-09-30: every account runs 3 bots -- w01 lead, w02
builder, w03 reviewer (see `Claud-Cloud-Project` `Team/ORG.md`, CURRENT
STRUCTURE).

Project focus since 2026-09-30:
- **n8n-studio** -- continue (revenue work).
- **AirboxVIP_Coffeenet** -- continue, product work only; no more fleet plumbing commits there.
- **Claud-Cloud-Project (TBS)** -- time-boxed to one milestone: a playable Ren'Py slice plus a real APK, no new gates or roles; stop if it is not reached.
- **status-dashboard** -- maintenance only, no new features.
- **Control-Room** -- frozen; archive pending.
