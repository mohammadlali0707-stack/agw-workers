"""cluster_chat/rooms.py

Room message management and persistence for the Cluster Chat bridge.
Provides message logging, history retrieval, and attachment binding across
cluster rooms (main, dm-argon, workers-room).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from typing import Any
import uuid

from cluster_chat.attachments import (
    STANDARD_ROOMS,
    AttachmentMetadata,
    validate_room_name,
)

DEFAULT_DATA_DIR = "./data/cluster-chat"


@dataclass
class RoomMessage:
    """Represents a message sent to a cluster chat room."""

    message_id: str
    room: str
    sender: str
    text: str = ""
    target_agent: str | None = None
    attachments: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert room message to dictionary representation."""
        return {
            "message_id": self.message_id,
            "room": self.room,
            "sender": self.sender,
            "target_agent": self.target_agent,
            "text": self.text,
            "attachments": [
                att.to_dict() if hasattr(att, "to_dict") else att
                for att in self.attachments
            ],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RoomMessage":
        """Construct RoomMessage from dictionary."""
        return cls(
            message_id=str(data["message_id"]),
            room=str(data["room"]),
            sender=str(data["sender"]),
            text=str(data.get("text", "")),
            target_agent=data.get("target_agent"),
            attachments=list(data.get("attachments", [])),
            created_at=str(data.get("created_at", "")),
        )


class RoomManager:
    """Manages persistent room messages, JSONL logs, and room queries."""

    def __init__(
        self,
        data_dir: str | Path | None = None,
        known_rooms: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        raw_dir = (
            data_dir
            if data_dir is not None
            else os.environ.get("CLUSTER_CHAT_DATA_DIR", DEFAULT_DATA_DIR)
        )
        self.data_dir: Path = Path(raw_dir).resolve()
        self.messages_dir: Path = (self.data_dir / "messages").resolve()
        self.messages_dir.mkdir(parents=True, exist_ok=True)

        self.known_rooms: set[str] = set(known_rooms or STANDARD_ROOMS)
        self._lock = threading.Lock()

    def get_room_file(self, room: str) -> Path:
        """Resolve room message storage JSONL file safely."""
        validated_room = validate_room_name(room)
        # Support 'workers' and 'workers-room' mapping to the same underlying file if one exists
        file_stem = validated_room
        if file_stem == "workers" and (self.messages_dir / "workers-room.jsonl").exists():
            file_stem = "workers-room"
        elif file_stem == "workers-room" and (self.messages_dir / "workers.jsonl").exists() and not (self.messages_dir / "workers-room.jsonl").exists():
            file_stem = "workers"

        target = (self.messages_dir / f"{file_stem}.jsonl").resolve()

        if not target.is_relative_to(self.messages_dir):
            raise ValueError(f"Path traversal detected for room '{room}'")

        return target

    def list_rooms(self) -> list[str]:
        """Return list of all recognized and active cluster rooms."""
        rooms = set(self.known_rooms)
        if self.messages_dir.exists():
            for p in self.messages_dir.glob("*.jsonl"):
                rooms.add(p.stem)
        return sorted(rooms)

    def post_message(
        self,
        room: str,
        sender: str,
        text: str = "",
        attachments: list[dict[str, Any] | AttachmentMetadata] | None = None,
        target_agent: str | None = None,
        message_id: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        """Post a new message to a cluster room and persist to room JSONL log."""
        validated_room = validate_room_name(room)

        if not sender or not str(sender).strip():
            raise ValueError("sender must be a non-empty string")
        clean_sender = str(sender).strip()

        clean_text = text if isinstance(text, str) else str(text or "")

        # Process attachments
        processed_attachments: list[dict[str, Any]] = []
        if attachments:
            for item in attachments:
                if isinstance(item, AttachmentMetadata):
                    processed_attachments.append(item.to_dict())
                elif isinstance(item, dict):
                    # Ensure minimum required fields exist
                    required_keys = {"attachment_id", "kind", "filename", "local_path", "public_url"}
                    missing = required_keys - set(item.keys())
                    if missing:
                        raise ValueError(f"Attachment missing required keys: {sorted(missing)}")
                    processed_attachments.append(dict(item))
                else:
                    raise TypeError(
                        f"Unsupported attachment type '{type(item).__name__}'; expected dict or AttachmentMetadata"
                    )

        if not clean_text.strip() and not processed_attachments:
            raise ValueError("Message must have non-empty text or at least one attachment")

        # Timestamp
        if not created_at:
            created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Message ID
        if not message_id:
            now_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            message_id = f"msg_{now_ts}_{uuid.uuid4().hex[:8]}"

        message = RoomMessage(
            message_id=message_id,
            room=validated_room,
            sender=clean_sender,
            text=clean_text,
            target_agent=str(target_agent).strip() if target_agent else None,
            attachments=processed_attachments,
            created_at=created_at,
        )

        msg_dict = message.to_dict()

        # Thread-safe persistent append to room's JSONL
        room_file = self.get_room_file(validated_room)
        line = json.dumps(msg_dict, ensure_ascii=False) + "\n"

        with self._lock:
            with open(room_file, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()

        return msg_dict

    def get_messages(
        self,
        room: str,
        limit: int = 50,
        before_id: str | None = None,
        target_agent: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve recent messages for a room, optionally filtered by target_agent or before_id."""
        room_file = self.get_room_file(room)
        if not room_file.exists():
            return []

        all_messages: list[dict[str, Any]] = []

        with self._lock:
            with open(room_file, "r", encoding="utf-8") as f:
                for line in f:
                    line_str = line.strip()
                    if not line_str:
                        continue
                    try:
                        record = json.loads(line_str)
                        all_messages.append(record)
                    except json.JSONDecodeError:
                        continue

        # Optional before_id truncation
        if before_id:
            truncated: list[dict[str, Any]] = []
            for m in all_messages:
                if m.get("message_id") == before_id:
                    break
                truncated.append(m)
            all_messages = truncated

        # Optional target_agent filtering: keep messages targeted to this agent or broadcast (target_agent is None)
        if target_agent is not None:
            clean_target = str(target_agent).strip()
            all_messages = [
                m
                for m in all_messages
                if m.get("target_agent") is None or m.get("target_agent") == clean_target
            ]

        # Return up to limit messages (most recent)
        if limit > 0 and len(all_messages) > limit:
            return all_messages[-limit:]

        return all_messages

    def get_message_by_id(self, room: str, message_id: str) -> dict[str, Any] | None:
        """Find a specific message by its message_id in a room."""
        room_file = self.get_room_file(room)
        if not room_file.exists():
            return None

        with self._lock:
            with open(room_file, "r", encoding="utf-8") as f:
                for line in f:
                    line_str = line.strip()
                    if not line_str:
                        continue
                    try:
                        record = json.loads(line_str)
                        if record.get("message_id") == message_id:
                            return record
                    except json.JSONDecodeError:
                        continue

        return None

    def clear_room_messages(self, room: str) -> None:
        """Clear all messages in a room (for cleanup/testing)."""
        room_file = self.get_room_file(room)
        with self._lock:
            if room_file.exists():
                room_file.unlink()
