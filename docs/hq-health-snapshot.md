# HQ health snapshot — isolated phase 1

Read-only Python standard-library foundation. It does not start a service, expose an endpoint, publish messages, or modify the OS bot. Process state is not end-to-end health; Gemini model and n8n workflow health are explicitly unknown. Never put this private output on a public Gist or unauthenticated endpoint. Use one authenticated adapter in a later phase for both chat and bot.

Run `python3 -m unittest discover -s tests -p 'test_hq_health_snapshot.py'` and `python3 -m hq.health_snapshot`. On non-systemd systems checks correctly report unknown. Stale data must not be displayed as healthy. The 90% disk and 10% available-memory thresholds are initial defaults, not a capacity guarantee.
