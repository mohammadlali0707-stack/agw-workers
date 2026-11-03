"""Pure Telegram webhook-secret validation; no web server or secret storage.

Use before processing update bodies. This verifies a shared transport secret,
not the human sender's authorization. The caller must also authorize the
verified message sender and destination, and handle duplicate updates.
"""
from __future__ import annotations

import hmac
import re

_HEADER = 'x-telegram-bot-api-secret-token'
_ALLOWED = re.compile(r'^[A-Za-z0-9_-]{1,256}$')


def verify_telegram_webhook(header_pairs, expected_secret):
    """Fail closed; require one header and an explicitly configured secret.

    header_pairs is an iterable of (name, value), retaining duplicates from the
    HTTP server. The caller must not collapse duplicate headers before calling.
    Neither the provided nor expected secret is logged or returned.
    """
    if not isinstance(expected_secret, str) or not _ALLOWED.fullmatch(expected_secret):
        return False
    try:
        found = []
        for name, value in header_pairs:
            if not isinstance(name, str):
                return False
            if name.lower() == _HEADER:
                if not isinstance(value, str):
                    return False
                found.append(value)
                if len(found) > 1:
                    return False
        if len(found) != 1 or not _ALLOWED.fullmatch(found[0]):
            return False
        return hmac.compare_digest(expected_secret.encode('ascii'), found[0].encode('ascii'))
    except (TypeError, ValueError, UnicodeError):
        return False
