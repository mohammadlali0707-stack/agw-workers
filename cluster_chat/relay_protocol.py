"""cluster_chat.relay_protocol

Shared core protocol, cryptography, secret scrubbing, and command-parsing
library for the GitHub Relay Mode and Web Proxy Bridge of Cluster Chat.

Designed for resilient asynchronous communication between Mohammad (via static
browser UI / GitHub Pages), LeadDesk AI agents on the VPS (/opt/hq/), and
GitHub Actions runners during network filtering or National Internet isolation.
"""

from __future__ import annotations

import base64
import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import re
import secrets
from typing import Any

# Optional cryptography import with graceful stdlib fallback
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    HAVE_CRYPTOGRAPHY = True
except ImportError:  # pragma: no cover
    HAVE_CRYPTOGRAPHY = False

# ==============================================================================
# Protocol Constants
# ==============================================================================

VALID_ROOMS: tuple[str, ...] = ("main", "dm-argon", "workers")
VALID_SENDERS: tuple[str, ...] = ("mohammad", "argon", "worker", "web-bridge", "system")

MAX_ATTACHMENT_BYTES: int = 4 * 1024 * 1024  # 4 MB chunk size cap
DEFAULT_WEB_MAX_BYTES_TEXT: int = 2 * 1024 * 1024  # 2 MB for HTML/Markdown extraction
DEFAULT_WEB_MAX_BYTES_FILE: int = 5 * 1024 * 1024  # 5 MB for binary file downloads

DEFAULT_PBKDF2_ITERATIONS: int = 100_000
SALT_BYTES: int = 16
IV_BYTES: int = 12
AUTH_TAG_BYTES: int = 16
KEY_BYTES: int = 32  # 256 bits

CIPHER_AES_256_GCM: str = "aes-256-gcm"
CIPHER_NONE: str = "none"
CIPHER_STDLIB_FALLBACK: str = "aes-256-gcm-fallback"


# ==============================================================================
# Exceptions
# ==============================================================================

class RelayProtocolError(ValueError):
    """Base exception for all relay protocol errors."""
    pass


class RelayValidationError(RelayProtocolError):
    """Raised when an envelope or payload fails structural validation."""
    pass


class RelayCryptoError(RelayProtocolError):
    """Raised when encryption, decryption, or key derivation fails."""
    pass


# ==============================================================================
# Message ID Calculation
# ==============================================================================

def compute_msg_id(
    room: str,
    sender: str,
    created_at: str,
    body: str,
    nonce: str = "",
) -> str:
    """Compute a deterministic SHA-256 msg_id hex string from message fields.

    The canonical format binds the room, sender, timestamp, nonce, and the
    SHA-256 digest of the body to produce a unique, collision-resistant identifier:
      sha256(room:sender:created_at:nonce:sha256(body))
    """
    if room == "workers-room":
        room = "workers"
    if not isinstance(room, str) or not room.strip():
        raise RelayValidationError("Room must be a non-empty string")
    if not isinstance(sender, str) or not sender.strip():
        raise RelayValidationError("Sender must be a non-empty string")

    body_str = body if isinstance(body, str) else str(body or "")
    body_hash = hashlib.sha256(body_str.encode("utf-8")).hexdigest()
    canonical_input = f"{room}:{sender}:{created_at}:{nonce}:{body_hash}"
    return hashlib.sha256(canonical_input.encode("utf-8")).hexdigest()


# ==============================================================================
# Secret Scrubbing & Sanitization
# ==============================================================================

