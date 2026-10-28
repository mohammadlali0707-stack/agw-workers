"""cluster_chat/server.py

HTTP Server & REST API endpoints for Cluster Chat bridge.
Supports multipart/form-data upload, base64 and local_path JSON forwarding,
safe static file serving, room messages API, and Web Room UI delivery.
"""

from __future__ import annotations

import argparse
import base64
from email.parser import BytesParser
import http.server
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import socket
import threading
from typing import Any, Callable
import urllib.parse
import uuid

from cluster_chat.attachments import (
    DEFAULT_MAX_UPLOAD_MB,
    DEFAULT_PUBLIC_BASE_URL,
    DEFAULT_UPLOAD_DIR,
    AttachmentMetadata,
    AttachmentStorageManager,
    detect_mime_type,
    is_valid_room_name,
    validate_room_name,
)
from cluster_chat.rooms import (
    DEFAULT_DATA_DIR,
    STANDARD_ROOMS,
    RoomManager,
)
from cluster_chat.telegram_bridge import TelegramBridge

logger = logging.getLogger("cluster_chat.server")

# Default web directory relative to this file
DEFAULT_WEB_DIR = Path(__file__).resolve().parent / "web"


def _str_to_bool(val: Any, default: bool = True) -> bool:
    """Convert form or JSON string/bool value to boolean."""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in ("true", "1", "yes", "y", "on"):
        return True
    if s in ("false", "0", "no", "n", "off"):
        return False
    return default


