"""tests/test_cluster_chat.py

Unit test suite for cluster_chat attachment storage and room persistence.
Tests schema compliance, validation, size limits, MIME detection, path-traversal blocking,
entropy of attachment_id, and message persistence across cluster rooms.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from cluster_chat.attachments import (
    STANDARD_ROOMS,
    AttachmentMetadata,
    AttachmentStorageManager,
    classify_attachment_kind,
    detect_mime_type,
    is_standard_room,
    is_valid_room_name,
    sanitize_filename,
    validate_room_name,
)
from cluster_chat.rooms import (
    RoomManager,
    RoomMessage,
)


class TestClusterChatAttachments(unittest.TestCase):
    """Test AttachmentStorageManager and attachment utility functions."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.upload_dir = Path(self.tmpdir.name) / "uploads"
        self.storage = AttachmentStorageManager(
            upload_dir=self.upload_dir,
            public_base_url="https://chat.example.com",
            max_upload_mb=5,
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_room_name_validation(self):
        # Standard rooms
        for r in STANDARD_ROOMS:
            self.assertTrue(is_standard_room(r))
            self.assertTrue(is_valid_room_name(r))
            self.assertEqual(validate_room_name(r), r)

        # Custom room
        self.assertFalse(is_standard_room("custom_project-123"))
        self.assertTrue(is_valid_room_name("custom_project-123"))
        self.assertEqual(validate_room_name("custom_project-123"), "custom_project-123")

        # Invalid rooms (path traversal, special chars, empty)
        self.assertFalse(is_valid_room_name(""))
        self.assertFalse(is_valid_room_name("   "))
        self.assertFalse(is_valid_room_name("../escaped"))
        self.assertFalse(is_valid_room_name("room/nested"))
        self.assertFalse(is_valid_room_name("room\\nested"))
        self.assertFalse(is_valid_room_name("room with spaces"))
        self.assertFalse(is_valid_room_name("room;drop"))

        with self.assertRaises(ValueError):
            validate_room_name("../etc")

    def test_sanitize_filename(self):
        self.assertEqual(sanitize_filename("valid_name.png"), "valid_name.png")
        self.assertEqual(sanitize_filename("my document (1).pdf"), "my_document_1_.pdf")
        self.assertEqual(sanitize_filename("../../etc/passwd"), "passwd")
        self.assertEqual(sanitize_filename("..\\..\\windows\\system32\\calc.exe"), "calc.exe")
        self.assertEqual(sanitize_filename("...hidden.png"), "hidden.png")
        self.assertEqual(sanitize_filename(""), "attachment.bin")
        self.assertEqual(sanitize_filename("   "), "attachment.bin")
        self.assertEqual(sanitize_filename("???###"), "attachment.bin")

        # Long extension handling
        long_ext_name = "test." + "a" * 150
        sanitized = sanitize_filename(long_ext_name)
        self.assertTrue(len(sanitized) <= 100)
        self.assertTrue(sanitized.startswith("test."))

    def test_mime_and_kind_classification(self):
        # Photos
        self.assertEqual(classify_attachment_kind("image/jpeg"), "photo")
        self.assertEqual(classify_attachment_kind("image/png"), "photo")
        self.assertEqual(classify_attachment_kind("image/webp"), "photo")
        self.assertEqual(classify_attachment_kind("image/gif"), "photo")

        # Documents
        self.assertEqual(classify_attachment_kind("application/pdf"), "document")
        self.assertEqual(classify_attachment_kind("text/plain"), "document")
        self.assertEqual(classify_attachment_kind("application/json"), "document")
        self.assertEqual(classify_attachment_kind("application/zip"), "document")
        self.assertEqual(classify_attachment_kind("text/x-python"), "document")

        # Detection
        self.assertEqual(detect_mime_type("photo.jpg"), "image/jpeg")
        self.assertEqual(detect_mime_type("img.png"), "image/png")
        self.assertEqual(detect_mime_type("doc.pdf"), "application/pdf")
        self.assertEqual(detect_mime_type("script.py"), "text/x-python")
        self.assertEqual(detect_mime_type("code.ts"), "application/typescript")
        self.assertEqual(detect_mime_type("unknown.xyz123"), "application/octet-stream")
        self.assertEqual(detect_mime_type("file.txt", "text/plain; charset=utf-8"), "text/plain")

    def test_save_bytes_all_standard_rooms_and_entropy(self):
        expected_fields = [
            "attachment_id",
            "kind",
            "filename",
            "mime_type",
            "size_bytes",
            "sha256",
            "room",
            "sender",
            "target_agent",
            "local_path",
            "public_url",
            "telegram_file_id",
            "created_at",
        ]

        ids = set()
        for room in STANDARD_ROOMS:
            img_content = b"\x89PNG\r\n\x1a\nphoto_data"
            photo_meta = self.storage.save_bytes(
                content=img_content,
                filename="preview.png",
                room=room,
                sender="mohammad",
                target_agent="argon",
                telegram_file_id="tg_photo_999",
            )
            for f in expected_fields:
                self.assertIn(f, photo_meta)

            self.assertEqual(photo_meta["kind"], "photo")
            self.assertEqual(photo_meta["room"], room)
            self.assertEqual(photo_meta["sender"], "mohammad")
            self.assertEqual(photo_meta["target_agent"], "argon")
            self.assertEqual(photo_meta["telegram_file_id"], "tg_photo_999")
            self.assertTrue(photo_meta["public_url"].startswith("https://chat.example.com/api/cluster-chat/files/"))
            self.assertTrue(Path(photo_meta["local_path"]).is_file())
            self.assertEqual(Path(photo_meta["local_path"]).read_bytes(), img_content)

            # Check 16-hex character entropy: 'att_' + 16 chars = 20 chars
            self.assertTrue(photo_meta["attachment_id"].startswith("att_"))
            self.assertEqual(len(photo_meta["attachment_id"]), 20)
            self.assertNotIn(photo_meta["attachment_id"], ids)
            ids.add(photo_meta["attachment_id"])

            # Save document
            doc_content = b"Task specification and report"
            doc_meta = self.storage.save_bytes(
                content=doc_content,
                filename="report.md",
                room=room,
                sender="argon",
                target_agent="cookie",
            )
            self.assertEqual(doc_meta["kind"], "document")
            self.assertEqual(doc_meta["room"], room)
            self.assertEqual(doc_meta["mime_type"], "text/markdown")
            self.assertTrue(Path(doc_meta["local_path"]).is_file())
            self.assertEqual(Path(doc_meta["local_path"]).read_bytes(), doc_content)
            ids.add(doc_meta["attachment_id"])

    def test_save_bytes_naive_and_aware_datetime(self):
        naive_dt = datetime(2026, 10, 28, 5, 30, 0)
        meta_naive = self.storage.save_bytes(
            content=b"test",
            filename="naive.txt",
            room="main",
            sender="mohammad",
            timestamp=naive_dt,
        )
        self.assertEqual(meta_naive["created_at"], "2026-10-28T05:30:00Z")

        aware_dt = datetime(2026, 10, 28, 7, 30, 0, tzinfo=timezone.utc)
        meta_aware = self.storage.save_bytes(
            content=b"test",
            filename="aware.txt",
            room="main",
            sender="mohammad",
            timestamp=aware_dt,
        )
        self.assertEqual(meta_aware["created_at"], "2026-10-28T07:30:00Z")

    def test_save_file_from_path(self):
        sample_file = Path(self.tmpdir.name) / "test_file.txt"
        sample_file.write_text("Hello from local file system")

        meta = self.storage.save_file_from_path(
            src_path=sample_file,
            room="dm-argon",
            sender="cookie",
            target_agent="argon",
        )
        self.assertEqual(meta["kind"], "document")
        self.assertEqual(meta["filename"], "test_file.txt")
        self.assertEqual(meta["size_bytes"], len("Hello from local file system"))
        self.assertTrue(Path(meta["local_path"]).is_file())

        # Non-existent file
        with self.assertRaises(FileNotFoundError):
            self.storage.save_file_from_path(
                src_path="/tmp/non_existent_file_abc123.bin",
                room="dm-argon",
                sender="cookie",
            )

        # Directory instead of regular file
        sub_dir = Path(self.tmpdir.name) / "sub_dir"
        sub_dir.mkdir()
        with self.assertRaises(ValueError):
            self.storage.save_file_from_path(
                src_path=sub_dir,
                room="dm-argon",
                sender="cookie",
            )

    def test_max_upload_size_enforced(self):
        storage_small = AttachmentStorageManager(
            upload_dir=self.upload_dir,
            max_upload_mb=0.001,  # ~1 KB
        )
        oversized = b"A" * 2000
        with self.assertRaises(ValueError) as ctx:
            storage_small.save_bytes(
                content=oversized,
                filename="big.dat",
                room="main",
                sender="mohammad",
            )
        self.assertIn("exceeds maximum upload limit", str(ctx.exception))

    def test_resolve_safe_file_path_security(self):
        meta = self.storage.save_bytes(
            content=b"test safe data",
            filename="safe.txt",
            room="dm-argon",
            sender="argon",
        )
        date_str = meta["created_at"][:10]
        stored_filename = Path(meta["local_path"]).name

        resolved = self.storage.resolve_safe_file_path(
            room="dm-argon",
            date_str=date_str,
            stored_filename=stored_filename,
            must_exist=True,
        )
        self.assertEqual(resolved, Path(meta["local_path"]))

        # Traversal attempts
        traversal_attempts = [
            "../../etc/passwd",
            "..\\..\\windows\\system32",
            "../other_file",
            "file/with/slash",
            "/absolute/path",
        ]
        for bad_filename in traversal_attempts:
            with self.assertRaises(ValueError):
                self.storage.resolve_safe_file_path("dm-argon", date_str, bad_filename)

        with self.assertRaises(ValueError):
            self.storage.resolve_safe_file_path("../escape", date_str, stored_filename)

        with self.assertRaises(ValueError):
            self.storage.resolve_safe_file_path("dm-argon", "invalid-date", stored_filename)

        with self.assertRaises(FileNotFoundError):
            self.storage.resolve_safe_file_path(
                "dm-argon", date_str, "nonexistent_file.txt", must_exist=True
            )


class TestClusterChatRooms(unittest.TestCase):
    """Test RoomManager and RoomMessage persistence across cluster rooms."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmpdir.name) / "cluster_data"
        self.upload_dir = Path(self.tmpdir.name) / "uploads"
        self.room_mgr = RoomManager(data_dir=self.data_dir)
        self.storage = AttachmentStorageManager(upload_dir=self.upload_dir)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_post_and_get_messages(self):
        room = "workers-room"

        msg1 = self.room_mgr.post_message(
            room=room,
            sender="mohammad",
            text="Worker team status check",
        )
        self.assertEqual(msg1["room"], room)
        self.assertEqual(msg1["sender"], "mohammad")
        self.assertEqual(msg1["text"], "Worker team status check")
        self.assertEqual(msg1["attachments"], [])
        self.assertTrue(msg1["message_id"].startswith("msg_"))

        att = self.storage.save_bytes(
            content=b"patch content",
            filename="fix.patch",
            room=room,
            sender="argon",
            target_agent="cookie",
        )
        msg2 = self.room_mgr.post_message(
            room=room,
            sender="argon",
            text="Here is the patch",
            attachments=[att],
            target_agent="cookie",
        )
        self.assertEqual(len(msg2["attachments"]), 1)
        self.assertEqual(msg2["attachments"][0]["filename"], "fix.patch")
        self.assertEqual(msg2["target_agent"], "cookie")

        messages = self.room_mgr.get_messages(room=room)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["message_id"], msg1["message_id"])
        self.assertEqual(messages[1]["message_id"], msg2["message_id"])

        found = self.room_mgr.get_message_by_id(room, msg2["message_id"])
        self.assertIsNotNone(found)
        self.assertEqual(found["message_id"], msg2["message_id"])
        self.assertEqual(found["sender"], "argon")

        self.assertIsNone(self.room_mgr.get_message_by_id(room, "msg_non_existent"))

    def test_target_agent_filtering(self):
        room = "dm-argon"
        self.room_mgr.post_message(room=room, sender="mohammad", text="Msg for all", target_agent=None)
        self.room_mgr.post_message(room=room, sender="mohammad", text="Msg for argon", target_agent="argon")
        self.room_mgr.post_message(room=room, sender="mohammad", text="Msg for cookie", target_agent="cookie")

        argon_msgs = self.room_mgr.get_messages(room=room, target_agent="argon")
        self.assertEqual(len(argon_msgs), 2)
        self.assertEqual([m["text"] for m in argon_msgs], ["Msg for all", "Msg for argon"])

    def test_empty_message_validation(self):
        with self.assertRaises(ValueError):
            self.room_mgr.post_message(room="main", sender="mohammad", text="", attachments=[])

        with self.assertRaises(ValueError):
            self.room_mgr.post_message(room="main", sender="", text="Valid text")


if __name__ == "__main__":
    unittest.main()
