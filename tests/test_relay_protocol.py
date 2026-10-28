"""tests/test_relay_protocol.py

Comprehensive unit tests for cluster_chat.relay_protocol.
Covers deterministic msg_id computation, secret scrubbing, AES-256-GCM encryption/decryption,
envelope validation and serialization, and /web command parsing.
"""

from __future__ import annotations

import base64
import json
import unittest

from cluster_chat.relay_protocol import (
    CIPHER_AES_256_GCM,
    CIPHER_NONE,
    CIPHER_STDLIB_FALLBACK,
    DEFAULT_WEB_MAX_BYTES_FILE,
    DEFAULT_WEB_MAX_BYTES_TEXT,
    MAX_ATTACHMENT_BYTES,
    VALID_ROOMS,
    VALID_SENDERS,
    MessageEnvelope,
    RelayCryptoError,
    RelayValidationError,
    _stdlib_decrypt_payload,
    _stdlib_encrypt_payload,
    compute_msg_id,
    create_envelope,
    decrypt_payload,
    deserialize_envelope,
    encrypt_payload,
    parse_web_command,
    scrub_secrets,
    serialize_envelope,
    validate_envelope,
)


class TestComputeMsgId(unittest.TestCase):
    """Test deterministic compute_msg_id across rooms, senders, and content."""

    def test_deterministic_same_inputs(self):
        msg_id1 = compute_msg_id("main", "mohammad", "2026-10-28T08:00:00Z", "Hello", nonce="abc1")
        msg_id2 = compute_msg_id("main", "mohammad", "2026-10-28T08:00:00Z", "Hello", nonce="abc1")
        self.assertEqual(msg_id1, msg_id2)
        self.assertEqual(len(msg_id1), 64)  # SHA-256 hex digest

    def test_rooms_variety(self):
        for room in ("main", "dm-argon", "workers"):
            mid = compute_msg_id(room, "argon", "2026-10-28T08:00:00Z", "Status update", nonce="123")
            self.assertTrue(isinstance(mid, str) and len(mid) == 64)

    def test_workers_room_normalization(self):
        mid_normalized = compute_msg_id("workers-room", "worker", "2026-10-28T08:00:00Z", "Ping", nonce="n1")
        mid_direct = compute_msg_id("workers", "worker", "2026-10-28T08:00:00Z", "Ping", nonce="n1")
        self.assertEqual(mid_normalized, mid_direct)

    def test_different_inputs_produce_different_ids(self):
        base = compute_msg_id("main", "mohammad", "2026-10-28T08:00:00Z", "Text", nonce="1")
        diff_room = compute_msg_id("dm-argon", "mohammad", "2026-10-28T08:00:00Z", "Text", nonce="1")
        diff_sender = compute_msg_id("main", "argon", "2026-10-28T08:00:00Z", "Text", nonce="1")
        diff_ts = compute_msg_id("main", "mohammad", "2026-10-28T08:01:00Z", "Text", nonce="1")
        diff_body = compute_msg_id("main", "mohammad", "2026-10-28T08:00:00Z", "Different Text", nonce="1")
        diff_nonce = compute_msg_id("main", "mohammad", "2026-10-28T08:00:00Z", "Text", nonce="2")

        unique_ids = {base, diff_room, diff_sender, diff_ts, diff_body, diff_nonce}
        self.assertEqual(len(unique_ids), 6)

    def test_invalid_room_or_sender_raises(self):
        with self.assertRaises(RelayValidationError):
            compute_msg_id("", "mohammad", "2026-10-28T08:00:00Z", "text")
        with self.assertRaises(RelayValidationError):
            compute_msg_id("   ", "mohammad", "2026-10-28T08:00:00Z", "text")
        with self.assertRaises(RelayValidationError):
            compute_msg_id("main", "", "2026-10-28T08:00:00Z", "text")


