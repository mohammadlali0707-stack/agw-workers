# Experimental cluster intake — offline only

A single-source, single-room, Notion 2-only queue proof of concept. This is not a listener and performs no network, Notion API, or chat operations. Only synthetic test data is used.

`trusted_source`, `owner_verified`, and `room_verified` are placeholders for independent, authenticated checks by a future caller. Display names, message content, or a shared agent key alone are not evidence of owner identity. No such verifier exists here. This module must not be connected to production as-is.

Capture deduplicates integer message IDs and detects changed payloads; a conflict observed while pending or claimed freezes the item and revokes its claim, it retains a SHA-256 digest, not the message. Digests are not anonymous or secret: short or predictable text can be guessed. Claims expire until dispatch begins. A job in `dispatching` cannot be automatically reclaimed: after a crash or timeout it requires manual reconciliation because the remote call may have succeeded. This sacrifices availability to reduce duplicate dispatch; it is not an exactly-once guarantee. A conflict reported after dispatch begins cannot undo an in-flight operation and is not persistently recorded by this prototype; a production design needs an audit trail and reconciliation. API session IDs may be recorded only against the matching claim.

Before any live deployment, build and test source and actor authentication, room isolation, backfill/cursor and gap recovery based on actual server semantics, safe session reconciliation, response delivery, access control, local DB permissions, retention, logging, queue bounds, and a real approval enforcement mechanism before deletions. The caller must refetch and match the message for dispatch; do not persist plaintext or sensitive deployment details in this public prototype. Local timestamps must come from a trusted clock.

Run locally: `python3 -m unittest discover -s tests -v`