_SCRUB_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    # PEM Private Keys (RSA, EC, DSA, OPENSSH, or generic PRIVATE KEY)
    (
        re.compile(r"-----\s*BEGIN\s+[A-Z0-9_\- ]*PRIVATE\s+KEY\s*-----[\s\S]*?-----\s*END\s+[A-Z0-9_\- ]*PRIVATE\s+KEY\s*-----"),
        "[REDACTED_SECRET]",
    ),
    # GitHub Personal Access Tokens & App Tokens:
    # Classic (ghp_), Fine-grained (github_pat_), OAuth (gho_), Server-to-server (ghs_),
    # User-to-server (ghu_), Refresh tokens (ghr_)
    (
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b"),
        "[REDACTED_SECRET]",
    ),
    (
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
        "[REDACTED_SECRET]",
    ),
    # Google / Gemini API Keys (starts with AIza followed by 30-45 chars of base64/url-safe)
    (
        re.compile(r"\bAIza[0-9A-Za-z\-_]{30,45}\b"),
        "[REDACTED_SECRET]",
    ),
    # Telegram Bot Tokens: 8-10 digits followed by colon and 35 chars
    (
        re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
        "[REDACTED_SECRET]",
    ),
    # Bearer Authorization headers / tokens
    (
        re.compile(r"(?i)\bBearer(?:\s+|:\s*)[A-Za-z0-9\-._~+/]+=*"),
        "[REDACTED_SECRET]",
    ),
    # Additional high-risk secrets: OpenAI api keys and AWS access key IDs
    (
        re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
        "[REDACTED_SECRET]",
    ),
    (
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "[REDACTED_SECRET]",
    ),
)