class TestSecretScrubbing(unittest.TestCase):
    """Test scrub_secrets against realistic tokens, credentials, and keys."""

    def test_github_pat_classic(self):
        raw = "My classic token is ghp_1234567890abcdef1234567890ABCDEF."
        sanitized = scrub_secrets(raw)
        self.assertNotIn("ghp_", sanitized)
        self.assertIn("[REDACTED_SECRET]", sanitized)

    def test_github_pat_fine_grained(self):
        raw = "PAT: github_pat_11AAAAAAA0000000000000_12345678901234567890abcdefghijklmnopqrstuv"
        sanitized = scrub_secrets(raw)
        self.assertNotIn("github_pat_", sanitized)
        self.assertEqual(sanitized, "PAT: [REDACTED_SECRET]")

    def test_github_app_tokens(self):
        for prefix in ("gho_", "ghu_", "ghs_", "ghr_"):
            token = f"{prefix}1234567890abcdef1234567890123456"
            sanitized = scrub_secrets(f"Using {token} for access")
            self.assertNotIn(prefix, sanitized)
            self.assertIn("[REDACTED_SECRET]", sanitized)

    def test_gemini_api_key(self):
        raw = "Google key is AIzaSyD1234567890abcdefghijklmnopqrstuv"
        sanitized = scrub_secrets(raw)
        self.assertNotIn("AIzaSyD", sanitized)
        self.assertEqual(sanitized, "Google key is [REDACTED_SECRET]")

    def test_telegram_bot_token(self):
        token_secret = "A" * 35
        raw = f"Bot token: 123456789:{token_secret} in config"
        sanitized = scrub_secrets(raw)
        self.assertNotIn("123456789:", sanitized)
        self.assertIn("[REDACTED_SECRET]", sanitized)

    def test_bearer_header(self):
        raw1 = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token"
        raw2 = "bearer: some_random_secret_token_value_here"
        self.assertIn("[REDACTED_SECRET]", scrub_secrets(raw1))
        self.assertNotIn("eyJhbGci", scrub_secrets(raw1))
        self.assertIn("[REDACTED_SECRET]", scrub_secrets(raw2))

    def test_pem_private_keys(self):
        rsa_pem = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEA0Y1+examplekeydatahere...\n"
            "-----END RSA PRIVATE KEY-----"
        )
        generic_pem = (
            "-----BEGIN PRIVATE KEY-----\n"
            "MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQC...\n"
            "-----END PRIVATE KEY-----"
        )
        self.assertEqual(scrub_secrets(rsa_pem), "[REDACTED_SECRET]")
        self.assertEqual(scrub_secrets(generic_pem), "[REDACTED_SECRET]")

    def test_openai_and_aws_keys(self):
        openai_key = "sk-1234567890abcdef1234567890abcdef"
        aws_key = "AKIAIOSFODNN7EXAMPLE"
        self.assertEqual(scrub_secrets(f"Key: {openai_key}"), "Key: [REDACTED_SECRET]")
        self.assertEqual(scrub_secrets(f"AWS: {aws_key}"), "AWS: [REDACTED_SECRET]")

    def test_benign_text_preserved(self):
        text = "سلام آرگون! لطفاً وضعیت رله مخزن agw-workers را بررسی کنید. همه سرویس‌ها فعال هستند."
        self.assertEqual(scrub_secrets(text), text)

    def test_none_and_empty(self):
        self.assertEqual(scrub_secrets(""), "")
        self.assertEqual(scrub_secrets(None), "")


