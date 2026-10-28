"""cluster_chat/attachments.py

Attachment Storage & Metadata Manager for the Cluster Chat bridge.
Supports storing photos and documents across cluster rooms (main, dm-argon, workers-room)
with complete metadata tracking, path-traversal protection, and safe URLs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import mimetypes
import os
from pathlib import Path
import re
from typing import Any
import uuid

# Configuration defaults
DEFAULT_UPLOAD_DIR = "./data/cluster-chat/uploads"
DEFAULT_PUBLIC_BASE_URL = ""
DEFAULT_MAX_UPLOAD_MB = 50

# Core cluster rooms
STANDARD_ROOMS: tuple[str, ...] = ("main", "dm-argon", "workers-room")

# Photo MIME types
PHOTO_MIME_TYPES: frozenset[str] = frozenset({
    "image/jpeg", "image/png", "image/webp", "image/gif"
})

ROOM_SLUG_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
SAFE_FILENAME_REGEX = re.compile(r"^[a-zA-Z0-9._-]+$")
DATE_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}$")

EXTENSION_MIME_MAP: dict[str, str] = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif", ".pdf": "application/pdf",
    ".txt": "text/plain", ".md": "text/markdown", ".json": "application/json",
    ".csv": "text/csv", ".zip": "application/zip", ".log": "text/plain",
    ".py": "text/x-python", ".js": "application/javascript",
    ".ts": "application/typescript", ".html": "text/html", ".css": "text/css",
    ".xml": "application/xml", ".yaml": "application/x-yaml",
    ".yml": "application/x-yaml", ".tar": "application/x-tar",
    ".gz": "application/gzip", ".tgz": "application/gzip",
    ".patch": "text/x-diff", ".diff": "text/x-diff",
}


def is_standard_room(room: str) -> bool:
    """Check if room is one of the standard cluster rooms."""
    return isinstance(room, str) and room.strip() in STANDARD_ROOMS


def is_valid_room_name(room: str) -> bool:
    """Validate room name: must be a standard room or a safe 1-64 char slug."""
    if not isinstance(room, str):
        return False
    room_clean = room.strip()
    return bool(room_clean) and (room_clean in STANDARD_ROOMS or bool(ROOM_SLUG_REGEX.match(room_clean)))


def validate_room_name(room: str) -> str:
    """Validate room name, raising ValueError if invalid."""
    if not is_valid_room_name(room):
        raise ValueError(
            f"Invalid room name '{room}'. Must be one of standard rooms {STANDARD_ROOMS} "
            "or a 1-64 character slug matching [a-zA-Z0-9_-]."
        )
    return room.strip()


def sanitize_filename(filename: str | None) -> str:
    """Sanitize filename by stripping directory parts, null bytes, and unsafe characters."""
    if not filename:
        return "attachment.bin"

    cleaned = filename.replace("\0", "").replace("\\", "/")
    base_name = os.path.basename(cleaned).strip()
    base_name = re.sub(r"^\.+", "", base_name).strip()
    sanitized = re.sub(r"[^a-zA-Z0-9._-]", "_", base_name)
    sanitized = re.sub(r"_+", "_", sanitized)

    if not sanitized or not re.search(r"[a-zA-Z0-9]", sanitized):
        return "attachment.bin"

    if len(sanitized) > 100:
        ext = Path(sanitized).suffix
        if len(ext) > 20:
            ext = ext[:20]
        stem = Path(sanitized).stem
        max_stem = max(1, 100 - len(ext))
        sanitized = f"{stem[:max_stem]}{ext}"

    return sanitized


def detect_mime_type(filename: str, override_mime: str | None = None) -> str:
    """Detect MIME type from override, file extension, or standard mimetypes."""
    if override_mime and override_mime.strip():
        clean_override = override_mime.strip().split(";")[0].strip().lower()
        if "/" in clean_override:
            return clean_override

    ext = Path(filename).suffix.lower()
    if ext in EXTENSION_MIME_MAP:
        return EXTENSION_MIME_MAP[ext]

    guessed, _ = mimetypes.guess_type(filename)
    return guessed.lower() if guessed else "application/octet-stream"


def classify_attachment_kind(mime_type: str) -> str:
    """Classify attachment as 'photo' or 'document' based on MIME type."""
    normalized = (mime_type or "").strip().lower()
    return "photo" if normalized in PHOTO_MIME_TYPES else "document"


@dataclass
class AttachmentMetadata:
    """Metadata schema for room message attachments."""

    attachment_id: str
    kind: str  # "photo" | "document"
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    room: str
    sender: str
    target_agent: str | None = None
    local_path: str = ""
    public_url: str = ""
    telegram_file_id: str | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "attachment_id": self.attachment_id,
            "kind": self.kind,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "room": self.room,
            "sender": self.sender,
            "target_agent": self.target_agent,
            "local_path": self.local_path,
            "public_url": self.public_url,
            "telegram_file_id": self.telegram_file_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AttachmentMetadata":
        return cls(
            attachment_id=str(data["attachment_id"]),
            kind=str(data["kind"]),
            filename=str(data["filename"]),
            mime_type=str(data["mime_type"]),
            size_bytes=int(data["size_bytes"]),
            sha256=str(data["sha256"]),
            room=str(data["room"]),
            sender=str(data["sender"]),
            target_agent=data.get("target_agent"),
            local_path=str(data.get("local_path", "")),
            public_url=str(data.get("public_url", "")),
            telegram_file_id=data.get("telegram_file_id"),
            created_at=str(data.get("created_at", "")),
        )


class AttachmentStorageManager:
    """Handles file storage, metadata generation, and path resolution for cluster chat."""

    def __init__(
        self,
        upload_dir: str | Path | None = None,
        public_base_url: str | None = None,
        max_upload_mb: int | float | None = None,
    ) -> None:
        raw_upload_dir = (
            upload_dir
            if upload_dir is not None
            else os.environ.get("CLUSTER_CHAT_UPLOAD_DIR", DEFAULT_UPLOAD_DIR)
        )
        self.upload_dir: Path = Path(raw_upload_dir).resolve()

        raw_base_url = (
            public_base_url
            if public_base_url is not None
            else os.environ.get("CLUSTER_CHAT_PUBLIC_BASE_URL", DEFAULT_PUBLIC_BASE_URL)
        )
        self.public_base_url: str = raw_base_url.rstrip("/")

        raw_max_mb = (
            max_upload_mb
            if max_upload_mb is not None
            else os.environ.get("CLUSTER_CHAT_MAX_UPLOAD_MB", DEFAULT_MAX_UPLOAD_MB)
        )
        self.max_upload_mb: float = float(raw_max_mb)
        self.max_bytes: int = int(self.max_upload_mb * 1024 * 1024)
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    def save_bytes(
        self,
        content: bytes,
        filename: str,
        room: str,
        sender: str,
        mime_type: str | None = None,
        target_agent: str | None = None,
        telegram_file_id: str | None = None,
        timestamp: datetime | None = None,
    ) -> dict[str, Any]:
        """Save file bytes into room-partitioned storage and return attachment metadata."""
        if not isinstance(content, (bytes, bytearray)):
            raise TypeError(f"content must be bytes or bytearray, got {type(content).__name__}")

        validated_room = validate_room_name(room)
        if not sender or not str(sender).strip():
            raise ValueError("sender must be a non-empty string")
        sender_clean = str(sender).strip()

        size_bytes = len(content)
        if size_bytes > self.max_bytes:
            raise ValueError(
                f"File size ({size_bytes} bytes) exceeds maximum upload limit of "
                f"{self.max_upload_mb} MB ({self.max_bytes} bytes)"
            )

        sha256_hash = hashlib.sha256(content).hexdigest()
        sanitized_fn = sanitize_filename(filename)
        resolved_mime = detect_mime_type(sanitized_fn, mime_type)
        kind = classify_attachment_kind(resolved_mime)

        if timestamp is not None:
            now = timestamp.replace(tzinfo=timezone.utc) if timestamp.tzinfo is None else timestamp.astimezone(timezone.utc)
        else:
            now = datetime.now(timezone.utc)

        iso_created_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        date_str = now.strftime("%Y-%m-%d")
        unix_ts = int(now.timestamp())

        stored_filename = f"{unix_ts}_{sha256_hash[:8]}_{sanitized_fn}"
        target_dir = self.upload_dir / validated_room / date_str
        target_dir.mkdir(parents=True, exist_ok=True)

        target_path = (target_dir / stored_filename).resolve()
        if not target_path.is_relative_to(self.upload_dir):
            raise ValueError(f"Path traversal detected: {target_path} is outside {self.upload_dir}")

        target_path.write_bytes(content)

        url_path = f"/api/cluster-chat/files/{validated_room}/{date_str}/{stored_filename}"
        public_url = f"{self.public_base_url}{url_path}" if self.public_base_url else url_path

        # 64-bit entropy attachment ID
        attachment_id = f"att_{uuid.uuid4().hex[:16]}"

        metadata = AttachmentMetadata(
            attachment_id=attachment_id,
            kind=kind,
            filename=sanitized_fn,
            mime_type=resolved_mime,
            size_bytes=size_bytes,
            sha256=sha256_hash,
            room=validated_room,
            sender=sender_clean,
            target_agent=target_agent.strip() if target_agent else None,
            local_path=str(target_path),
            public_url=public_url,
            telegram_file_id=telegram_file_id.strip() if telegram_file_id else None,
            created_at=iso_created_at,
        )

        return metadata.to_dict()

    def save_file_from_path(
        self,
        src_path: str | Path,
        room: str,
        sender: str,
        filename: str | None = None,
        target_agent: str | None = None,
        mime_type: str | None = None,
        telegram_file_id: str | None = None,
    ) -> dict[str, Any]:
        """Save a local file into attachment store, verifying size and existence before reading."""
        source = Path(src_path).resolve()
        if not source.exists():
            raise FileNotFoundError(f"Source file does not exist: {src_path}")
        if not source.is_file():
            raise ValueError(f"Source path is not a regular file: {src_path}")

        file_size = source.stat().st_size
        if file_size > self.max_bytes:
            raise ValueError(
                f"File size ({file_size} bytes) exceeds maximum upload limit of "
                f"{self.max_upload_mb} MB ({self.max_bytes} bytes)"
            )

        chosen_filename = filename.strip() if filename and filename.strip() else source.name
        content = source.read_bytes()

        return self.save_bytes(
            content=content,
            filename=chosen_filename,
            room=room,
            sender=sender,
            mime_type=mime_type,
            target_agent=target_agent,
            telegram_file_id=telegram_file_id,
        )

    def resolve_safe_file_path(
        self,
        room: str,
        date_str: str,
        stored_filename: str,
        must_exist: bool = False,
    ) -> Path:
        """Safely resolve an attachment path on disk with strict directory containment check."""
        validated_room = validate_room_name(room)

        if not isinstance(date_str, str) or not DATE_REGEX.match(date_str.strip()):
            raise ValueError(f"Invalid date_str '{date_str}'. Expected ISO date format YYYY-MM-DD.")
        date_clean = date_str.strip()

        if not isinstance(stored_filename, str) or not stored_filename.strip():
            raise ValueError("stored_filename cannot be empty")
        filename_clean = stored_filename.strip()

        if "/" in filename_clean or "\\" in filename_clean or "\0" in filename_clean or ".." in filename_clean:
            raise ValueError(f"Invalid stored_filename '{stored_filename}'. Path separators and traversal sequences are forbidden.")

        if not SAFE_FILENAME_REGEX.match(filename_clean):
            raise ValueError(f"Invalid stored_filename '{stored_filename}'. Contains forbidden characters.")

        target_path = (self.upload_dir / validated_room / date_clean / filename_clean).resolve()
        base_dir = self.upload_dir.resolve()
        room_dir = (self.upload_dir / validated_room).resolve()

        if not target_path.is_relative_to(base_dir) or not target_path.is_relative_to(room_dir):
            raise ValueError(f"Path traversal detected: resolved path '{target_path}' is outside upload base directory.")

        if must_exist and not target_path.is_file():
            raise FileNotFoundError(f"Attachment file not found: {validated_room}/{date_clean}/{filename_clean}")

        return target_path