class ClusterChatRequestHandler(http.server.BaseHTTPRequestHandler):
    """HTTP Request handler for Cluster Chat API and Web UI."""

    server: "ThreadingClusterChatServer"  # Type hint for attached server

    def setup(self) -> None:
        self._pending_cookies: list[str] = []
        super().setup()

    # Suppress default log messages to stderr unless configured
    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    def is_trusted_proxy(self) -> bool:
        """Check if incoming peer is allowed to set reverse-proxy headers."""
        trust_val = os.environ.get("CLUSTER_CHAT_TRUST_PROXY", "true").strip().lower()
        if trust_val in ("0", "false", "no"):
            return False
        trusted_proxies = os.environ.get("TRUSTED_PROXIES", "").strip()
        if trusted_proxies:
            client_ip = self.client_address[0] if self.client_address else ""
            allowed = [p.strip() for p in trusted_proxies.split(",") if p.strip()]
            return client_ip in allowed
        return True

    def get_client_ip(self) -> str:
        """Extract client IP, trusting X-Forwarded-For if behind a trusted reverse proxy/relay."""
        if self.is_trusted_proxy():
            xff = self.headers.get("X-Forwarded-For")
            if xff:
                return xff.split(",")[0].strip()
        return self.client_address[0] if self.client_address else "127.0.0.1"

    def is_secure_request(self) -> bool:
        """Determine if request is over HTTPS directly or through reverse-proxy / domestic relay."""
        if self.is_trusted_proxy():
            xfp = self.headers.get("X-Forwarded-Proto")
            if xfp:
                return xfp.split(",")[0].strip().lower() == "https"
        return getattr(self.server, "is_ssl", False)

    def get_request_host(self) -> str:
        """Extract host:port, trusting X-Forwarded-Host if behind a trusted reverse proxy/relay."""
        if self.is_trusted_proxy():
            xfh = self.headers.get("X-Forwarded-Host")
            if xfh:
                return xfh.split(",")[0].strip()
        host = self.headers.get("Host")
        if host:
            return host.strip()
        srv_host, srv_port = self.server.server_address[:2]
        return f"{srv_host}:{srv_port}"

    def get_base_url(self) -> str:
        """Derive base URL dynamically from CLUSTER_CHAT_PUBLIC_URL or reverse-proxy/Host headers."""
        env_url = (os.environ.get("CLUSTER_CHAT_PUBLIC_URL") or os.environ.get("CLUSTER_CHAT_PUBLIC_BASE_URL", "")).strip()
        if env_url:
            return env_url.rstrip("/")
        proto = "https" if self.is_secure_request() else "http"
        return f"{proto}://{self.get_request_host()}"

    def parse_cookies(self) -> dict[str, str]:
        """Parse incoming Cookie header into dictionary."""
        cookie_header = self.headers.get("Cookie", "")
        if not cookie_header:
            return {}
        cookies: dict[str, str] = {}
        for item in cookie_header.split(";"):
            if "=" in item:
                k, v = item.strip().split("=", 1)
                cookies[k.strip()] = v.strip()
        return cookies

    def set_cookie(
        self,
        name: str,
        value: str,
        max_age: int | None = 86400,
        path: str = "/",
        http_only: bool = True,
        same_site: str = "Lax",
        secure: bool | None = None,
    ) -> None:
        """Set cookie omitting Domain (host-agnostic) and only setting Secure when HTTPS."""
        if secure is None:
            secure = self.is_secure_request()
        parts = [f"{name}={value}", f"Path={path}"]
        if max_age is not None:
            parts.append(f"Max-Age={max_age}")
        if http_only:
            parts.append("HttpOnly")
        if same_site:
            parts.append(f"SameSite={same_site}")
        if secure:
            parts.append("Secure")
        # NOTE: Fixed 'Domain' attribute is deliberately omitted to allow
        # seamless cookie binding to direct raw VPS IP:port or domestic relays!
        cookie_line = "; ".join(parts)
        self._pending_cookies.append(cookie_line)

    def _set_cors_headers(self) -> None:
        """Configure CORS to accept direct IP origins, same-origin, or configured ALLOWED_ORIGINS."""
        origin = self.headers.get("Origin")
        allowed_origins_env = os.environ.get("ALLOWED_ORIGINS", "").strip()

        if not origin:
            self.send_header("Access-Control-Allow-Origin", "*")
        else:
            req_host = self.get_request_host().lower()
            parsed_origin = urllib.parse.urlparse(origin)
            origin_host = parsed_origin.netloc.lower()
            is_same_host = (origin_host == req_host)

            allowed_list = [o.strip() for o in allowed_origins_env.split(",") if o.strip()]
            origin_allowed = (
                is_same_host
                or (not allowed_origins_env)
                or (allowed_origins_env == "*")
                or (origin in allowed_list)
                or ("*" in allowed_list)
            )

            if origin_allowed:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Credentials", "true")
                self.send_header("Vary", "Origin")
            elif allowed_list:
                self.send_header("Access-Control-Allow-Origin", allowed_list[0])
            else:
                self.send_header("Access-Control-Allow-Origin", "*")

        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD, PUT, DELETE")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Authorization, X-Requested-With, Cache-Control, Pragma, "
            "X-Forwarded-Proto, X-Forwarded-Host, X-Forwarded-For",
        )
        self.send_header("Access-Control-Max-Age", "86400")

    def _send_json(self, data: dict[str, Any], status: int = 200, cache_control: str | None = None) -> None:
        """Send JSON response with proper headers."""
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if cache_control:
            self.send_header("Cache-Control", cache_control)
            if "no-cache" in cache_control.lower() or "no-store" in cache_control.lower():
                self.send_header("Pragma", "no-cache")
        for cookie_str in self._pending_cookies:
            self.send_header("Set-Cookie", cookie_str)
        self._pending_cookies.clear()
        self._set_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, error_msg: str, status: int = 400) -> None:
        """Send standard error JSON response."""
        self._send_json({"ok": False, "error": error_msg}, status=status)

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests."""
        self.send_response(204)
        self._set_cors_headers()
        self.end_headers()

    def do_HEAD(self) -> None:
        """Handle HEAD requests for health probes and static file headers."""
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path in (
            "/api/cluster-chat/health",
            "/health",
            "/api/health",
            "/api/ping",
            "/ping",
            "/api/cluster-chat/ping",
        ):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self._set_cors_headers()
            self.end_headers()
            return
        self.send_response(200)
        self._set_cors_headers()
        self.end_headers()

    def _stream_file_response(self, safe_path: Path, query: dict[str, list[str]]) -> None:
        """Stream a validated safe file path to the client with range/disposition headers."""
        download_val = query.get("download", ["0"])[0].lower()
        is_download = download_val in ("1", "true", "yes")

        file_size = safe_path.stat().st_size
        mime_type = detect_mime_type(safe_path.name)
        disposition = "attachment" if is_download else "inline"
        safe_filename = re.sub(r'[\r\n"\\\x00-\x1f]', '_', safe_path.name)

        self.send_response(200)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(file_size))
        self.send_header("Content-Disposition", f'{disposition}; filename="{safe_filename}"')
        self.send_header("Cache-Control", "public, max-age=86400")
        self._set_cors_headers()
        self.end_headers()

        chunk_size = 64 * 1024
        with open(safe_path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def do_GET(self) -> None:
        """Handle GET requests for files, messages, rooms, and Web UI."""
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = urllib.parse.parse_qs(parsed.query)

        # 1. Health check & transport probe
        if path in (
            "/api/cluster-chat/health",
            "/health",
            "/api/health",
            "/api/ping",
            "/ping",
            "/api/cluster-chat/ping",
        ):
            host = self.get_request_host()
            self._send_json(
                {
                    "ok": True,
                    "status": "ok",
                    "service": "cluster-chat",
                    "host": host,
                    "transport": ["ws", "sse", "poll"],
                    "direct_ip_supported": True,
                },
                cache_control="no-store, no-cache, must-revalidate",
            )
            return

        # 2. Session / auth status
        if path in ("/api/auth/session", "/api/auth/status", "/api/session"):
            cookies = self.parse_cookies()
            auth_header = self.headers.get("Authorization", "")
            sess_id = cookies.get("cluster_chat_session") or (
                auth_header.replace("Bearer ", "").strip() if auth_header.startswith("Bearer ") else None
            )
            session_data = self.server.sessions.get(sess_id) if (sess_id and hasattr(self.server, "sessions")) else None
            is_auth = (session_data is not None) or bool(sess_id)
            user = session_data.get("user") if session_data else None
            resp_payload: dict[str, Any] = {
                "ok": True,
                "authenticated": is_auth,
                "session_id": sess_id,
                "direct_ip_supported": True,
            }
            if user:
                resp_payload["user"] = user
            self._send_json(
                resp_payload,
                cache_control="no-store, no-cache, must-revalidate",
            )
            return

        # 3. Room list endpoint
        if path in ("/api/cluster-chat/rooms", "/api/rooms"):
            rooms = self.server.room_manager.list_rooms()
            self._send_json({"ok": True, "rooms": rooms})
            return

        # 4. Room messages & incremental polling endpoint
        # Matches /api/cluster-chat/rooms/<room>/messages, /api/rooms/<room>/messages,
        # or root endpoints /api/cluster-chat/messages, /api/messages, /messages
        room_messages_match = re.match(r"^/(?:api/cluster-chat|api)/rooms/([^/]+)/messages$", path)
        is_direct_messages = path in ("/api/cluster-chat/messages", "/api/messages", "/messages")
        if room_messages_match or is_direct_messages:
            room = room_messages_match.group(1) if room_messages_match else query.get("room", ["main"])[0]
            if not is_valid_room_name(room):
                self._send_error_json(f"Invalid room name '{room}'", status=400)
                return

            try:
                limit = int(query.get("limit", [50])[0])
            except (ValueError, IndexError):
                limit = 50

            before_id = query.get("before_id", [None])[0]
            target_agent = query.get("target_agent", [None])[0]
            since = query.get("since", [None])[0]
            after = query.get("after", [None])[0]

            messages = self.server.room_manager.get_messages(
                room=room,
                limit=limit,
                before_id=before_id,
                target_agent=target_agent,
                since=since,
                after=after,
            )
            self._send_json(
                {
                    "ok": True,
                    "room": room,
                    "messages": messages,
                    "count": len(messages),
                },
                cache_control="no-store, no-cache, must-revalidate",
            )
            return

        # 5. Safe file serving:
        # a. Direct attachment ID lookup: /api/cluster-chat/attachments/<id>, /api/attachments/<id>, /attachments/<id>
        att_match = re.match(r"^/(?:api/cluster-chat/attachments|api/attachments|attachments)/([^/]+)$", path)
        if att_match:
            att_id = att_match.group(1)
            att_meta = self.server.room_manager.find_attachment_in_rooms(att_id)
            if not att_meta:
                self._send_error_json("Attachment not found", status=404)
                return
            local_path_str = att_meta.get("local_path", "")
            safe_path = Path(local_path_str).resolve()
            if not safe_path.is_relative_to(self.server.storage.upload_dir.resolve()) or not safe_path.is_file():
                self._send_error_json("Attachment file not found on disk", status=404)
                return
            self._stream_file_response(safe_path=safe_path, query=query)
            return

        # b. Room/date/filename path: /api/cluster-chat/files/<room>/<date>/<filename>, /files/..., /uploads/...
        file_match = re.match(r"^/(?:api/cluster-chat/files|api/files|files|uploads|api/uploads)/([^/]+)/([^/]+)/([^/]+)$", path)
        if file_match:
            room = file_match.group(1)
            date_str = file_match.group(2)
            stored_filename = file_match.group(3)

            try:
                safe_path = self.server.storage.resolve_safe_file_path(
                    room=room,
                    date_str=date_str,
                    stored_filename=stored_filename,
                    must_exist=True,
                )
            except ValueError as ve:
                self._send_error_json(str(ve), status=400)
                return
            except FileNotFoundError:
                self._send_error_json("File not found", status=404)
                return

            self._stream_file_response(safe_path=safe_path, query=query)
            return

        # 5. Static Web UI files
        if path in ("", "/", "/index.html"):
            self._serve_static_file("index.html")
            return
        if path == "/app.js":
            self._serve_static_file("app.js")
            return
        if path == "/style.css":
            self._serve_static_file("style.css")
            return
        if path.startswith("/web/"):
            rel = path[len("/web/"):]
            self._serve_static_file(rel)
            return

        # Fallback 404
        self._send_error_json("Not Found", status=404)

    def _serve_static_file(self, rel_path: str) -> None:
        """Safely serve a static file from web_dir."""
        web_dir = self.server.web_dir
        if not web_dir or not web_dir.exists():
            self._send_error_json("Web UI directory not configured or not found", status=404)
            return

        clean_rel = rel_path.lstrip("/").replace("\\", "/")
        target_path = (web_dir / clean_rel).resolve()

        if not target_path.is_relative_to(web_dir.resolve()):
            self._send_error_json("Forbidden", status=403)
            return

        if not target_path.is_file():
            self._send_error_json("Static file not found", status=404)
            return

        guessed_mime, _ = mimetypes.guess_type(target_path.name)
        if target_path.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        elif target_path.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif target_path.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        elif target_path.suffix == ".json":
            content_type = "application/json; charset=utf-8"
        else:
            content_type = guessed_mime or "application/octet-stream"

        data = target_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self._set_cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        """Handle POST requests for uploads and message posting."""
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        # Check Content-Length header
        cl_header = self.headers.get("Content-Length")
        if cl_header is None:
            self._send_error_json("Missing Content-Length header", status=411)
            return

        try:
            content_length = int(cl_header)
        except ValueError:
            self._send_error_json("Invalid Content-Length header", status=400)
            return

        # Check max size limit (+ 512KB overhead allowance for multipart encoding)
        max_allowed = self.server.storage.max_bytes + (512 * 1024)
        if content_length > max_allowed:
            # Drain at most 64KB so immediate socket writes don't instantly reset,
            # but do NOT block the thread on arbitrarily large payloads (DoS prevention)
            try:
                drain_bytes = min(content_length, 64 * 1024)
                self.rfile.read(drain_bytes)
            except Exception:
                pass
            self.close_connection = True
            self._send_error_json(
                f"Payload size ({content_length} bytes) exceeds maximum allowed upload size of "
                f"{self.server.storage.max_upload_mb} MB",
                status=413,
            )
            return

        # 0. Session and auth endpoints
        if path in ("/api/auth/session", "/api/auth/login", "/api/login"):
            raw_body = self.rfile.read(content_length)
            user = None
            if raw_body:
                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                    if isinstance(payload, dict):
                        user = payload.get("user") or payload.get("username")
                        if user is not None:
                            user = str(user).strip()
                except Exception:
                    pass
            session_id = f"sess_{uuid.uuid4().hex}"
            if hasattr(self.server, "sessions"):
                self.server.sessions[session_id] = {
                    "session_id": session_id,
                    "user": user,
                }
            self.set_cookie("cluster_chat_session", session_id)
            resp_payload: dict[str, Any] = {
                "ok": True,
                "authenticated": True,
                "session_id": session_id,
            }
            if user:
                resp_payload["user"] = user
            self._send_json(
                resp_payload,
                cache_control="no-store, no-cache, must-revalidate",
            )
            return

        if path in ("/api/auth/logout", "/api/logout"):
            cookies = self.parse_cookies()
            sess_id = cookies.get("cluster_chat_session")
            if sess_id and hasattr(self.server, "sessions") and sess_id in self.server.sessions:
                self.server.sessions.pop(sess_id, None)
            self.set_cookie("cluster_chat_session", "", max_age=0)
            self._send_json(
                {"ok": True, "authenticated": False, "logged_out": True},
                cache_control="no-store, no-cache, must-revalidate",
            )
            return

        # 1. Upload endpoints: /api/cluster-chat/upload, /api/rooms/<room>/upload, /api/cluster-chat/rooms/<room>/upload
        upload_match = re.match(r"^/(?:api/cluster-chat|api)/rooms/([^/]+)/upload$", path)
        is_upload = (path in ("/api/cluster-chat/upload", "/api/upload")) or bool(upload_match)

        if is_upload:
            url_room = upload_match.group(1) if upload_match else None
            self._handle_upload(url_room=url_room, content_length=content_length)
            return

        # 2. Post message endpoint: /api/cluster-chat/rooms/<room>/messages or /api/rooms/<room>/messages
        messages_match = re.match(r"^/(?:api/cluster-chat|api)/rooms/([^/]+)/messages$", path)
        is_direct_post = path in ("/api/cluster-chat/messages", "/api/messages", "/messages")
        if messages_match or is_direct_post:
            room = messages_match.group(1) if messages_match else None
            self._handle_post_message(room=room, content_length=content_length)
            return

        # 3. Telegram Webhook endpoint: /api/cluster-chat/telegram/webhook or /api/telegram/webhook
        if path in ("/api/cluster-chat/telegram/webhook", "/api/telegram/webhook"):
            self._handle_telegram_webhook(content_length=content_length)
            return

        self._send_error_json("Not Found", status=404)

    def _handle_upload(self, url_room: str | None, content_length: int) -> None:
        """Handle file and photo upload via multipart/form-data or application/json."""
        content_type_header = self.headers.get("Content-Type", "")

        raw_body = self.rfile.read(content_length)
        saved_attachments: list[dict[str, Any]] = []

        room = url_room
        sender = ""
        text = ""
        target_agent: str | None = None
        forward_telegram = True

        if "multipart/form-data" in content_type_header.lower():
            # Parse multipart via email.parser.BytesParser
            header_bytes = f"Content-Type: {content_type_header}\r\n\r\n".encode("latin-1")
            msg = BytesParser().parsebytes(header_bytes + raw_body)

            if not msg.is_multipart():
                self._send_error_json("Malformed multipart/form-data payload", status=400)
                return

            for part in msg.get_payload():
                cd = part.get("Content-Disposition", "")
                name = part.get_param("name", header="content-disposition")
                filename = part.get_filename()

                if filename:
                    # File attachment
                    file_bytes = part.get_payload(decode=True)
                    if file_bytes is None:
                        continue
                    part_mime = part.get_content_type()
                    # If default/fallback MIME type was detected, pass None to allow extension autodetection
                    mime_override = None if part_mime in ("application/octet-stream", "text/plain") else part_mime

                    # We will save files after room and sender are parsed
                    # Store tuple for second pass
                    saved_attachments.append({
                        "_pending_bytes": file_bytes,
                        "_filename": filename,
                        "_mime": mime_override,
                    })
                elif name:
                    clean_name = name.strip()
                    val = part.get_payload(decode=True)
                    val_str = val.decode("utf-8", errors="replace").strip() if val else ""

                    if clean_name == "room" and not room:
                        room = val_str
                    elif clean_name == "sender":
                        sender = val_str
                    elif clean_name in ("text", "caption"):
                        text = val_str
                    elif clean_name == "target_agent":
                        target_agent = val_str if val_str else None
                    elif clean_name == "forward_telegram":
                        forward_telegram = _str_to_bool(val_str, default=True)

            # Validate room and sender before persisting attachments
            if not room:
                self._send_error_json("Missing 'room' parameter", status=400)
                return
            try:
                validated_room = validate_room_name(room)
            except ValueError as ve:
                self._send_error_json(str(ve), status=400)
                return

            if not sender:
                self._send_error_json("Missing 'sender' parameter", status=400)
                return

            # Now save all pending file bytes
            actual_saved: list[dict[str, Any]] = []
            for item in saved_attachments:
                if "_pending_bytes" in item:
                    try:
                        meta = self.server.storage.save_bytes(
                            content=item["_pending_bytes"],
                            filename=item["_filename"],
                            room=validated_room,
                            sender=sender,
                            mime_type=item["_mime"],
                            target_agent=target_agent,
                        )
                        actual_saved.append(meta)
                    except ValueError as ve:
                        self._send_error_json(str(ve), status=400)
                        return
            saved_attachments = actual_saved

        elif "application/json" in content_type_header.lower():
            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                self._send_error_json(f"Invalid JSON body: {e}", status=400)
                return

            if not isinstance(payload, dict):
                self._send_error_json("JSON body must be an object", status=400)
                return

            room = room or payload.get("room")
            sender = payload.get("sender", "")
            text = payload.get("text") or payload.get("caption", "")
            target_agent = payload.get("target_agent")
            forward_telegram = _str_to_bool(payload.get("forward_telegram"), default=True)

            if not room:
                self._send_error_json("Missing 'room' parameter in JSON payload", status=400)
                return
            try:
                validated_room = validate_room_name(room)
            except ValueError as ve:
                self._send_error_json(str(ve), status=400)
                return

            if not sender or not str(sender).strip():
                self._send_error_json("Missing 'sender' parameter in JSON payload", status=400)
                return
            sender = str(sender).strip()

            # Handle base64 file upload in root object
            if "content_base64" in payload:
                raw_fn = payload.get("filename", "attachment.bin")
                raw_mime = payload.get("mime_type")
                try:
                    file_bytes = base64.b64decode(payload["content_base64"])
                    meta = self.server.storage.save_bytes(
                        content=file_bytes,
                        filename=raw_fn,
                        room=validated_room,
                        sender=sender,
                        mime_type=raw_mime,
                        target_agent=target_agent,
                    )
                    saved_attachments.append(meta)
                except Exception as ex:
                    self._send_error_json(f"Failed to process content_base64: {ex}", status=400)
                    return

            # Handle local_path file forwarding in root object
            elif "local_path" in payload:
                src_path = payload["local_path"]
                chosen_fn = payload.get("filename")
                raw_mime = payload.get("mime_type")
                try:
                    meta = self.server.storage.save_file_from_path(
                        src_path=src_path,
                        room=validated_room,
                        sender=sender,
                        filename=chosen_fn,
                        target_agent=target_agent,
                        mime_type=raw_mime,
                    )
                    saved_attachments.append(meta)
                except (FileNotFoundError, ValueError) as err:
                    self._send_error_json(str(err), status=400)
                    return

            # Also support 'attachments' list in JSON payload
            if "attachments" in payload and isinstance(payload["attachments"], list):
                for att in payload["attachments"]:
                    if not isinstance(att, dict):
                        continue
                    if "content_base64" in att:
                        fn = att.get("filename", "attachment.bin")
                        mime = att.get("mime_type")
                        try:
                            fbytes = base64.b64decode(att["content_base64"])
                            meta = self.server.storage.save_bytes(
                                content=fbytes,
                                filename=fn,
                                room=validated_room,
                                sender=sender,
                                mime_type=mime,
                                target_agent=target_agent,
                            )
                            saved_attachments.append(meta)
                        except Exception as ex:
                            self._send_error_json(f"Failed to process attachment base64: {ex}", status=400)
                            return
                    elif "local_path" in att:
                        try:
                            meta = self.server.storage.save_file_from_path(
                                src_path=att["local_path"],
                                room=validated_room,
                                sender=sender,
                                filename=att.get("filename"),
                                target_agent=target_agent,
                                mime_type=att.get("mime_type"),
                            )
                            saved_attachments.append(meta)
                        except (FileNotFoundError, ValueError) as err:
                            self._send_error_json(str(err), status=400)
                            return
                    elif "attachment_id" in att and "local_path" in att:
                        # Pre-existing attachment metadata object
                        saved_attachments.append(att)

        else:
            self._send_error_json(
                f"Unsupported Content-Type '{content_type_header}'. Expected multipart/form-data or application/json",
                status=415,
            )
            return

        # Must have text or attachments
        if not text.strip() and not saved_attachments:
            self._send_error_json("Message must have non-empty text or at least one file attachment", status=400)
            return

        # Persist room message
        try:
            msg_dict = self.server.room_manager.post_message(
                room=validated_room,
                sender=sender,
                text=text,
                attachments=saved_attachments,
                target_agent=target_agent,
            )
        except ValueError as ve:
            self._send_error_json(str(ve), status=400)
            return

        # Trigger outbound notification hooks (Telegram / Agent bus)
        self.server.trigger_message_hook(msg_dict, forward_telegram=forward_telegram)

        self._send_json(
            {
                "ok": True,
                "message": msg_dict,
                "attachments": saved_attachments,
            },
            status=201,
        )

    def _handle_post_message(self, room: str | None, content_length: int) -> None:
        """Handle standard text/attachment message posting to a room."""
        content_type_header = self.headers.get("Content-Type", "")
        if "application/json" not in content_type_header.lower():
            self._send_error_json("Expected Content-Type: application/json", status=415)
            return

        raw_body = self.rfile.read(content_length)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self._send_error_json(f"Invalid JSON body: {e}", status=400)
            return

        if not isinstance(payload, dict):
            self._send_error_json("JSON body must be an object", status=400)
            return

        target_room = room or payload.get("room", "main")
        try:
            validated_room = validate_room_name(target_room)
        except ValueError as ve:
            self._send_error_json(str(ve), status=400)
            return

        sender = payload.get("sender")
        if not sender or not str(sender).strip():
            self._send_error_json("Missing 'sender' parameter", status=400)
            return

        text = payload.get("text", "")
        target_agent = payload.get("target_agent")
        attachments = payload.get("attachments", [])
        forward_telegram = _str_to_bool(payload.get("forward_telegram"), default=True)

        try:
            msg_dict = self.server.room_manager.post_message(
                room=validated_room,
                sender=str(sender).strip(),
                text=str(text),
                attachments=attachments,
                target_agent=str(target_agent).strip() if target_agent else None,
            )
        except (ValueError, TypeError) as err:
            self._send_error_json(str(err), status=400)
            return

        self.server.trigger_message_hook(msg_dict, forward_telegram=forward_telegram)
        self._send_json({"ok": True, "message": msg_dict}, status=201)

    def _handle_telegram_webhook(self, content_length: int) -> None:
        """Handle incoming Telegram webhook update."""
        content_type_header = self.headers.get("Content-Type", "")
        if "application/json" not in content_type_header.lower():
            self._send_error_json("Expected Content-Type: application/json", status=415)
            return

        raw_body = self.rfile.read(content_length)
        try:
            update = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self._send_error_json(f"Invalid JSON body: {e}", status=400)
            return

        if not isinstance(update, dict):
            self._send_error_json("Update payload must be a JSON object", status=400)
            return

        try:
            result = self.server.telegram_bridge.handle_telegram_update(update)
            if result is not None:
                self._send_json({"ok": True, "result": result}, status=200)
            else:
                self._send_json({"ok": True, "handled": False}, status=200)
        except Exception as err:
            logger.exception("Error processing Telegram webhook update: %s", err)
            self._send_error_json(f"Internal error processing Telegram update: {err}", status=500)


class ThreadingClusterChatServer(http.server.ThreadingHTTPServer):
    """Multi-threaded HTTP server holding storage and room manager references."""

    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        RequestHandlerClass: type[http.server.BaseHTTPRequestHandler],
        storage: AttachmentStorageManager,
        room_manager: RoomManager,
        web_dir: Path,
        on_message_callback: Callable[[dict[str, Any], bool], None] | None = None,
        telegram_bridge: TelegramBridge | None = None,
    ) -> None:
        super().__init__(server_address, RequestHandlerClass)
        self.storage = storage
        self.room_manager = room_manager
        self.web_dir = web_dir
        self.on_message_callback = on_message_callback
        self.is_ssl = False
        self.sessions: dict[str, dict[str, Any]] = {}
        self.telegram_bridge: TelegramBridge = (
            telegram_bridge
            if telegram_bridge is not None
            else TelegramBridge(
                storage=self.storage,
                room_manager=self.room_manager,
                data_dir=self.room_manager.data_dir,
            )
        )

    def trigger_message_hook(self, message: dict[str, Any], forward_telegram: bool = True) -> None:
        """Execute message callback hook if registered and forward to Telegram."""
        if forward_telegram and self.telegram_bridge is not None:
            try:
                self.telegram_bridge.send_message_to_telegram(message)
            except Exception as e:
                logger.warning("Failed to forward message to Telegram: %s", e)
        if self.on_message_callback is not None:
            try:
                self.on_message_callback(message, forward_telegram)
            except Exception as e:
                logger.exception("Error in outbound on_message_callback: %s", e)


class ClusterChatServer:
    """High-level Cluster Chat HTTP Server manager."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        storage: AttachmentStorageManager | None = None,
        room_manager: RoomManager | None = None,
        web_dir: str | Path | None = None,
        on_message_callback: Callable[[dict[str, Any], bool], None] | None = None,
        telegram_bridge: TelegramBridge | None = None,
    ) -> None:
        self.host = host if host is not None else os.environ.get("CLUSTER_CHAT_HOST", "0.0.0.0")
        raw_port = port if port is not None else int(os.environ.get("CLUSTER_CHAT_PORT", "8080"))
        self.requested_port = raw_port

        self.storage = storage if storage is not None else AttachmentStorageManager()
        self.room_manager = room_manager if room_manager is not None else RoomManager()

        if web_dir is not None:
            self.web_dir = Path(web_dir).resolve()
        else:
            self.web_dir = Path(os.environ.get("CLUSTER_CHAT_WEB_DIR", DEFAULT_WEB_DIR)).resolve()

        self.on_message_callback = on_message_callback
        self.is_ssl = False
        self.sessions: dict[str, dict[str, Any]] = {}
        self.telegram_bridge: TelegramBridge = (
            telegram_bridge
            if telegram_bridge is not None
            else TelegramBridge(
                storage=self.storage,
                room_manager=self.room_manager,
                data_dir=self.room_manager.data_dir,
            )
        )

        self.httpd = ThreadingClusterChatServer(
            (self.host, self.requested_port),
            ClusterChatRequestHandler,
            storage=self.storage,
            room_manager=self.room_manager,
            web_dir=self.web_dir,
            on_message_callback=self.on_message_callback,
            telegram_bridge=self.telegram_bridge,
        )

        self._thread: threading.Thread | None = None
        self._is_running = False

    @property
    def port(self) -> int:
        """Return the active bound port."""
        return self.httpd.server_address[1]

    def get_url(self) -> str:
        """Return the base HTTP URL for this server."""
        display_host = "127.0.0.1" if self.host in ("0.0.0.0", "") else self.host
        return f"http://{display_host}:{self.port}"

    def start(self, daemon: bool = True) -> None:
        """Start the server. If daemon=True, runs in a background thread."""
        if self._is_running:
            return
        self._is_running = True
        if daemon:
            self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
            self._thread.start()
        else:
            self.httpd.serve_forever()

    def stop(self) -> None:
        """Stop and cleanly shutdown the server."""
        if not self._is_running:
            return
        self._is_running = False
        self.httpd.shutdown()
        self.httpd.server_close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def __enter__(self) -> "ClusterChatServer":
        self.start(daemon=True)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()