class TestRelayCryptography(unittest.TestCase):
    """Test AES-256-GCM encryption, decryption, PBKDF2 derivation, and fallback cipher."""

    def test_encrypt_decrypt_roundtrip_ascii(self):
        passphrase = "LeadDesk-Master-Key-2026!"
        plaintext = "Status report: All 6 worker agents operating normally."
        enc = encrypt_payload(plaintext, passphrase)

        self.assertIn("salt", enc)
        self.assertIn("iv", enc)
        self.assertIn("ciphertext", enc)
        self.assertIn(enc["cipher"], (CIPHER_AES_256_GCM, CIPHER_STDLIB_FALLBACK))

        dec = decrypt_payload(enc, passphrase)
        self.assertEqual(dec, plaintext)

    def test_encrypt_decrypt_roundtrip_persian_unicode(self):
        passphrase = "کلید-رمزنگاری-کلاستر-چت-۱۴۰۵"
        plaintext = "سلام محمد! پیام رله شده با رمزنگاری سرتاسری و کاراکترهای خاص: ⚡️🚀🔒\nخط دوم تست."
        enc = encrypt_payload(plaintext, passphrase)
        dec = decrypt_payload(enc, passphrase)
        self.assertEqual(dec, plaintext)

    def test_wrong_passphrase_fails(self):
        passphrase = "correct-passphrase-123"
        wrong_passphrase = "wrong-passphrase-456"
        enc = encrypt_payload("Confidential payload", passphrase)
        with self.assertRaises(RelayCryptoError):
            decrypt_payload(enc, wrong_passphrase)

    def test_corrupted_ciphertext_fails(self):
        passphrase = "password123"
        enc = encrypt_payload("Sensitive message", passphrase)
        raw_ct = base64.b64decode(enc["ciphertext"])
        corrupted = bytearray(raw_ct)
        corrupted[5] ^= 0xFF
        enc["ciphertext"] = base64.b64encode(corrupted).decode("ascii")

        with self.assertRaises(RelayCryptoError):
            decrypt_payload(enc, passphrase)

    def test_short_ciphertext_fails(self):
        passphrase = "password123"
        short_enc = {
            "salt": base64.b64encode(b"0" * 16).decode("ascii"),
            "iv": base64.b64encode(b"0" * 12).decode("ascii"),
            "ciphertext": base64.b64encode(b"short").decode("ascii"),
            "cipher": CIPHER_STDLIB_FALLBACK,
        }
        with self.assertRaises(RelayCryptoError):
            decrypt_payload(short_enc, passphrase)

    def test_missing_keys_in_payload_raises(self):
        with self.assertRaises(RelayCryptoError):
            decrypt_payload("not-a-dict", "pass")
        with self.assertRaises(RelayCryptoError):
            decrypt_payload({}, "pass")
        with self.assertRaises(RelayCryptoError):
            decrypt_payload({"iv": "iv", "ciphertext": "ct"}, "pass")  # missing salt
        with self.assertRaises(RelayCryptoError):
            decrypt_payload({"salt": "salt", "ciphertext": "ct"}, "pass")  # missing iv
        with self.assertRaises(RelayCryptoError):
            decrypt_payload({"salt": "salt", "iv": "iv"}, "pass")  # missing ciphertext

    def test_empty_passphrase_raises(self):
        with self.assertRaises(RelayCryptoError):
            encrypt_payload("data", "")
        with self.assertRaises(RelayCryptoError):
            decrypt_payload({"salt": "s", "iv": "i", "ciphertext": "c"}, "")

    def test_stdlib_fallback_direct(self):
        passphrase = "fallback-test-secret"
        text = "Testing pure standard library fallback stream cipher."
        enc = _stdlib_encrypt_payload(text, passphrase, iterations=1000)
        salt_bytes = base64.b64decode(enc["salt"])
        iv_bytes = base64.b64decode(enc["iv"])
        raw_ct = base64.b64decode(enc["ciphertext"])

        dec = _stdlib_decrypt_payload(salt_bytes, iv_bytes, raw_ct, passphrase, iterations=1000)
        self.assertEqual(dec, text)

        with self.assertRaises(RelayCryptoError):
            _stdlib_decrypt_payload(salt_bytes, iv_bytes, raw_ct, "bad-key", iterations=1000)


