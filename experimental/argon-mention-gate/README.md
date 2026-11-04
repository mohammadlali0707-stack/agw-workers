# Argon direct-mention gate (offline prototype)

This module is an isolated example. It does not integrate with or modify any live service.
Run: `python3 -m unittest -v` in this directory.

A lexical mention does not authenticate a sender or verify a room. Actual integration must independently verify sender identity, room policy, directness, loop prevention, retry behavior, and all message entry points. Review the deployed code before adapting the gate and validate in a staging environment; never deploy this prototype by copying blindly.
