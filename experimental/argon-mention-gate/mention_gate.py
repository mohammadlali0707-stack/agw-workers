"""Offline proposed direct-mention gate; not integrated with production."""
import re

# Avoid matching suffixes like @Argon-task, @Argon2 or @آرگان‌کار.
# Matching a lexical tag is not proof of sender identity, room ACL, or user intent.
_MENTION = re.compile(r'(?<![\w@])@(?:argon|آرگان)(?![\w\-\u200c\u200d])', re.IGNORECASE)

def directly_mentions_argon(text):
    return isinstance(text, str) and bool(_MENTION.search(text))

def dispatch_if_mentioned(text, dispatch):
    """Demonstrate side-effect boundary; must be placed before any remote call."""
    if not directly_mentions_argon(text):
        return False
    dispatch(text)
    return True