class TestMessageEnvelope(unittest.TestCase):
    """Test MessageEnvelope creation, validation, and JSON serialization."""

    def test_create_envelope_basic(self):
        env = create_envelope(
            room="main",
            sender="mohammad",
            body="Hello cluster!",
            created_at="2026-10-28T08:00:00Z",
            nonce="test_nonce",
        )
        self.assertEqual(env["version"], 1)
        self.assertEqual(env["room"], "main")
        self.assertEqual(env["sender"], "mohammad")
        self.assertEqual(env["body"], "Hello cluster!")
        self.assertFalse(env["encrypted"])
        self.assertEqual(env["cipher"], CIPHER_NONE)
        self.assertTrue(len(env["msg_id"]) == 64)

    def test_create_envelope_scrubs_secrets_by_default(self):
        env = create_envelope(
            room="dm-argon",
            sender="mohammad",
            body="Here is token: ghp_1234567890abcdef1234567890123456",
            created_at="2026-10-28T08:00:00Z",
        )
        self.assertNotIn("ghp_", env["body"])
        self.assertIn("[REDACTED_SECRET]", env["body"])

    def test_workers_room_normalization(self):
        env = create_envelope(room="workers-room", sender="worker", body="Test")
        self.assertEqual(env["room"], "workers")

    def test_validation_errors(self):
        base = {
            "version": 1,
            "msg_id": "sha256hex1234",
            "room": "main",
            "sender": "mohammad",
            "created_at": "2026-10-28T08:00:00Z",
            "encrypted": False,
            "cipher": CIPHER_NONE,
            "iv": None,
            "body": "Hi",
            "attachments": [],
            "meta": {},
        }

        # Non-dict
        with self.assertRaises(RelayValidationError):
            validate_envelope("not a dict")

        # Invalid version
        bad_ver = dict(base, version=0)
        with self.assertRaises(RelayValidationError):
            validate_envelope(bad_ver)

        # Invalid room
        bad_room = dict(base, room="unknown-room")
        with self.assertRaises(RelayValidationError):
            validate_envelope(bad_room)

        # Invalid sender
        bad_sender = dict(base, sender="hacker")
        with self.assertRaises(RelayValidationError):
            validate_envelope(bad_sender)

        # Invalid timestamp
        bad_ts = dict(base, created_at="yesterday")
        with self.assertRaises(RelayValidationError):
            validate_envelope(bad_ts)

        # Empty msg_id
        bad_id = dict(base, msg_id="")
        with self.assertRaises(RelayValidationError):
            validate_envelope(bad_id)

        # Encrypted without cipher or iv
        bad_enc1 = dict(base, encrypted=True, cipher=CIPHER_NONE)
        with self.assertRaises(RelayValidationError):
            validate_envelope(bad_enc1)
        bad_enc2 = dict(base, encrypted=True, cipher=CIPHER_AES_256_GCM, iv=None)
        with self.assertRaises(RelayValidationError):
            validate_envelope(bad_enc2)

        # Attachment exceeds max size
        huge_att = [{"id": "att_1", "name": "big.bin", "size": MAX_ATTACHMENT_BYTES + 100}]
        bad_att = dict(base, attachments=huge_att)
        with self.assertRaises(RelayValidationError):
            validate_envelope(bad_att)

    def test_serialization_and_deserialization_unencrypted(self):
        env = create_envelope(room="workers", sender="argon", body="Job queued")
        json_str = serialize_envelope(env)
        recovered = deserialize_envelope(json_str)
        self.assertEqual(recovered["msg_id"], env["msg_id"])
        self.assertEqual(recovered["body"], "Job queued")
        self.assertFalse(recovered["encrypted"])

    def test_serialization_and_deserialization_encrypted(self):
        passphrase = "relay-secret-passphrase"
        env = create_envelope(room="dm-argon", sender="mohammad", body="Secret briefing for Argon")
        json_str = serialize_envelope(env, passphrase=passphrase)

        # In serialized form, body should be ciphertext and encrypted=True
        wire_data = json.loads(json_str)
        self.assertTrue(wire_data["encrypted"])
        self.assertNotEqual(wire_data["body"], "Secret briefing for Argon")
        self.assertIn("salt", wire_data["meta"])

        # Deserializing without passphrase returns encrypted envelope
        raw_recovered = deserialize_envelope(json_str)
        self.assertTrue(raw_recovered["encrypted"])

        # Deserializing with passphrase restores plaintext body and encrypted=False
        decrypted_recovered = deserialize_envelope(json_str, passphrase=passphrase)
        self.assertFalse(decrypted_recovered["encrypted"])
        self.assertEqual(decrypted_recovered["body"], "Secret briefing for Argon")

    def test_dataclass_methods(self):
        env_dict = create_envelope(room="main", sender="mohammad", body="Dataclass check")
        obj = MessageEnvelope.from_dict(env_dict)
        self.assertEqual(obj.room, "main")
        self.assertEqual(obj.sender, "mohammad")
        self.assertEqual(obj.body, "Dataclass check")
        self.assertEqual(obj.to_dict()["msg_id"], env_dict["msg_id"])