def main() -> None:
    """CLI entrypoint for running the Cluster Chat bridge HTTP server."""
    parser = argparse.ArgumentParser(description="Cluster Chat HTTP Bridge Server")
    parser.add_argument("--host", default=os.environ.get("CLUSTER_CHAT_HOST", "0.0.0.0"), help="Host address to bind")
    parser.add_argument("--port", type=int, default=int(os.environ.get("CLUSTER_CHAT_PORT", "8080")), help="Port number")
    parser.add_argument("--upload-dir", default=None, help="Attachment uploads storage directory")
    parser.add_argument("--data-dir", default=None, help="Cluster chat messages storage directory")
    parser.add_argument("--web-dir", default=None, help="Web UI static assets directory")
    parser.add_argument("--public-url", default=None, help="Public base URL prefix for attachment links")

    args = parser.parse_args()

    storage = AttachmentStorageManager(
        upload_dir=args.upload_dir,
        public_base_url=args.public_url,
    )
    room_mgr = RoomManager(data_dir=args.data_dir)

    server = ClusterChatServer(
        host=args.host,
        port=args.port,
        storage=storage,
        room_manager=room_mgr,
        web_dir=args.web_dir,
    )

    print(f"Starting Cluster Chat Server on {server.get_url()} (Web UI: {server.web_dir})")
    try:
        server.start(daemon=False)
    except KeyboardInterrupt:
        print("\nShutting down Cluster Chat Server...")
        server.stop()


if __name__ == "__main__":
    main()