def scrub_secrets(text: str) -> str:
    """Redact sensitive credentials, tokens, and private keys into [REDACTED_SECRET].

    Sanitizes GitHub PATs, Google/Gemini API keys, Telegram bot tokens,
    Bearer headers, and PEM private keys.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)

    sanitized = text
    for pattern, replacement in _SCRUB_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


# ==============================================================================
# Cryptography: AES-256-GCM with PBKDF2 Key Derivation
# ==============================================================================

def _stdlib_stream_cipher_keystream(k_enc: bytes, iv: bytes, length: int) -> bytes:
    """Generate a pseudo-random keystream of specified length using HMAC-SHA256 CTR."""
    chunks = []
    block_num = 0
    generated = 0
    while generated < length:
        counter = block_num.to_bytes(4, byteorder="big")
        block = hmac.new(k_enc, iv + counter, hashlib.sha256).digest()
        chunks.append(block)
        generated += len(block)
        block_num += 1
    return b"".join(chunks)[:length]


def _stdlib_encrypt_payload(plaintext: str, passphrase: str, iterations: int = DEFAULT_PBKDF2_ITERATIONS) -> dict[str, Any]:
    """Pure-Python standard library fallback authenticated stream cipher (HMAC-SHA256 CTR)."""
    salt = secrets.token_bytes(SALT_BYTES)
    iv = secrets.token_bytes(IV_BYTES)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        passphrase.encode("utf-8"),
        salt,
        iterations,
        dklen=64,
    )
    k_enc = derived[:32]
    k_mac = derived[32:]

    pt_bytes = plaintext.encode("utf-8")
    keystream = _stdlib_stream_cipher_keystream(k_enc, iv, len(pt_bytes))
    ciphertext = bytes(p ^ k for p, k in zip(pt_bytes, keystream))

    # Compute 16-byte HMAC auth tag
    auth_tag = hmac.new(k_mac, iv + ciphertext, hashlib.sha256).digest()[:AUTH_TAG_BYTES]
    ciphertext_with_tag = ciphertext + auth_tag

    return {
        "cipher": CIPHER_STDLIB_FALLBACK,
        "algorithm": "AES-256-GCM-FALLBACK",
        "salt": base64.b64encode(salt).decode("ascii"),
        "iv": base64.b64encode(iv).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext_with_tag).decode("ascii"),
        "iterations": iterations,
    }


def _stdlib_decrypt_payload(
    salt: bytes,
    iv: bytes,
    raw_ciphertext: bytes,
    passphrase: str,
    iterations: int = DEFAULT_PBKDF2_ITERATIONS,
) -> str:
    """Pure-Python standard library fallback decryption and verification."""
    if len(raw_ciphertext) < AUTH_TAG_BYTES:
        raise RelayCryptoError("Ciphertext payload is shorter than authentication tag")

    ciphertext = raw_ciphertext[:-AUTH_TAG_BYTES]
    received_tag = raw_ciphertext[-AUTH_TAG_BYTES:]

    derived = hashlib.pbkdf2_hmac(
        "sha256",
        passphrase.encode("utf-8"),
        salt,
        iterations,
        dklen=64,
    )
    k_enc = derived[:32]
    k_mac = derived[32:]

    expected_tag = hmac.new(k_mac, iv + ciphertext, hashlib.sha256).digest()[:AUTH_TAG_BYTES]
    if not hmac.compare_digest(received_tag, expected_tag):
        raise RelayCryptoError("Authentication tag verification failed: incorrect passphrase or corrupted data")

    keystream = _stdlib_stream_cipher_keystream(k_enc, iv, len(ciphertext))
    plaintext_bytes = bytes(c ^ k for c, k in zip(ciphertext, keystream))
    try:
        return plaintext_bytes.decode("utf-8")
    except UnicodeDecodeError as err:
        raise RelayCryptoError("Decrypted payload contains invalid UTF-8") from err


def encrypt_payload(
    plaintext: str,
    passphrase: str,
    iterations: int = DEFAULT_PBKDF2_ITERATIONS,
) -> dict[str, Any]:
    """Encrypt a plaintext string using AES-256-GCM with PBKDF2-HMAC-SHA256 key derivation.

    Specification:
      - Key Derivation: PBKDF2-HMAC-SHA256 with 100,000 iterations and 16-byte random salt.
      - Cipher: AES-256-GCM with 12-byte random IV and 16-byte authentication tag.
      - Output: Dict with Base64-encoded 'salt', 'iv', 'ciphertext' (with tag appended).
      - Interoperable with browser WebCrypto API (window.crypto.subtle).
    """
    if not isinstance(plaintext, str):
        plaintext = str(plaintext or "")
    if not passphrase or not isinstance(passphrase, str):
        raise RelayCryptoError("Passphrase must be a non-empty string")

    if not HAVE_CRYPTOGRAPHY:
        return _stdlib_encrypt_payload(plaintext, passphrase, iterations)

    salt = secrets.token_bytes(SALT_BYTES)
    iv = secrets.token_bytes(IV_BYTES)

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_BYTES,
        salt=salt,
        iterations=iterations,
    )
    key = kdf.derive(passphrase.encode("utf-8"))
    aesgcm = AESGCM(key)
    ciphertext_with_tag = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)

    return {
        "cipher": CIPHER_AES_256_GCM,
        "algorithm": "AES-256-GCM",
        "salt": base64.b64encode(salt).decode("ascii"),
        "iv": base64.b64encode(iv).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext_with_tag).decode("ascii"),
        "iterations": iterations,
    }


def decrypt_payload(enc_dict: dict[str, Any], passphrase: str) -> str:
    """Decrypt an encrypted payload dict or envelope using AES-256-GCM or stdlib fallback.

    Accepts either an explicit encryption dictionary (with 'salt', 'iv', 'ciphertext')
    or a MessageEnvelope dictionary (with 'iv', 'body', and 'meta.salt').
    """
    if not isinstance(enc_dict, dict):
        raise RelayCryptoError("Encrypted payload must be a dictionary")
    if not passphrase or not isinstance(passphrase, str):
        raise RelayCryptoError("Passphrase must be a non-empty string")

    # Extract salt
    salt_b64 = enc_dict.get("salt")
    if not salt_b64 and isinstance(enc_dict.get("meta"), dict):
        salt_b64 = enc_dict["meta"].get("salt")
    if not salt_b64:
        raise RelayCryptoError("Missing 'salt' in encrypted payload")

    # Extract IV
    iv_b64 = enc_dict.get("iv")
    if not iv_b64:
        raise RelayCryptoError("Missing 'iv' in encrypted payload")

    # Extract Ciphertext
    ct_b64 = enc_dict.get("ciphertext") or enc_dict.get("body")
    if not ct_b64:
        raise RelayCryptoError("Missing 'ciphertext' or 'body' in encrypted payload")

    # Extract iterations
    iterations = DEFAULT_PBKDF2_ITERATIONS
    if "iterations" in enc_dict:
        try:
            iterations = int(enc_dict["iterations"])
        except (ValueError, TypeError):
            iterations = DEFAULT_PBKDF2_ITERATIONS
    elif isinstance(enc_dict.get("meta"), dict) and "iterations" in enc_dict["meta"]:
        try:
            iterations = int(enc_dict["meta"]["iterations"])
        except (ValueError, TypeError):
            iterations = DEFAULT_PBKDF2_ITERATIONS

    cipher_name = (
        enc_dict.get("cipher")
        or enc_dict.get("algorithm")
        or CIPHER_AES_256_GCM
    ).lower()

    try:
        salt = base64.b64decode(salt_b64)
        iv = base64.b64decode(iv_b64)
        raw_ciphertext = base64.b64decode(ct_b64)
    except Exception as err:
        raise RelayCryptoError(f"Base64 decoding failed for encryption components: {err}") from err

    # Check for fallback cipher
    if cipher_name in (CIPHER_STDLIB_FALLBACK, "aes-256-gcm-fallback", "hmac-sha256-xor-stream"):
        return _stdlib_decrypt_payload(salt, iv, raw_ciphertext, passphrase, iterations)

    # Standard AES-256-GCM
    if not HAVE_CRYPTOGRAPHY:
        raise RelayCryptoError("Payload encrypted with AES-256-GCM requires the 'cryptography' library")

    try:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=KEY_BYTES,
            salt=salt,
            iterations=iterations,
        )
        key = kdf.derive(passphrase.encode("utf-8"))
        aesgcm = AESGCM(key)
        plaintext_bytes = aesgcm.decrypt(iv, raw_ciphertext, None)
        return plaintext_bytes.decode("utf-8")
    except Exception as err:
        raise RelayCryptoError("Decryption failed: authentication tag mismatch or invalid passphrase") from err


# ==============================================================================
# Message Envelope Data Structure & Validation
# ==============================================================================

@dataclass
class MessageEnvelope:
    """Canonical message envelope representation for relay transmission."""
    room: str
    sender: str
    body: str
    version: int = 1
    msg_id: str = ""
    created_at: str = ""
    encrypted: bool = False
    cipher: str = CIPHER_NONE
    iv: str | None = None
    attachments: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert envelope to serializable dictionary."""
        return {
            "version": self.version,
            "msg_id": self.msg_id,
            "room": self.room,
            "sender": self.sender,
            "created_at": self.created_at,
            "encrypted": self.encrypted,
            "cipher": self.cipher,
            "iv": self.iv,
            "body": self.body,
            "attachments": list(self.attachments),
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MessageEnvelope:
        """Create and validate MessageEnvelope from dictionary."""
        validated = validate_envelope(data)
        return cls(
            version=int(validated.get("version", 1)),
            msg_id=str(validated.get("msg_id", "")),
            room=str(validated.get("room", "")),
            sender=str(validated.get("sender", "")),
            created_at=str(validated.get("created_at", "")),
            encrypted=bool(validated.get("encrypted", False)),
            cipher=str(validated.get("cipher", CIPHER_NONE)),
            iv=validated.get("iv"),
            body=str(validated.get("body", "")),
            attachments=list(validated.get("attachments") or []),
            meta=dict(validated.get("meta") or {}),
        )


def validate_envelope(data: dict[str, Any]) -> dict[str, Any]:
    """Validate structure, types, and constraints of a relay message envelope.

    Returns the validated dictionary or raises RelayValidationError.
    """
    if not isinstance(data, dict):
        raise RelayValidationError("Message envelope must be a dictionary")

    # Version
    version = data.get("version")
    if not isinstance(version, int) or version < 1:
        raise RelayValidationError(f"Invalid envelope version: {version}. Expected positive integer.")

    # Room
    room = data.get("room")
    if room == "workers-room":
        room = "workers"
        data["room"] = "workers"
    if not isinstance(room, str) or room not in VALID_ROOMS:
        raise RelayValidationError(f"Invalid room '{room}'. Must be one of {VALID_ROOMS}")

    # Sender
    sender = data.get("sender")
    if not isinstance(sender, str) or sender not in VALID_SENDERS:
        raise RelayValidationError(f"Invalid sender '{sender}'. Must be one of {VALID_SENDERS}")

    # Created at
    created_at = data.get("created_at")
    if not isinstance(created_at, str) or not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", created_at):
        raise RelayValidationError(f"Invalid created_at '{created_at}'. Expected ISO 8601 timestamp string.")

    # Message ID
    msg_id = data.get("msg_id")
    if not isinstance(msg_id, str) or not msg_id.strip():
        raise RelayValidationError("Envelope msg_id must be a non-empty string")

    # Encryption flags
    encrypted = data.get("encrypted")
    if not isinstance(encrypted, bool):
        raise RelayValidationError(f"Invalid encrypted flag: {encrypted}. Expected boolean.")

    cipher = data.get("cipher")
    if not isinstance(cipher, str):
        raise RelayValidationError(f"Invalid cipher '{cipher}'. Expected string.")

    iv = data.get("iv")
    if encrypted:
        if cipher.lower() == CIPHER_NONE:
            raise RelayValidationError("Encrypted envelope cannot have cipher 'none'")
        if not iv or not isinstance(iv, str):
            raise RelayValidationError("Encrypted envelope requires non-empty string 'iv'")
    else:
        if iv is not None and not isinstance(iv, str):
            raise RelayValidationError("Unencrypted envelope iv must be null or string")

    # Body
    body = data.get("body")
    if not isinstance(body, str):
        raise RelayValidationError("Envelope body must be a string")

    # Attachments
    attachments = data.get("attachments")
    if attachments is not None:
        if not isinstance(attachments, list):
            raise RelayValidationError("Envelope attachments must be a list")
        for idx, att in enumerate(attachments):
            if not isinstance(att, dict):
                raise RelayValidationError(f"Attachment at index {idx} must be a dictionary")
            size = att.get("size") if att.get("size") is not None else att.get("size_bytes")
            if size is not None:
                if not isinstance(size, int) or size < 0:
                    raise RelayValidationError(f"Attachment size must be a non-negative integer")
                if size > MAX_ATTACHMENT_BYTES:
                    raise RelayValidationError(
                        f"Attachment size {size} bytes exceeds maximum allowed {MAX_ATTACHMENT_BYTES} bytes"
                    )

    # Meta
    meta = data.get("meta")
    if meta is not None and not isinstance(meta, dict):
        raise RelayValidationError("Envelope meta must be a dictionary")

    return data


def create_envelope(
    room: str,
    sender: str,
    body: str,
    attachments: list[dict[str, Any]] | None = None,
    encrypted: bool = False,
    cipher: str = CIPHER_NONE,
    iv: str | None = None,
    meta: dict[str, Any] | None = None,
    nonce: str = "",
    created_at: str | None = None,
) -> dict[str, Any]:
    """Construct a canonical, validated message envelope dictionary.

    Computes deterministic SHA-256 msg_id and applies secret scrubbing to unencrypted bodies.
    """
    # Room normalization
    if room == "workers-room":
        room = "workers"
    if room not in VALID_ROOMS:
        raise RelayValidationError(f"Invalid room '{room}'. Must be one of {VALID_ROOMS}")

    # Sender validation
    if sender not in VALID_SENDERS:
        raise RelayValidationError(f"Invalid sender '{sender}'. Must be one of {VALID_SENDERS}")

    # Timestamp
    if created_at is None:
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Nonce
    if not nonce:
        nonce = secrets.token_hex(4)

    # Secret scrubbing for unencrypted envelopes
    cleaned_body = body if encrypted else scrub_secrets(body)

    # Message ID
    msg_id = compute_msg_id(room, sender, created_at, cleaned_body, nonce)

    # Meta
    meta_dict = dict(meta or {})
    meta_dict.setdefault("nonce", nonce)
    meta_dict.setdefault("source", "relay_protocol")

    envelope = {
        "version": 1,
        "msg_id": msg_id,
        "room": room,
        "sender": sender,
        "created_at": created_at,
        "encrypted": bool(encrypted),
        "cipher": str(cipher),
        "iv": iv,
        "body": cleaned_body,
        "attachments": list(attachments or []),
        "meta": meta_dict,
    }

    return validate_envelope(envelope)


def serialize_envelope(env: dict[str, Any], passphrase: str | None = None) -> str:
    """Serialize a message envelope dict to a formatted JSON string.

    If passphrase is provided, encrypts the envelope body using AES-256-GCM.
    """
    out_env = copy.deepcopy(env)
    if passphrase:
        if not out_env.get("encrypted"):
            enc = encrypt_payload(out_env.get("body", ""), passphrase)
            out_env["encrypted"] = True
            out_env["cipher"] = enc.get("cipher", CIPHER_AES_256_GCM)
            out_env["iv"] = enc["iv"]
            out_env["body"] = enc["ciphertext"]
            meta = out_env.setdefault("meta", {})
            meta["salt"] = enc["salt"]
            meta["iterations"] = enc["iterations"]

    validate_envelope(out_env)
    return json.dumps(out_env, indent=2, sort_keys=True)


def deserialize_envelope(raw_json: str, passphrase: str | None = None) -> dict[str, Any]:
    """Parse, validate, and optionally decrypt a message envelope from JSON string."""
    try:
        data = json.loads(raw_json)
    except Exception as err:
        raise RelayValidationError(f"Invalid JSON string: {err}") from err

    validate_envelope(data)

    if data.get("encrypted") and passphrase:
        plaintext = decrypt_payload(data, passphrase)
        data["body"] = plaintext
        data["encrypted"] = False
        data["cipher"] = CIPHER_NONE
        validate_envelope(data)

    return data


# ==============================================================================
# Web Command Parser (/web and !web)
# ==============================================================================

def parse_web_command(body: str) -> dict[str, Any] | None:
    """Detect and parse chat commands starting with /web or !web.

    Examples:
      - /web https://example.com
      - !web --mode=raw https://example.com/file.pdf
      - /web --mode=html https://example.com/page
      - /web [https://example.com](https://example.com)
      - /web https://example.com --max-bytes=1048576

    Returns:
      {
        "url": str,
        "mode": "markdown" | "html" | "file",
        "max_bytes": int,
        "raw_command": str
      }
      or None if the body is not a valid web command.
    """
    if not body or not isinstance(body, str):
        return None

    stripped = body.strip()
    match = re.match(r"^(?:/|!)web(?:\s+(.*)|$)", stripped, re.IGNORECASE | re.DOTALL)
    if not match:
        return None

    rest = match.group(1)
    if not rest or not rest.strip():
        return None
    rest = rest.strip()

    # Extract mode flag: --mode=(markdown|html|file|raw)
    mode = None
    mode_match = re.search(r"--mode=([a-zA-Z0-9_\-]+)", rest, re.IGNORECASE)
    if mode_match:
        m_str = mode_match.group(1).lower()
        if m_str == "raw":
            mode = "file"
        elif m_str in ("markdown", "html", "file"):
            mode = m_str
        rest = rest[:mode_match.start()] + " " + rest[mode_match.end():]

    # Extract max_bytes flag: --max-bytes=12345 or --max_bytes=12345 or --bytes=12345
    max_bytes = None
    bytes_match = re.search(r"--(?:max[\-_]bytes|bytes|max[\-_]size)=([0-9]+)", rest, re.IGNORECASE)
    if bytes_match:
        try:
            max_bytes = int(bytes_match.group(1))
        except (ValueError, TypeError):
            max_bytes = None
        rest = rest[:bytes_match.start()] + " " + rest[bytes_match.end():]

    # Extract URL from remaining tokens
    # 1. Markdown link: [text](url)
    md_link_match = re.search(r"\[([^\]]*)\]\((https?://[^\s\)]+)\)", rest)
    if md_link_match:
        url = md_link_match.group(2).strip()
    else:
        # 2. Angle brackets: <url>
        angle_match = re.search(r"<(https?://[^>]+)>", rest)
        if angle_match:
            url = angle_match.group(1).strip()
        else:
            # 3. Bare URL: http:// or https://
            bare_match = re.search(r"https?://[^\s]+", rest)
            if bare_match:
                url = bare_match.group(0).strip().rstrip(".,;:)]>")
            else:
                return None

    # Determine default mode based on file extensions if not specified
    if mode is None:
        file_exts = (
            ".pdf", ".zip", ".tar.gz", ".tgz", ".png", ".jpg",
            ".jpeg", ".csv", ".bin", ".mp4", ".mp3", ".docx",
            ".xlsx", ".tar", ".gz", ".7z",
        )
        url_lower = url.lower().split("?")[0].split("#")[0]
        if any(url_lower.endswith(ext) for ext in file_exts):
            mode = "file"
        else:
            mode = "markdown"

    # Default max_bytes
    if max_bytes is None:
        max_bytes = DEFAULT_WEB_MAX_BYTES_FILE if mode == "file" else DEFAULT_WEB_MAX_BYTES_TEXT

    return {
        "url": url,
        "mode": mode,
        "max_bytes": max_bytes,
        "raw_command": stripped,
    }
