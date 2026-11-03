# Experimental intake: operator guidance (offline prototype)

Not a runbook for production. No live listener, transport, response publisher, authentication, approval gate, replay mechanism, alerts or admin interface exists here. Never feed private production messages or credentials into its tests. The public repository contains synthetic examples only.

## Status semantics

- `pending`: captured; not yet claimed.
- `claimed`: a worker holds a time-limited local claim. Only after expiry may another claim be attempted; trusted local time is required.
- `conflict`: a different payload was observed for the same message ID before dispatch. The claim is revoked. No automatic resume or conflict-resolution API exists.
- `dispatching`: local transition occurred before a possible remote call; it does **not** establish that the remote call was made or accepted. Never automatically reissue after timeout, crash or restart. Evidence and a separately designed manual reconciliation procedure are necessary.
- `session_known`: local session ID recorded; it does **not** prove agent completion, safe tool use or response delivery.

A conflict observation is counted in SQLite without message text, even when the state is `dispatching` or `session_known`. It cannot cancel a request already in flight. This count alone is not a full security audit and must not be cleared or interpreted as an authorization event. `inspect()` is local code, not an authenticated dashboard.

## Before a real pilot

1. Independently authenticate the producing actor and intended room, not displayed sender names. Verify actual source semantics and permissions.
2. Design and test persisted source cursors, catch-up, ordering, filtering, backpressure, duplication and restart with the real source contract; this prototype does not do this.
3. Prove how to resolve ambiguous calls using authoritative evidence available to the same API connection. If evidence is missing, remain blocked and ask the owner; never guess or automatically retry.
4. Enforce owner approval before every deletion at the operation boundary, including indirect agent tools. If no enforceable interception exists, do not claim this guarantee and do not grant unsafe tools to the automated path.
5. Add independently tested response delivery and loop prevention, endpoint/credential security, local file permissions and retention, bounded queue/alerts, deployment/rollback and fail-closed behavior on I/O errors.
6. Conduct controlled end-to-end testing in a segregated environment and verify no interference with running bot or other workspaces before a staged rollout.

These controls are prerequisites, not claims that the prototype implements them. Exactly-once external execution is not guaranteed.
