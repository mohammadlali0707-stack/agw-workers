"""cluster_chat/telegram_bridge.py

Bidirectional Telegram Bridge for Cluster Chat.
Handles photo and document forwarding between Cluster Chat rooms (main, dm-argon, workers-room)
and Telegram Bot API, including file downloads, 10 MB fallback handling, agent inbox delivery,
and room mapping.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import threading
from typing import Any, Callable
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
    classify_attachment_kind,
    detect_mime_type,
    sanitize_filename,
    validate_room_name,
)
from cluster_chat.rooms import (
    DEFAULT_DATA_DIR,
    RoomManager,
)

logger = logging.getLogger("cluster_chat.telegram_bridge")

DEFAULT_TELEGRAM_API_BASE_URL = "https://api.telegram.org"
MAX_TELEGRAM_PHOTO_SIZE = 10 * 1024 * 1024  # 10 MB limit for sendPhoto
TELEGRAM_CAPTION_MAX_LENGTH = 1024
KNOWN_AGENTS = ("argon", "cookie", "coca", "workers")


def encode_multipart_formdata(
    fields: dict[str, Any],
    file_field: str | None = None,
    filename: str | None = None,
    file_bytes: bytes | None = None,
    mime_type: str | None = None,
) -> tuple[bytes, str]:
    """Encode fields and optional file into multipart/form-data payload with boundary."""
    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
    body = bytearray()

    for key, val in fields.items():
        if val is None:
            continue
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(val).encode("utf-8"))
        body.extend(b"\r\n")

    if file_field and file_bytes is not None:
        fn = sanitize_filename(filename or "attachment.bin")
        ct = mime_type or detect_mime_type(fn)
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{file_field}"; filename="{fn}"\r\n'.encode("utf-8"))
        body.extend(f"Content-Type: {ct}\r\n\r\n".encode("utf-8"))
        body.extend(file_bytes)
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    content_type = f"multipart/form-data; boundary={boundary}"
    return bytes(body), content_type


def extract_target_agent(room: str, text: str = "") -> str | None:
    """Extract or infer target agent from message content or room name."""
    clean_text = (text or "").strip()

    # 1. Mention in text (e.g. @argon, @cookie, @coca, @workers)
    if clean_text:
        mention_match = re.search(r"@([a-zA-Z0-9_-]+)", clean_text)
        if mention_match:
            candidate = mention_match.group(1).lower()
            return candidate

        prefix_match = re.match(r"^([a-zA-Z0-9_-]+):", clean_text)
        if prefix_match:
            candidate = prefix_match.group(1).lower()
            if candidate in KNOWN_AGENTS:
                return candidate

    # 2. Room-based target inference
    room_clean = (room or "").strip().lower()
    if room_clean == "dm-argon":
        return "argon"
    if room_clean == "workers-room":
        return "workers"
    if room_clean.startswith("dm-") and len(room_clean) > 3:
        return room_clean[3:]

    return None


class TelegramBridge:
    """Bidirectional Telegram Bridge for Cluster Chat rooms."""

    def __init__(
        self,
        bot_token: str | None = None,
        room_map: dict[str, Any] | None = None,
        storage: AttachmentStorageManager | None = None,
        room_manager: RoomManager | None = None,
        api_base_url: str | None = None,
        agent_webhook_url: str | None = None,
        data_dir: str | Path | None = None,
    ) -> None:
        self.bot_token: str | None = (
            bot_token.strip()
            if bot_token and bot_token.strip()
            else os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or None
        )

        base_url = (
            api_base_url
            if api_base_url is not None
            else os.environ.get("TELEGRAM_API_BASE_URL", DEFAULT_TELEGRAM_API_BASE_URL)
        )
        self.api_base_url: str = base_url.rstrip("/")

        self.storage: AttachmentStorageManager = (
            storage if storage is not None else AttachmentStorageManager()
        )
        self.room_manager: RoomManager = (
            room_manager if room_manager is not None else RoomManager()
        )

        raw_data_dir = (
            data_dir
            if data_dir is not None
            else getattr(self.room_manager, "data_dir", os.environ.get("CLUSTER_CHAT_DATA_DIR", DEFAULT_DATA_DIR))
        )
        self.data_dir: Path = Path(raw_data_dir).resolve()
        self.inbox_dir: Path = (self.data_dir / "inbox").resolve()

        raw_webhook = (
            agent_webhook_url
            if agent_webhook_url is not None
            else os.environ.get("CLUSTER_CHAT_AGENT_WEBHOOK_URL", "")
        )
        self.agent_webhook_url: str | None = raw_webhook.strip() or None

        self._lock = threading.Lock()
        self.room_map: dict[str, Any] = self._load_room_map(room_map)

    def _load_room_map(self, explicit_map: dict[str, Any] | None = None) -> dict[str, Any]:
        """Load room-to-Telegram chat mapping from param, JSON env var, or per-room env vars."""
        mapping: dict[str, Any] = {}

        # 1. Parse from TELEGRAM_ROOM_MAP_JSON if present
        env_json = os.environ.get("TELEGRAM_ROOM_MAP_JSON", "").strip()
        if env_json:
            try:
                parsed = json.loads(env_json)
                if isinstance(parsed, dict):
                    for k, v in parsed.items():
                        mapping[str(k).strip()] = v
            except Exception as e:
                logger.warning("Failed to parse TELEGRAM_ROOM_MAP_JSON: %s", e)

        # 2. Per-room environment variables
        for room_name in STANDARD_ROOMS:
            slug = room_name.upper().replace("-", "_")
            chat_id = os.environ.get(f"TELEGRAM_CHAT_ID_{slug}")
            thread_id = os.environ.get(f"TELEGRAM_THREAD_ID_{slug}")

            if chat_id and chat_id.strip():
                entry: dict[str, Any] = {"chat_id": chat_id.strip()}
                if thread_id and thread_id.strip():
                    try:
                        entry["message_thread_id"] = int(thread_id.strip())
                    except ValueError:
                        pass
                mapping[room_name] = entry

        # 3. Explicit dictionary override
        if explicit_map:
            for k, v in explicit_map.items():
                mapping[str(k).strip()] = v

        return mapping

    def resolve_room_target(self, room: str) -> tuple[str | None, int | None]:
        """Resolve Telegram chat_id and optional message_thread_id for a given room."""
        room_clean = room.strip()

        # Check in room_map
        if room_clean in self.room_map:
            val = self.room_map[room_clean]
            if isinstance(val, dict):
                cid = val.get("chat_id")
                tid = val.get("message_thread_id") or val.get("thread_id")
                return (str(cid) if cid is not None else None, int(tid) if tid is not None else None)
            if val is not None:
                return (str(val), None)

        # Check generic env var
        slug = room_clean.upper().replace("-", "_")
        chat_id = os.environ.get(f"TELEGRAM_CHAT_ID_{slug}")
        thread_id = os.environ.get(f"TELEGRAM_THREAD_ID_{slug}")
        if chat_id and chat_id.strip():
            tid_int = None
            if thread_id and thread_id.strip():
                try:
                    tid_int = int(thread_id.strip())
                except ValueError:
                    pass
            return (chat_id.strip(), tid_int)

        # Fallback for main room
        if room_clean == "main":
            fallback_cid = os.environ.get("TELEGRAM_CHAT_ID")
            if fallback_cid and fallback_cid.strip():
                return (fallback_cid.strip(), None)

        return (None, None)

    def map_chat_to_room(self, chat_id: str | int, thread_id: int | None = None) -> str:
        """Map Telegram (chat_id, message_thread_id) back to cluster room, defaulting to 'main'."""
        cid_str = str(chat_id).strip()
        tid_int = int(thread_id) if thread_id is not None else None

        # 1. Exact match with thread_id in room_map
        if tid_int is not None:
            for r_name, r_val in self.room_map.items():
                if isinstance(r_val, dict):
                    v_cid = str(r_val.get("chat_id", "")).strip()
                    v_tid = r_val.get("message_thread_id") or r_val.get("thread_id")
                    if v_cid == cid_str and v_tid is not None and int(v_tid) == tid_int:
                        return r_name

        # 2. Match chat_id for non-thread-specific rooms (v_tid is None)
        for r_name, r_val in self.room_map.items():
            if isinstance(r_val, dict):
                v_cid = str(r_val.get("chat_id", "")).strip()
                v_tid = r_val.get("message_thread_id") or r_val.get("thread_id")
                if v_cid == cid_str and v_tid is None:
                    return r_name
            elif str(r_val).strip() == cid_str:
                return r_name

        # 3. Environment variables match (thread-bound first)
        if tid_int is not None:
            for r_name in STANDARD_ROOMS:
                slug = r_name.upper().replace("-", "_")
                env_cid = os.environ.get(f"TELEGRAM_CHAT_ID_{slug}", "").strip()
                env_tid = os.environ.get(f"TELEGRAM_THREAD_ID_{slug}", "").strip()
                if env_cid == cid_str and env_tid:
                    try:
                        if int(env_tid) == tid_int:
                            return r_name
                    except ValueError:
                        pass

        # 4. Environment variables match (non-threaded general room)
        for r_name in STANDARD_ROOMS:
            slug = r_name.upper().replace("-", "_")
            env_cid = os.environ.get(f"TELEGRAM_CHAT_ID_{slug}", "").strip()
            env_tid = os.environ.get(f"TELEGRAM_THREAD_ID_{slug}", "").strip()
            if env_cid == cid_str and not env_tid:
                return r_name

        # Default fallback
        return "main"

    def _http_request(
        self,
        url: str,
        method: str = "GET",
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> tuple[int, bytes]:
        """Perform HTTP request via urllib.request, returning (status_code, body_bytes)."""
        req_headers = {
            "User-Agent": "cluster-chat-telegram-bridge/1.0",
        }
        if headers:
            req_headers.update(headers)

        req = urllib.request.Request(
            url=url,
            data=body,
            headers=req_headers,
            method=method,
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status if hasattr(resp, "status") else resp.getcode()
                data = resp.read()
                return status, data
        except urllib.error.HTTPError as he:
            err_body = he.read() if hasattr(he, "read") else b""
            return he.code, err_body
        except urllib.error.URLError as ue:
            raise ConnectionError(f"Failed to connect to Telegram API ({url}): {ue.reason}") from ue

    def download_telegram_file(self, file_id: str) -> tuple[bytes, str]:
        """Call Telegram getFile and download the file payload bytes."""
        if not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is not configured")

        file_id_clean = str(file_id).strip()
        get_file_url = f"{self.api_base_url}/bot{self.bot_token}/getFile?file_id={urllib.parse.quote(file_id_clean)}"

        status, resp_bytes = self._http_request(get_file_url, method="GET")
        if status != 200:
            raise RuntimeError(
                f"Telegram getFile failed (status {status}): {resp_bytes.decode('utf-8', errors='replace')}"
            )

        try:
            info = json.loads(resp_bytes.decode("utf-8"))
        except Exception as e:
            raise RuntimeError(f"Invalid JSON returned by getFile: {e}") from e

        if not info.get("ok"):
            raise RuntimeError(f"Telegram getFile returned error: {info.get('description')}")

        result = info.get("result", {})
        file_path = result.get("file_path")
        if not file_path:
            raise RuntimeError("Telegram getFile returned result without file_path")

        clean_path = str(file_path).lstrip("/")
        download_url = f"{self.api_base_url}/file/bot{self.bot_token}/{clean_path}"

        dl_status, dl_bytes = self._http_request(download_url, method="GET")
        if dl_status != 200:
            raise RuntimeError(
                f"Telegram file download failed (status {dl_status}): {dl_bytes.decode('utf-8', errors='replace')}"
            )

        return dl_bytes, clean_path

    def send_attachment_to_telegram(
        self,
        message: dict[str, Any],
        attachment: dict[str, Any],
    ) -> dict[str, Any]:
        """Send an attachment (photo or document) to Telegram with caption and metadata update."""
        room = message.get("room", "main")
        chat_id, thread_id = self.resolve_room_target(room)

        # Graceful degradation if token or chat_id is missing
        if not self.bot_token or not chat_id:
            logger.warning(
                "Telegram send skipped: bot_token=%s, chat_id=%s for room '%s'",
                bool(self.bot_token),
                chat_id,
                room,
            )
            return {"ok": False, "skipped": "no_token_or_chat_id"}

        sender = message.get("sender", "unknown")
        text = (message.get("text") or "").strip()
        if text:
            caption = f"[{room}] {sender}: {text}"
        else:
            caption = f"[{room}] {sender}"

        if len(caption) > TELEGRAM_CAPTION_MAX_LENGTH:
            caption = caption[: TELEGRAM_CAPTION_MAX_LENGTH - 3] + "..."

        kind = attachment.get("kind", "document")
        size_bytes = int(attachment.get("size_bytes", 0))

        # Check 10 MB limit for photos
        is_photo = (kind == "photo" and size_bytes <= MAX_TELEGRAM_PHOTO_SIZE)
        method = "sendPhoto" if is_photo else "sendDocument"
        file_field = "photo" if is_photo else "document"

        fields: dict[str, Any] = {
            "chat_id": chat_id,
            "caption": caption,
        }
        if thread_id is not None:
            fields["message_thread_id"] = thread_id

        local_path = attachment.get("local_path")
        has_local_file = bool(local_path and Path(local_path).is_file())
        existing_file_id = attachment.get("telegram_file_id")

        file_bytes: bytes | None = None
        filename: str = attachment.get("filename") or "attachment.bin"
        mime_type: str | None = attachment.get("mime_type")

        # Prioritize local file upload if local file exists and file_id is not already cached,
        # or use existing telegram_file_id
        if existing_file_id and not has_local_file:
            fields[file_field] = existing_file_id
            file_field_for_upload = None
        elif has_local_file:
            file_bytes = Path(local_path).read_bytes()
            file_field_for_upload = file_field
        elif existing_file_id:
            fields[file_field] = existing_file_id
            file_field_for_upload = None
        else:
            logger.error("Attachment has neither local file nor telegram_file_id: %s", attachment)
            return {
                "ok": False,
                "error": f"Neither local file nor telegram_file_id available for attachment {attachment.get('attachment_id')}",
            }

        body, content_type = encode_multipart_formdata(
            fields=fields,
            file_field=file_field_for_upload,
            filename=filename,
            file_bytes=file_bytes,
            mime_type=mime_type,
        )

        url = f"{self.api_base_url}/bot{self.bot_token}/{method}"
        try:
            status, resp_bytes = self._http_request(
                url=url,
                method="POST",
                body=body,
                headers={"Content-Type": content_type},
            )
        except Exception as exc:
            logger.error("Telegram API %s request failed: %s", method, exc)
            return {"ok": False, "error": str(exc)}

        try:
            resp_json = json.loads(resp_bytes.decode("utf-8"))
        except Exception as exc:
            return {"ok": False, "error": f"Invalid JSON response from Telegram: {exc}", "status": status}

        if not resp_json.get("ok"):
            return {
                "ok": False,
                "error": resp_json.get("description", "Unknown Telegram error"),
                "status": status,
                "response": resp_json,
            }

        result = resp_json.get("result", {})
        new_file_id: str | None = None
        if is_photo and "photo" in result and isinstance(result["photo"], list) and len(result["photo"]) > 0:
            new_file_id = result["photo"][-1].get("file_id")
        elif "document" in result and isinstance(result["document"], dict):
            new_file_id = result["document"].get("file_id")

        if new_file_id:
            attachment["telegram_file_id"] = new_file_id

        return {
            "ok": True,
            "method": method,
            "telegram_file_id": new_file_id,
            "chat_id": chat_id,
            "result": result,
        }

    def send_message_to_telegram(self, message: dict[str, Any]) -> dict[str, Any]:
        """Forward a room message and all its attachments to Telegram."""
        room = message.get("room", "main")
        chat_id, thread_id = self.resolve_room_target(room)

        if not self.bot_token or not chat_id:
            return {"ok": False, "skipped": "no_token_or_chat_id"}

        attachments = message.get("attachments", [])
        if attachments:
            results = []
            for att in attachments:
                res = self.send_attachment_to_telegram(message, att)
                results.append(res)
            return {"ok": True, "results": results}

        # Text-only message dispatch
        sender = message.get("sender", "unknown")
        text = (message.get("text") or "").strip()
        if not text:
            return {"ok": False, "skipped": "empty_message"}

        caption = f"[{room}] {sender}: {text}"
        if len(caption) > 4096:
            caption = caption[:4093] + "..."

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": caption,
        }
        if thread_id is not None:
            payload["message_thread_id"] = thread_id

        url = f"{self.api_base_url}/bot{self.bot_token}/sendMessage"
        body = json.dumps(payload).encode("utf-8")
        status, resp_bytes = self._http_request(
            url=url,
            method="POST",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            resp_json = json.loads(resp_bytes.decode("utf-8"))
            return {"ok": resp_json.get("ok", False), "result": resp_json.get("result")}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "status": status}

    def handle_telegram_update(self, update: dict[str, Any]) -> dict[str, Any] | None:
        """Process incoming Telegram webhook/polling update for photos, documents, and text."""
        msg = update.get("message") or update.get("channel_post") or update.get("edited_message")
        if not isinstance(msg, dict):
            return None

        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        if chat_id is None:
            return None

        thread_id = msg.get("message_thread_id")
        room = self.map_chat_to_room(chat_id, thread_id)

        # Determine sender
        from_user = msg.get("from", {})
        sender = (
            from_user.get("username")
            or from_user.get("first_name")
            or chat.get("title")
            or "telegram_user"
        )

        caption = msg.get("caption", "") or ""
        text = msg.get("text", "") or ""
        content_text = (caption or text).strip()

        target_agent = extract_target_agent(room, content_text)
        saved_attachments: list[dict[str, Any]] = []

        # 1. Photo handling
        if "photo" in msg and isinstance(msg["photo"], list) and len(msg["photo"]) > 0:
            if not self.bot_token:
                logger.warning("Received Telegram photo update but TELEGRAM_BOT_TOKEN is not configured")
                return None

            best_photo = msg["photo"][-1]
            file_id = best_photo.get("file_id")
            if file_id:
                dl_bytes, file_path = self.download_telegram_file(file_id)
                ext = Path(file_path).suffix or ".jpg"
                filename = sanitize_filename(f"tg_photo_{file_id[:8]}{ext}")
                meta = self.storage.save_bytes(
                    content=dl_bytes,
                    filename=filename,
                    room=room,
                    sender=sender,
                    mime_type="image/jpeg" if ext.lower() in (".jpg", ".jpeg") else None,
                    target_agent=target_agent,
                    telegram_file_id=file_id,
                )
                saved_attachments.append(meta)

        # 2. Document handling
        elif "document" in msg and isinstance(msg["document"], dict):
            if not self.bot_token:
                logger.warning("Received Telegram document update but TELEGRAM_BOT_TOKEN is not configured")
                return None

            doc = msg["document"]
            file_id = doc.get("file_id")
            doc_filename = doc.get("file_name")
            doc_mime = doc.get("mime_type")

            if file_id:
                dl_bytes, file_path = self.download_telegram_file(file_id)
                chosen_fn = doc_filename or Path(file_path).name or f"tg_doc_{file_id[:8]}.bin"
                meta = self.storage.save_bytes(
                    content=dl_bytes,
                    filename=chosen_fn,
                    room=room,
                    sender=sender,
                    mime_type=doc_mime,
                    target_agent=target_agent,
                    telegram_file_id=file_id,
                )
                saved_attachments.append(meta)

        # Skip if message contains no usable text or attachments
        if not content_text and not saved_attachments:
            return None

        # Post message to room store
        posted_message = self.room_manager.post_message(
            room=room,
            sender=sender,
            text=content_text,
            attachments=saved_attachments,
            target_agent=target_agent,
        )

        # Dispatch agent notification payload
        self.dispatch_agent_notification(posted_message, target_agent=target_agent)

        return posted_message

    def dispatch_agent_notification(
        self,
        message: dict[str, Any],
        target_agent: str | None = None,
    ) -> None:
        """Deliver notification payload to target agent inbox JSONL and optional webhook."""
        attachments = message.get("attachments", [])
        first_att = attachments[0] if attachments else {}

        payload: dict[str, Any] = {
            "event": "cluster_chat_message",
            "message_id": message.get("message_id"),
            "room": message.get("room"),
            "sender": message.get("sender"),
            "target_agent": target_agent,
            "text": message.get("text", ""),
            "attachments": attachments,
            "local_path": first_att.get("local_path", ""),
            "public_url": first_att.get("public_url", ""),
            "created_at": message.get("created_at"),
            "message": message,
        }

        # 1. Local agent inbox file append (e.g. data/cluster-chat/inbox/<target_agent>.jsonl)
        if target_agent:
            clean_agent = sanitize_filename(target_agent).replace(".bin", "").strip("_")
            if clean_agent:
                inbox_file = (self.inbox_dir / f"{clean_agent}.jsonl").resolve()
                line = json.dumps(payload, ensure_ascii=False) + "\n"
                with self._lock:
                    self.inbox_dir.mkdir(parents=True, exist_ok=True)
                    with open(inbox_file, "a", encoding="utf-8") as f:
                        f.write(line)
                        f.flush()

        # 2. Agent webhook dispatch if configured
        if self.agent_webhook_url:
            try:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self._http_request(
                    url=self.agent_webhook_url,
                    method="POST",
                    body=body,
                    headers={"Content-Type": "application/json"},
                    timeout=5.0,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to dispatch to CLUSTER_CHAT_AGENT_WEBHOOK_URL (%s): %s",
                    self.agent_webhook_url,
                    exc,
                )


# Global singleton bridge instance
_global_bridge: TelegramBridge | None = None


def get_default_bridge() -> TelegramBridge:
    """Get or initialize default singleton TelegramBridge."""
    global _global_bridge
    if _global_bridge is None:
        _global_bridge = TelegramBridge()
    return _global_bridge


def send_attachment_to_telegram(
    message: dict[str, Any],
    attachment: dict[str, Any],
    bridge: TelegramBridge | None = None,
) -> dict[str, Any]:
    """Module-level helper to send an attachment to Telegram using the provided or default bridge."""
    active_bridge = bridge if bridge is not None else get_default_bridge()
    return active_bridge.send_attachment_to_telegram(message, attachment)


def handle_telegram_update(
    update: dict[str, Any],
    bridge: TelegramBridge | None = None,
) -> dict[str, Any] | None:
    """Module-level helper to process an inbound Telegram update using the provided or default bridge."""
    active_bridge = bridge if bridge is not None else get_default_bridge()
    return active_bridge.handle_telegram_update(update)