class TestParseWebCommand(unittest.TestCase):
    """Test parse_web_command on standard, Markdown, and custom web commands."""

    def test_standard_web_url(self):
        res = parse_web_command("/web https://example.com")
        self.assertIsNotNone(res)
        self.assertEqual(res["url"], "https://example.com")
        self.assertEqual(res["mode"], "markdown")
        self.assertEqual(res["max_bytes"], DEFAULT_WEB_MAX_BYTES_TEXT)

    def test_exclamation_prefix(self):
        res = parse_web_command("!web https://example.com/status")
        self.assertIsNotNone(res)
        self.assertEqual(res["url"], "https://example.com/status")
        self.assertEqual(res["mode"], "markdown")

    def test_markdown_link_format(self):
        res = parse_web_command("/web [Example Site](https://example.com)")
        self.assertIsNotNone(res)
        self.assertEqual(res["url"], "https://example.com")

    def test_angle_brackets_format(self):
        res = parse_web_command("/web <https://example.com/api/docs>")
        self.assertIsNotNone(res)
        self.assertEqual(res["url"], "https://example.com/api/docs")

    def test_mode_and_max_bytes_flags(self):
        res1 = parse_web_command("/web --mode=file https://example.com/doc.pdf")
        self.assertIsNotNone(res1)
        self.assertEqual(res1["mode"], "file")
        self.assertEqual(res1["max_bytes"], DEFAULT_WEB_MAX_BYTES_FILE)

        res2 = parse_web_command("/web --mode=html --max-bytes=100000 https://example.com")
        self.assertIsNotNone(res2)
        self.assertEqual(res2["mode"], "html")
        self.assertEqual(res2["max_bytes"], 100000)

        # raw mode mapped to file
        res3 = parse_web_command("/web --mode=raw https://example.com/archive.tar.gz")
        self.assertIsNotNone(res3)
        self.assertEqual(res3["mode"], "file")

    def test_auto_detect_file_mode_from_extension(self):
        for ext in (".pdf", ".zip", ".tar.gz", ".png", ".bin"):
            res = parse_web_command(f"/web https://example.com/download{ext}")
            self.assertIsNotNone(res)
            self.assertEqual(res["mode"], "file")
            self.assertEqual(res["max_bytes"], DEFAULT_WEB_MAX_BYTES_FILE)

    def test_non_commands_return_none(self):
        self.assertIsNone(parse_web_command(None))
        self.assertIsNone(parse_web_command(""))
        self.assertIsNone(parse_web_command("   "))
        self.assertIsNone(parse_web_command("hello world"))
        self.assertIsNone(parse_web_command("Check this out: https://example.com"))
        self.assertIsNone(parse_web_command("we should discuss /web in the meeting"))
        self.assertIsNone(parse_web_command("/web"))
        self.assertIsNone(parse_web_command("/web   "))
        self.assertIsNone(parse_web_command("/website https://example.com"))


if __name__ == "__main__":
    unittest.main()
