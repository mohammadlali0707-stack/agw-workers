"""cluster_chat/client.py

Python Client & SDK for Cluster Chat bridge.
Enables Mohammad and AI agents (Argon, Cookie, Coca, AGY workers) to upload,
send, and inspect photos and documents across cluster rooms (main, dm-argon, workers-room)
via HTTP API or direct local storage and room persistence.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import uuid

from cluster_chat.attachments import (
    DEFAULT_MAX_UPLOAD_MB,
    DEFAULT_PUBLIC_BASE_URL,
    DEFAULT_UPLOAD_DIR,
    STANDARD_ROOMS,
    AttachmentMetadata,
    AttachmentStorageManager,
    detect_mime_type,
    validate_room_name,
)
from cluster_chat.rooms import (
    DEFAULT_DATA_DIR,
    RoomManager,
)
from cluster_chat.telegram_bridge import TelegramBridge

logger = logging.getLogger("cluster_chat.client")


def format_file_size(size_bytes: int | float | None) -> str:
    """Format bytes into human-readable string (B, KB, MB, GB)."""
    try:
        val = int(size_bytes) if size_bytes is not None else 0
    except (ValueError, TypeError):
        val = 0

    if val < 1024:
        return f"{val} B"
    elif val < 1024 * 1024:
        return f"{val / 1024:.1f} KB"
    elif val < 1024 * 1024 * 1024:
        return f"{val / (1024 * 1024):.1f} MB"
    return f"{val / (1024 * 1024 * 1024):.2f} GB"


def _build_multipart_payload(
    fields: dict[str, Any],
    files: list[tuple[str, str, bytes, str]],
) -> tuple[bytes, str]:
    """Build multipart/form-data payload and content-type header with boundary.

    files: list of (field_name, filename, file_bytes, mime_type)
    """
    boundary = f"----ClusterChatCLIFormBoundary{uuid.uuid4().hex}"
    body = bytearray()

    for k, v in fields.items():
        if v is None:
            continue
        body.extend(f"--{boundary}\r\n".encode("latin-1"))
        body.extend(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode("latin-1"))
        body.extend(str(v).encode("utf-8"))
        body.extend(b"\r\n")

    for field_name, filename, data, mime in files:
        body.extend(f"--{boundary}\r\n".encode("latin-1"))
        body.extend(
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode("latin-1")
        )
        body.extend(f"Content-Type: {mime}\r\n\r\n".encode("latin-1"))
        body.extend(data)
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode("latin-1"))
    content_type = f"multipart/form-data; boundary={boundary}"
    return bytes(body), content_type


def send_room_file(
    room: str,
    sender: str,
    file_path: str | Path | list[str | Path] | None = None,
    files: list[str | Path] | None = None,
    text: str = "",
    target_agent: str | None = None,
    api_url: str | None = None,
    forward_telegram: bool = True,
    storage: AttachmentStorageManager | None = None,
    room_manager: RoomManager | None = None,
    telegram_bridge: TelegramBridge | None = None,
    upload_dir: str | Path | None = None,
    data_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Upload/send files or photos and message to a cluster chat room.

    Supports both local execution (direct disk write + room store + Telegram forward)
    and remote execution via HTTP API (POST /api/cluster-chat/upload).

    Args:
        room: Target room name ('main', 'dm-argon', 'workers-room', or custom).
        sender: Sender identifier (e.g. 'argon', 'mohammad', 'agy-1').
        file_path: Single file path or list of file paths to attach.
        files: Additional or alternative list of file paths.
        text: Text message or caption.
        target_agent: Optional recipient agent (e.g. 'argon', 'mohammad').
        api_url: Optional Cluster Chat HTTP base URL (e.g. 'http://localhost:8080').
                 If provided, uploads via HTTP API; otherwise writes locally.
        forward_telegram: Whether to dispatch to Telegram if configured.
        storage: Optional preconfigured AttachmentStorageManager.
        room_manager: Optional preconfigured RoomManager.
        telegram_bridge: Optional preconfigured TelegramBridge.
        upload_dir: Optional custom upload directory for local mode.
        data_dir: Optional custom data directory for local mode.

    Returns:
        dict containing 'ok', 'message', and 'attachments'.
    """
    validated_room = validate_room_name(room)

    if not sender or not str(sender).strip():
        raise ValueError("sender must be a non-empty string")
    clean_sender = str(sender).strip()
    clean_text = str(text or "")
    clean_target = str(target_agent).strip() if target_agent else None

    # Collect and validate all file paths
    collected_files: list[Path] = []
    if file_path:
        if isinstance(file_path, (list, tuple)):
            for p in file_path:
                if p:
                    collected_files.append(Path(p).resolve())
        else:
            collected_files.append(Path(file_path).resolve())

    if files:
        for p in files:
            if p:
                collected_files.append(Path(p).resolve())

    for f in collected_files:
        if not f.exists():
            raise FileNotFoundError(f"File not found: {f}")
        if not f.is_file():
            raise ValueError(f"Path is not a regular file: {f}")

    if not clean_text.strip() and not collected_files:
        raise ValueError("Message must have non-empty text or at least one file attachment")

    # 1. HTTP API Mode
    if api_url:
        api_base = api_url.rstrip("/")

        if collected_files:
            # Multipart upload endpoint
            if "/upload" in api_base:
                upload_endpoint = api_base
            else:
                upload_endpoint = f"{api_base}/api/cluster-chat/upload"

            fields: dict[str, Any] = {
                "room": validated_room,
                "sender": clean_sender,
                "text": clean_text,
                "forward_telegram": "true" if forward_telegram else "false",
            }
            if clean_target:
                fields["target_agent"] = clean_target

            file_payloads: list[tuple[str, str, bytes, str]] = []
            for f in collected_files:
                f_bytes = f.read_bytes()
                mime = detect_mime_type(f.name)
                file_payloads.append(("file", f.name, f_bytes, mime))

            body, content_type = _build_multipart_payload(fields, file_payloads)
            req = urllib.request.Request(
                upload_endpoint,
                data=body,
                headers={
                    "Content-Type": content_type,
                    "Content-Length": str(len(body)),
                },
                method="POST",
            )
        else:
            # Text-only message endpoint
            if "/messages" in api_base:
                messages_endpoint = api_base
            else:
                messages_endpoint = f"{api_base}/api/cluster-chat/rooms/{validated_room}/messages"

            json_payload = {
                "room": validated_room,
                "sender": clean_sender,
                "text": clean_text,
                "target_agent": clean_target,
                "forward_telegram": forward_telegram,
                "attachments": [],
            }
            body = json.dumps(json_payload).encode("utf-8")
            req = urllib.request.Request(
                messages_endpoint,
                data=body,
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "Content-Length": str(len(body)),
                },
                method="POST",
            )

        try:
            with urllib.request.urlopen(req) as resp:
                resp_bytes = resp.read()
                return json.loads(resp_bytes.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            try:
                err_json = json.loads(err_body)
                err_msg = err_json.get("error", err_body)
            except Exception:
                err_msg = err_body
            raise RuntimeError(f"HTTP {exc.code} error uploading to {api_url}: {err_msg}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Failed to connect to {api_url}: {exc.reason}") from exc

    # 2. Local Direct Mode
    storage_mgr = storage or AttachmentStorageManager(upload_dir=upload_dir)
    room_mgr = room_manager or RoomManager(data_dir=data_dir)

    saved_attachments: list[dict[str, Any]] = []
    for f in collected_files:
        meta = storage_mgr.save_file_from_path(
            src_path=f,
            room=validated_room,
            sender=clean_sender,
            target_agent=clean_target,
        )
        saved_attachments.append(meta)

    msg_dict = room_mgr.post_message(
        room=validated_room,
        sender=clean_sender,
        text=clean_text,
        attachments=saved_attachments,
        target_agent=clean_target,
    )

    if forward_telegram:
        tb = telegram_bridge or TelegramBridge(
            storage=storage_mgr,
            room_manager=room_mgr,
            data_dir=room_mgr.data_dir,
        )
        try:
            tb.send_message_to_telegram(msg_dict)
        except Exception as exc:
            logger.warning("Telegram forward skipped or encountered error: %s", exc)

    return {
        "ok": True,
        "message": msg_dict,
        "attachments": saved_attachments,
    }


def list_room_messages(
    room: str,
    limit: int = 20,
    target_agent: str | None = None,
    since: str | float | int | None = None,
    after: str | None = None,
    api_url: str | None = None,
    room_manager: RoomManager | None = None,
    data_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Retrieve recent messages and attachments for a room.

    Args:
        room: Room name to query.
        limit: Maximum number of recent messages to return.
        target_agent: Optional filter by target agent.
        since: Optional message ID or ISO/Unix timestamp for incremental polling.
        after: Optional message ID to return messages strictly after.
        api_url: Optional Cluster Chat HTTP base URL.
        room_manager: Optional preconfigured RoomManager.
        data_dir: Optional custom data directory for local mode.

    Returns:
        list of message dicts in chronological order.
    """
    validated_room = validate_room_name(room)
    clean_target = str(target_agent).strip() if target_agent else None

    if api_url:
        api_base = api_url.rstrip("/")
        params: dict[str, str] = {"limit": str(limit)}
        if clean_target:
            params["target_agent"] = clean_target
        if since is not None:
            params["since"] = str(since)
        if after is not None:
            params["after"] = str(after)

        query_str = urllib.parse.urlencode(params)
        endpoint = f"{api_base}/api/cluster-chat/rooms/{validated_room}/messages?{query_str}"
        req = urllib.request.Request(endpoint, method="GET")

        try:
            with urllib.request.urlopen(req) as resp:
                resp_bytes = resp.read()
                data = json.loads(resp_bytes.decode("utf-8"))
                return data.get("messages", [])
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} error querying {endpoint}: {err_body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Failed to connect to {api_url}: {exc.reason}") from exc

    # Local mode
    room_mgr = room_manager or RoomManager(data_dir=data_dir)
    return room_mgr.get_messages(
        room=validated_room,
        limit=limit,
        target_agent=clean_target,
        since=since,
        after=after,
    )
