#!/usr/bin/env python3
"""scripts/web_bridge_runner.py

GitHub Actions Runner Web-Bridge and Safe URL Fetcher for Cluster-Chat.
Fetches target external URLs on behalf of Mohammad and LeadDesk cluster agents,
enforces strict SSRF protections, extracts clean Markdown or packages binary
attachments, and publishes results back to the GitHub relay queue.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import html
from html.parser import HTMLParser
import http.client
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import socket
import ssl
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

# Ensure repo root is on sys.path so cluster_chat is importable
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import cluster_chat.relay_protocol as rp

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [web-bridge] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%SZ",
)
logger = logging.getLogger("web_bridge_runner")


# ==============================================================================
# Constants & SSRF Blacklists
# ==============================================================================

DEFAULT_TIMEOUT_SECONDS: int = 20
CHUNK_SIZE_BYTES: int = 64 * 1024  # 64 KB streaming chunks
DEFAULT_USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

BLOCKED_HOSTNAMES: frozenset[str] = frozenset({
    "localhost",
    "metadata.google.internal",
    "metadata",
    "instance-data",
})

BLOCKED_SUFFIXES: tuple[str, ...] = (
    ".localhost",
    ".local",
    ".internal",
    ".lan",
    ".home",
    ".corp",
)

BLOCKED_METADATA_IPS: frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address] = frozenset({
    ipaddress.ip_address("169.254.169.254"),  # AWS/GCP/Azure/OpenStack metadata
    ipaddress.ip_address("100.100.100.200"),  # Alibaba Cloud metadata
    ipaddress.ip_address("255.255.255.255"),  # Broadcast
    ipaddress.ip_address("0.0.0.0"),          # Wildcard
})

CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")

BINARY_EXTENSIONS: frozenset[str] = frozenset({
    ".pdf", ".zip", ".tar.gz", ".tgz", ".png", ".jpg", ".jpeg", ".gif",
    ".webp", ".csv", ".bin", ".mp4", ".mp3", ".docx", ".xlsx", ".tar",
    ".gz", ".7z", ".exe", ".iso", ".dmg", ".apk",
})

HTML_MIME_TYPES: frozenset[str] = frozenset({
    "text/html",
    "application/xhtml+xml",
})


# ==============================================================================
# Exceptions
# ==============================================================================

class SSRFError(ValueError):
    """Raised when a target URL violates SSRF safety constraints."""
    pass


# ==============================================================================
# SSRF & Network Safety Guard
# ==============================================================================

# Thread-safe cache of hostname -> validated safe IP address (prevents DNS rebinding / TOCTOU)
_PINNED_RESOLVED_IPS: dict[str, str] = {}


class PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection connecting directly to verified IP to prevent DNS rebinding."""

    def connect(self):
        target_ip = _PINNED_RESOLVED_IPS.get(self.host.lower())
        dest = (target_ip, self.port) if target_ip else (self.host, self.port)
        self.sock = self._create_connection(dest, self.timeout, self.source_address)


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection connecting to verified IP while preserving hostname for TLS SNI."""

    def connect(self):
        target_ip = _PINNED_RESOLVED_IPS.get(self.host.lower())
        dest = (target_ip, self.port) if target_ip else (self.host, self.port)
        sock = self._create_connection(dest, self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class PinnedHTTPHandler(urllib.request.HTTPHandler):
    """Urllib handler dispatching through PinnedHTTPConnection."""

    def http_open(self, req):
        return self.do_open(PinnedHTTPConnection, req)


class PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    """Urllib handler dispatching through PinnedHTTPSConnection."""

    def https_open(self, req):
        return self.do_open(PinnedHTTPSConnection, req)


def validate_safe_url(url: str) -> str:
    """Validate that a URL is safe to fetch and does not target internal assets.

    Enforces:
      - Only 'http' and 'https' schemes.
      - Non-empty hostname.
      - Blocks localhost, .local, .internal, and cloud metadata hostnames.
      - Resolves DNS for all A and AAAA records.
      - Verifies every resolved IP:
        Rejects if private (RFC 1918), loopback (127.0.0.0/8, ::1),
        link-local (169.254.0.0/16, fe80::/10), multicast, reserved,
        unspecified, CGNAT (100.64.0.0/10), or cloud metadata services.

    Returns the stripped, validated URL string or raises SSRFError.
    """
    if not url or not isinstance(url, str):
        raise SSRFError("URL must be a non-empty string.")

    cleaned_url = url.strip()
    try:
        parsed = urllib.parse.urlsplit(cleaned_url)
    except Exception as err:
        raise SSRFError(f"Malformed URL: {err}") from err

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise SSRFError(f"Disallowed scheme '{parsed.scheme}'. Only http:// and https:// are permitted.")

    hostname = parsed.hostname
    if not hostname:
        raise SSRFError("URL must contain a valid hostname.")

    hostname_lower = hostname.lower()
    if hostname_lower in BLOCKED_HOSTNAMES or any(hostname_lower.endswith(sfx) for sfx in BLOCKED_SUFFIXES):
        raise SSRFError(f"Hostname '{hostname}' is blocked by SSRF security policy.")

    port = parsed.port or (443 if scheme == "https" else 80)
    if not (1 <= port <= 65535):
        raise SSRFError(f"Invalid port number: {port}")

    # DNS resolution
    try:
        addr_info = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as err:
        raise SSRFError(f"DNS resolution failed for hostname '{hostname}': {err}") from err

    if not addr_info:
        raise SSRFError(f"Could not resolve any IP address for hostname '{hostname}'.")

    for entry in addr_info:
        sockaddr = entry[4]
        ip_str = sockaddr[0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError as err:
            raise SSRFError(f"Invalid resolved IP '{ip_str}': {err}") from err

        # Normalize IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1)
        if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped:
            ip_obj = ip_obj.ipv4_mapped

        # SSRF checks
        if ip_obj.is_private:
            raise SSRFError(f"Resolved IP {ip_str} is in private address space.")
        if ip_obj.is_loopback:
            raise SSRFError(f"Resolved IP {ip_str} is a loopback address.")
        if ip_obj.is_link_local:
            raise SSRFError(f"Resolved IP {ip_str} is a link-local address.")
        if ip_obj.is_multicast:
            raise SSRFError(f"Resolved IP {ip_str} is a multicast address.")
        if ip_obj.is_reserved:
            raise SSRFError(f"Resolved IP {ip_str} is a reserved address.")
        if ip_obj.is_unspecified:
            raise SSRFError(f"Resolved IP {ip_str} is an unspecified address.")
        if not ip_obj.is_global:
            raise SSRFError(f"Resolved IP {ip_str} is not a globally routable public address.")
        if ip_obj in CGNAT_NETWORK:
            raise SSRFError(f"Resolved IP {ip_str} is in Carrier-Grade NAT (CGNAT) space.")
        if ip_obj in BLOCKED_METADATA_IPS:
            raise SSRFError(f"Resolved IP {ip_str} is a blocked cloud metadata IP.")

    # Pin the first resolved valid IP to prevent DNS rebinding TOCTOU
    _PINNED_RESOLVED_IPS[hostname_lower] = addr_info[0][4][0]

    return cleaned_url


# ==============================================================================
# Safe Redirect Handler
# ==============================================================================

class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Custom HTTP redirect handler re-validating every redirect target against SSRF."""

    def __init__(self, max_redirects: int = 5):
        super().__init__()
        self.max_redirects = max_redirects
        self.redirect_count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.redirect_count += 1
        if self.redirect_count > self.max_redirects:
            raise SSRFError(f"Exceeded maximum allowed redirect count of {self.max_redirects}")

        target_url = urllib.parse.urljoin(req.full_url, newurl)
        validate_safe_url(target_url)
        return super().redirect_request(req, fp, code, msg, headers, target_url)


