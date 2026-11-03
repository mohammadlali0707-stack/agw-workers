"""Experimental private-chat authorization; not wired to production.

The caller MUST supply platform and transport_verified from a trusted ingress
boundary, never from the update body. Owner IDs must come from trusted config.
Only Telegram private messages/callbacks are modeled; Bale is unsupported until
its transport verification and update contract are independently established.
This function does not verify webhooks or prevent replay.
"""
from __future__ import annotations

_MAX_ID = 2**53 - 1


def _id(value):
    if type(value) is int:
        return str(value) if 0 < value <= _MAX_ID else None
    if (type(value) is str and value.isascii() and value.isdigit()
            and not value.startswith('0') and len(value) <= 16):
        number = int(value)
        return value if 0 < number <= _MAX_ID else None
    return None


def authorize_private_update(platform, update, owner_ids, transport_verified):
    """Authorize only an authenticated Telegram owner's private interaction.

    This is a restrictive policy, not a replacement for the existing router.
    Group chats, channels, other update types and Bale intentionally fail closed.
    """
    if transport_verified is not True or platform != 'telegram':
        return False
    if not isinstance(update, dict) or not isinstance(owner_ids, dict):
        return False
    owner = _id(owner_ids.get('telegram'))
    if owner is None:
        return False
    # Reject ambiguous/multiple or unsupported update types, not merely pick one.
    if set(update) - {'update_id', 'message', 'callback_query'}:
        return False
    if ('message' in update) == ('callback_query' in update):
        return False
    if 'message' in update:
        message = update['message']
        if not isinstance(message, dict):
            return False
        sender = message.get('from')
    else:
        callback = update['callback_query']
        if not isinstance(callback, dict):
            return False
        sender = callback.get('from')
        message = callback.get('message')
    if not isinstance(sender, dict) or not isinstance(message, dict):
        return False
    chat = message.get('chat')
    if not isinstance(chat, dict) or chat.get('type') != 'private':
        return False
    if 'sender_chat' in message or sender.get('is_bot') is not False:
        return False
    return _id(sender.get('id')) == owner and _id(chat.get('id')) == owner