# ==============================================================================
# HTML to Markdown Extractor
# ==============================================================================

class HTMLToMarkdownParser(HTMLParser):
    """Pure-Python standard library HTML to readable Markdown converter.

    Strips script, style, nav, footer, header, aside, svg, noscript, and template tags.
    Converts headings, paragraphs, lists, code blocks, blockquotes, and links.
    """

    IGNORED_TAGS: frozenset[str] = frozenset({
        "script", "style", "nav", "footer", "header", "aside",
        "noscript", "svg", "template", "iframe",
    })

    def __init__(self, base_url: str = ""):
        super().__init__()
        self.base_url = base_url
        self.title: str = ""
        self._in_title = False
        self._ignored_depth = 0
        self._output_parts: list[str] = []
        self._list_stack: list[dict[str, Any]] = []
        self._in_pre = False
        self._link_stack: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        tag_lower = tag.lower()
        if tag_lower in self.IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth > 0:
            return

        attr_dict = {k.lower(): (v or "") for k, v in attrs if k}

        if tag_lower == "title":
            self._in_title = True
            return

        if tag_lower in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag_lower[1])
            self._ensure_newline(2)
            self._output_parts.append("#" * level + " ")
            return

        if tag_lower == "p":
            self._ensure_newline(2)
            return

        if tag_lower == "br":
            self._output_parts.append("\n")
            return

        if tag_lower == "hr":
            self._ensure_newline(2)
            self._output_parts.append("---\n\n")
            return

        if tag_lower == "pre":
            self._in_pre = True
            self._ensure_newline(2)
            self._output_parts.append("```\n")
            return

        if tag_lower == "code" and not self._in_pre:
            self._output_parts.append("`")
            return

        if tag_lower == "blockquote":
            self._ensure_newline(2)
            self._output_parts.append("> ")
            return

        if tag_lower in ("ul", "ol"):
            self._ensure_newline()
            self._list_stack.append({"type": tag_lower, "count": 1})
            return

        if tag_lower == "li":
            self._ensure_newline()
            indent = "  " * max(0, len(self._list_stack) - 1)
            if self._list_stack and self._list_stack[-1]["type"] == "ol":
                prefix = f"{indent}{self._list_stack[-1]['count']}. "
                self._list_stack[-1]["count"] += 1
            else:
                prefix = f"{indent}- "
            self._output_parts.append(prefix)
            return

        if tag_lower == "a":
            href = attr_dict.get("href", "").strip()
            if href:
                if self.base_url:
                    href = urllib.parse.urljoin(self.base_url, href)
                self._link_stack.append({
                    "href": href,
                    "start_idx": len(self._output_parts),
                })
            return

        if tag_lower in ("b", "strong"):
            self._output_parts.append("**")
            return

        if tag_lower in ("i", "em"):
            self._output_parts.append("*")
            return

    def handle_endtag(self, tag: str):
        tag_lower = tag.lower()
        if tag_lower in self.IGNORED_TAGS:
            if self._ignored_depth > 0:
                self._ignored_depth -= 1
            return
        if self._ignored_depth > 0:
            return

        if tag_lower == "title":
            self._in_title = False
            return

        if tag_lower in ("h1", "h2", "h3", "h4", "h5", "h6", "p"):
            self._ensure_newline(2)
            return

        if tag_lower == "pre":
            self._in_pre = False
            self._ensure_newline()
            self._output_parts.append("```\n\n")
            return

        if tag_lower == "code" and not self._in_pre:
            self._output_parts.append("`")
            return

        if tag_lower in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
            self._ensure_newline(2)
            return

        if tag_lower == "li":
            self._ensure_newline()
            return

        if tag_lower == "a":
            if self._link_stack:
                info = self._link_stack.pop()
                start_idx = info["start_idx"]
                text_slice = "".join(self._output_parts[start_idx:]).strip()
                del self._output_parts[start_idx:]
                href = info["href"]
                display = text_slice or href
                self._output_parts.append(f"[{display}]({href})")
            return

        if tag_lower in ("b", "strong"):
            self._output_parts.append("**")
            return

        if tag_lower in ("i", "em"):
            self._output_parts.append("*")
            return

    def handle_data(self, data: str):
        if self._ignored_depth > 0:
            return

        if self._in_title:
            self.title += data.strip() + " "
            return

        if self._in_pre:
            self._output_parts.append(data)
            return

        text = re.sub(r"[ \t\r\f\v]+", " ", data)
        if text:
            self._output_parts.append(text)

    def _ensure_newline(self, count: int = 1):
        if not self._output_parts:
            return
        combined = "".join(self._output_parts[-3:])
        trailing_newlines = len(combined) - len(combined.rstrip("\n"))
        needed = count - trailing_newlines
        if needed > 0:
            self._output_parts.append("\n" * needed)

    def get_markdown(self) -> str:
        raw = "".join(self._output_parts)
        unescaped = html.unescape(raw)
        return re.sub(r"\n{3,}", "\n\n", unescaped).strip()


# ==============================================================================
# Helper Utilities
# ==============================================================================

def sanitize_filename(filename: str) -> str:
    """Sanitize filename to prevent path traversal and shell issues."""
    base = os.path.basename(filename.strip().replace("\\", "/"))
    clean = re.sub(r"[^a-zA-Z0-9._-]", "_", base)
    clean = clean.lstrip(".")
    if not clean:
        clean = "download.bin"
    return clean[:120]


def format_size_human(size_bytes: int) -> str:
    """Format bytes into readable string (e.g. 1.25 MB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.2f} MB"


# ==============================================================================
# Content Fetcher
# ==============================================================================

def fetch_external_resource(
    url: str,
    mode: str = "markdown",
    max_bytes: int = rp.DEFAULT_WEB_MAX_BYTES_TEXT,
    queue_root: Path | str = "relay_queue",
) -> dict[str, Any]:
    """Fetch external web resource with SSRF protection, size caps, and format extraction.

    Supports:
      - mode='markdown': parses HTML into clean Markdown with title, status, and size.
      - mode='html': returns raw HTML text.
      - mode='file' or binary content: stores file in relay_queue/attachments/ and returns attachment metadata.
    """
    safe_url = validate_safe_url(url)
    queue_dir = Path(queue_root)

    # Normalize mode
    mode_normalized = mode.lower() if mode else "markdown"
    if mode_normalized not in ("markdown", "html", "file"):
        mode_normalized = "markdown"

    # Attachment chunk cap enforcement
    if mode_normalized == "file":
        max_bytes = min(max_bytes or rp.DEFAULT_WEB_MAX_BYTES_FILE, rp.MAX_ATTACHMENT_BYTES)
    else:
        max_bytes = min(max_bytes or rp.DEFAULT_WEB_MAX_BYTES_TEXT, rp.DEFAULT_WEB_MAX_BYTES_FILE)

    req = urllib.request.Request(
        safe_url,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,fa;q=0.8",
        },
    )

    opener = urllib.request.build_opener(
        SafeRedirectHandler(),
        PinnedHTTPHandler(),
        PinnedHTTPSHandler(),
    )

    logger.info(f"Fetching URL '{safe_url}' [mode={mode_normalized}, max_bytes={max_bytes}]")
    with opener.open(req, timeout=DEFAULT_TIMEOUT_SECONDS) as resp:
        url_from_resp = resp.geturl() if hasattr(resp, "geturl") else None
        final_url = str(url_from_resp).strip() if url_from_resp else safe_url
        # Re-validate final URL in case of redirect
        validate_safe_url(final_url)

        status_code = getattr(resp, "status", 200)
        headers_dict = dict(resp.headers)
        content_type_raw = headers_dict.get("Content-Type", "application/octet-stream")
        content_type = content_type_raw.split(";")[0].strip().lower()

        # Stream response up to max_bytes
        chunks: list[bytes] = []
        total_read = 0
        truncated = False

        while True:
            chunk = resp.read(CHUNK_SIZE_BYTES)
            if not chunk:
                break
            remaining = max_bytes - total_read
            if len(chunk) > remaining:
                chunks.append(chunk[:remaining])
                total_read += remaining
                truncated = True
                break
            chunks.append(chunk)
            total_read += len(chunk)
            if total_read >= max_bytes:
                truncated = True
                break

        raw_bytes = b"".join(chunks)

    # Detect if content is binary or should be treated as attachment
    is_binary = (
        mode_normalized == "file"
        or (
            not content_type.startswith("text/")
            and content_type not in ("application/json", "application/xml", "application/javascript")
            and content_type not in HTML_MIME_TYPES
        )
    )

    attachments: list[dict[str, Any]] = []
    title = ""
    body_markdown = ""

    if is_binary:
        # File / Binary Attachment Mode
        if len(raw_bytes) > rp.MAX_ATTACHMENT_BYTES:
            raw_bytes = raw_bytes[:rp.MAX_ATTACHMENT_BYTES]
            truncated = True

        sha256_hex = hashlib.sha256(raw_bytes).hexdigest()
        sha256_prefix = sha256_hex[:8]

        # Extract filename from Content-Disposition or URL path
        filename = ""
        cd_header = headers_dict.get("Content-Disposition", "")
        if "filename=" in cd_header:
            fn_match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd_header, re.IGNORECASE)
            if fn_match:
                filename = fn_match.group(1).strip()

        if not filename:
            path_part = urllib.parse.urlsplit(final_url).path
            filename = os.path.basename(path_part)

        if not filename or filename == "/":
            filename = "download.bin"

        safe_name = sanitize_filename(filename)
        att_id = f"att_{sha256_prefix}_{safe_name}"
        att_filename = f"att_{sha256_prefix}_{safe_name}.json"

        # Save to relay_queue/attachments/
        attachments_dir = queue_dir / "attachments"
        attachments_dir.mkdir(parents=True, exist_ok=True)
        att_file_path = attachments_dir / att_filename

        att_record = {
            "id": att_id,
            "attachment_id": att_id,
            "name": safe_name,
            "filename": safe_name,
            "mime": content_type,
            "mime_type": content_type,
            "size": len(raw_bytes),
            "size_bytes": len(raw_bytes),
            "sha256": sha256_hex,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "data_b64": base64.b64encode(raw_bytes).decode("ascii"),
        }
        with open(att_file_path, "w", encoding="utf-8") as f:
            json.dump(att_record, f, indent=2)

        att_meta = {
            "id": att_id,
            "name": safe_name,
            "mime": content_type,
            "size": len(raw_bytes),
            "sha256": sha256_hex,
        }
        attachments.append(att_meta)

        title = safe_name
        body_markdown = (
            f"### 📎 Web Fetch (Attachment Downloaded): {safe_name}\n"
            f"- **URL:** {final_url}\n"
            f"- **Status:** `{status_code}`\n"
            f"- **Content-Type:** `{content_type}`\n"
            f"- **Size:** {format_size_human(len(raw_bytes))} ({len(raw_bytes)} bytes)\n"
            f"- **Attachment ID:** `{att_id}`\n"
            f"- **Saved File:** `{att_filename}`\n"
        )
        if truncated:
            body_markdown += f"\n*(File capped at {format_size_human(rp.MAX_ATTACHMENT_BYTES)})*\n"

    elif mode_normalized == "html":
        # Raw HTML source mode
        charset = "utf-8"
        if "charset=" in content_type_raw.lower():
            cs_match = re.search(r"charset=([a-zA-Z0-9_\-]+)", content_type_raw, re.IGNORECASE)
            if cs_match:
                charset = cs_match.group(1).strip()
        try:
            html_text = raw_bytes.decode(charset, errors="replace")
        except Exception:
            html_text = raw_bytes.decode("utf-8", errors="replace")

        title = urllib.parse.urlsplit(final_url).netloc
        body_markdown = (
            f"### 📄 Web Fetch (HTML Source): {title}\n"
            f"- **URL:** {final_url}\n"
            f"- **Status:** `{status_code}`\n"
            f"- **Content-Type:** `{content_type}`\n"
            f"- **Size:** {format_size_human(len(raw_bytes))} ({len(raw_bytes)} bytes)\n\n"
            f"```html\n{html_text}\n```\n"
        )
        if truncated:
            body_markdown += f"\n*(HTML source truncated at {format_size_human(max_bytes)})*\n"

    else:
        # Markdown mode
        charset = "utf-8"
        if "charset=" in content_type_raw.lower():
            cs_match = re.search(r"charset=([a-zA-Z0-9_\-]+)", content_type_raw, re.IGNORECASE)
            if cs_match:
                charset = cs_match.group(1).strip()

        try:
            text_decoded = raw_bytes.decode(charset, errors="replace")
        except Exception:
            text_decoded = raw_bytes.decode("utf-8", errors="replace")

        if content_type in HTML_MIME_TYPES:
            parser = HTMLToMarkdownParser(base_url=final_url)
            parser.feed(text_decoded)
            title = parser.title.strip() or urllib.parse.urlsplit(final_url).netloc
            md_content = parser.get_markdown()
        elif content_type == "application/json":
            title = urllib.parse.urlsplit(final_url).netloc
            try:
                pretty_json = json.dumps(json.loads(text_decoded), indent=2)
                md_content = f"```json\n{pretty_json}\n```"
            except Exception:
                md_content = f"```json\n{text_decoded}\n```"
        else:
            title = urllib.parse.urlsplit(final_url).netloc
            md_content = text_decoded

        body_markdown = (
            f"### 🌐 Web Fetch: {title}\n"
            f"- **URL:** {final_url}\n"
            f"- **Status:** `{status_code}`\n"
            f"- **Content-Type:** `{content_type}`\n"
            f"- **Size:** {format_size_human(len(raw_bytes))} ({len(raw_bytes)} bytes)\n\n"
            f"---\n\n"
            f"{md_content}\n"
        )
        if truncated:
            body_markdown += f"\n*(Content truncated at {format_size_human(max_bytes)})*\n"

    return {
        "url": url,
        "final_url": final_url,
        "status_code": status_code,
        "content_type": content_type,
        "byte_size": len(raw_bytes),
        "mode": mode_normalized,
        "title": title,
        "body_markdown": body_markdown,
        "attachments": attachments,
        "truncated": truncated,
    }


# ==============================================================================
# Snapshot & Outbox Handling
# ==============================================================================

def update_room_snapshot(room: str, envelope: dict[str, Any], outbox_dir: Path) -> Path:
    """Update or create snapshot_<room>.json with rolling 100 messages.

    Defensively preserves either structured object schema or list schema.
    """
    outbox_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = outbox_dir / f"snapshot_{room}.json"
    existing_messages: list[dict[str, Any]] = []
    snapshot_obj: dict[str, Any] | None = None

    if snapshot_path.exists():
        try:
            with open(snapshot_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, list):
                existing_messages = loaded
            elif isinstance(loaded, dict):
                snapshot_obj = loaded
                existing_messages = loaded.get("messages", [])
        except Exception as err:
            logger.warning(f"Failed to read existing snapshot {snapshot_path}: {err}")
            existing_messages = []

    # Deduplicate by msg_id
    msg_id = envelope.get("msg_id")
    filtered = [m for m in existing_messages if isinstance(m, dict) and m.get("msg_id") != msg_id]
    filtered.append(envelope)
    rolling = filtered[-100:]

    if snapshot_obj is not None:
        snapshot_obj["messages"] = rolling
        snapshot_obj["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        content = json.dumps(snapshot_obj, indent=2, ensure_ascii=False)
    else:
        snapshot_dict = {
            "ok": True,
            "room": room,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "messages": rolling,
        }
        content = json.dumps(snapshot_dict, indent=2, ensure_ascii=False)

    with open(snapshot_path, "w", encoding="utf-8") as f:
        f.write(content + "\n")

    return snapshot_path


def write_outbox_envelope(
    envelope: dict[str, Any],
    room: str,
    request_id: str,
    queue_root: Path | str = "relay_queue",
    passphrase: str | None = None,
) -> tuple[Path, Path]:
    """Serialize envelope, apply optional AES-256-GCM encryption, and write to outbox & snapshot.

    Returns (outbox_file_path, snapshot_path).
    """
    queue_dir = Path(queue_root)
    outbox_dir = queue_dir / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)

    # Optional encryption via RELAY_SHARED_KEY
    if passphrase:
        serialized_json = rp.serialize_envelope(envelope, passphrase=passphrase)
        final_envelope = json.loads(serialized_json)
    else:
        serialized_json = rp.serialize_envelope(envelope)
        final_envelope = envelope

    timestamp_ms = int(time.time() * 1000)
    safe_req_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(request_id or "manual"))
    outbox_file = outbox_dir / f"web_{timestamp_ms}_{safe_req_id}.json"

    with open(outbox_file, "w", encoding="utf-8") as f:
        f.write(serialized_json + "\n")

    snapshot_file = update_room_snapshot(room=room, envelope=final_envelope, outbox_dir=outbox_dir)
    return outbox_file, snapshot_file


# ==============================================================================
# Web Fetch Processor
# ==============================================================================

def process_web_fetch(
    url: str,
    room: str = "main",
    mode: str = "markdown",
    request_id: str = "manual",
    max_bytes: int = rp.DEFAULT_WEB_MAX_BYTES_TEXT,
    queue_root: Path | str = "relay_queue",
    passphrase: str | None = None,
) -> dict[str, Any]:
    """Execute a single web fetch, build a MessageEnvelope, and publish to outbox.

    If the fetch encounters SSRF or network errors, constructs a clear error
    envelope so Mohammad receives immediate feedback in the chat room.
    """
    if room == "workers-room":
        room = "workers"
    if room not in rp.VALID_ROOMS:
        room = "main"

    error_msg: str | None = None
    fetch_result: dict[str, Any] | None = None

    try:
        fetch_result = fetch_external_resource(
            url=url,
            mode=mode,
            max_bytes=max_bytes,
            queue_root=queue_root,
        )
        report_body = fetch_result["body_markdown"]
        attachments = fetch_result.get("attachments", [])
        status_code = fetch_result["status_code"]
    except SSRFError as s_err:
        logger.error(f"SSRF violation for '{url}': {s_err}")
        error_msg = f"SSRF Security Violation: {s_err}"
        status_code = 403
    except urllib.error.HTTPError as h_err:
        logger.error(f"HTTP error fetching '{url}': {h_err}")
        error_msg = f"HTTP Error {h_err.code}: {h_err.reason}"
        status_code = h_err.code
    except urllib.error.URLError as u_err:
        logger.error(f"Network error fetching '{url}': {u_err}")
        error_msg = f"Network Connection Error: {u_err.reason}"
        status_code = 502
    except socket.timeout:
        logger.error(f"Connection timed out for '{url}'")
        error_msg = "Request timed out after 20 seconds."
        status_code = 504
    except Exception as exc:
        logger.error(f"Unexpected error fetching '{url}': {exc}", exc_info=True)
        error_msg = f"Fetch Failed: {exc}"
        status_code = 500

    if error_msg is not None:
        report_body = (
            f"### ⚠️ Web Fetch Failed: {url}\n"
            f"- **Target URL:** {url}\n"
            f"- **Status Code:** `{status_code}`\n"
            f"- **Error:** {error_msg}\n"
        )
        attachments = []

    # Build canonical message envelope
    meta = {
        "source": "gha-web-bridge",
        "request_id": request_id,
        "url": url,
        "mode": mode,
        "status_code": status_code,
    }

    envelope = rp.create_envelope(
        room=room,
        sender="web-bridge",
        body=report_body,
        attachments=attachments,
        meta=meta,
    )

    outbox_file, snapshot_file = write_outbox_envelope(
        envelope=envelope,
        room=room,
        request_id=request_id,
        queue_root=queue_root,
        passphrase=passphrase,
    )

    logger.info(
        f"Web fetch envelope published: msg_id={envelope['msg_id']} "
        f"-> outbox={outbox_file.name}, snapshot={snapshot_file.name}"
    )
    return envelope


# ==============================================================================
# Queue Directory Scan
# ==============================================================================

def scan_and_process_queue(
    queue_root: Path | str = "relay_queue",
    passphrase: str | None = None,
) -> list[dict[str, Any]]:
    """Scan relay_queue/web_requests/ for pending req_*.json files and process each."""
    queue_dir = Path(queue_root)
    requests_dir = queue_dir / "web_requests"
    if not requests_dir.exists():
        logger.info(f"Web requests directory does not exist: {requests_dir}")
        return []

    req_files = sorted(requests_dir.glob("req_*.json"))
    if not req_files:
        logger.info("No pending web request files found in queue.")
        return []

    logger.info(f"Found {len(req_files)} pending web request(s) to process.")
    results: list[dict[str, Any]] = []

    for req_file in req_files:
        logger.info(f"Processing request file: {req_file.name}")
        try:
            req_data = json.loads(req_file.read_text(encoding="utf-8"))
        except Exception as err:
            logger.error(f"Failed to parse JSON in {req_file}: {err}")
            req_file.unlink(missing_ok=True)
            continue

        url = req_data.get("url")
        if not url:
            logger.warning(f"Skipping {req_file.name}: missing 'url' field")
            req_file.unlink(missing_ok=True)
            continue

        room = req_data.get("room", "main")
        mode = req_data.get("mode", "markdown")
        max_bytes = (
            req_data.get("max_bytes")
            or req_data.get("max_size")
            or (rp.DEFAULT_WEB_MAX_BYTES_FILE if mode == "file" else rp.DEFAULT_WEB_MAX_BYTES_TEXT)
        )
        request_id = req_data.get("request_id") or req_file.stem.replace("req_", "")

        try:
            env = process_web_fetch(
                url=url,
                room=room,
                mode=mode,
                request_id=request_id,
                max_bytes=max_bytes,
                queue_root=queue_root,
                passphrase=passphrase,
            )
            results.append(env)
        finally:
            # Always remove the processed request file
            req_file.unlink(missing_ok=True)

    return results


# ==============================================================================
# CLI Entry Point
# ==============================================================================

def build_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser for web bridge runner."""
    parser = argparse.ArgumentParser(
        description="Cluster-Chat Web Bridge Runner and Safe URL Fetcher",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--url",
        type=str,
        default="",
        help="Target HTTP/HTTPS URL to fetch",
    )
    parser.add_argument(
        "--room",
        type=str,
        default="main",
        choices=["main", "dm-argon", "workers", "workers-room"],
        help="Destination cluster-chat room",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="markdown",
        choices=["markdown", "html", "file"],
        help="Content extraction mode",
    )
    parser.add_argument(
        "--request-id",
        type=str,
        default="manual",
        help="Unique identifier for this fetch request",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=None,
        help="Maximum bytes to download before truncating/capping",
    )
    parser.add_argument(
        "--queue-dir",
        type=str,
        default="relay_queue",
        help="Path to repository relay_queue root directory",
    )
    parser.add_argument(
        "--process-queue",
        action="store_true",
        help="Scan and process all pending requests in relay_queue/web_requests/",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    passphrase = os.environ.get("RELAY_SHARED_KEY") or None
    queue_root = Path(args.queue_dir)

    actions_taken = 0

    # 1. Direct URL fetch if specified
    if args.url:
        max_b = args.max_bytes
        if max_b is None:
            max_b = rp.DEFAULT_WEB_MAX_BYTES_FILE if args.mode == "file" else rp.DEFAULT_WEB_MAX_BYTES_TEXT

        logger.info(f"Executing CLI fetch for URL: {args.url}")
        process_web_fetch(
            url=args.url,
            room=args.room,
            mode=args.mode,
            request_id=args.request_id,
            max_bytes=max_b,
            queue_root=queue_root,
            passphrase=passphrase,
        )
        actions_taken += 1

    # 2. Queue directory scan if requested
    if args.process_queue:
        logger.info("Scanning queue directory for pending web requests...")
        queue_results = scan_and_process_queue(
            queue_root=queue_root,
            passphrase=passphrase,
        )
        actions_taken += len(queue_results)

    if actions_taken == 0 and not args.process_queue and not args.url:
        parser.print_help()
        return 1

    logger.info(f"Web bridge runner completed successfully. Processed {actions_taken} item(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
