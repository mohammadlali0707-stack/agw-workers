"""cluster_chat/cli.py

Command-Line Interface for Cluster Chat bridge.
Enables Mohammad and AI agents (Argon, Cookie, Coca, AGY workers) to upload,
send, and inspect photos and documents across cluster rooms (main, dm-argon, workers-room)
via HTTP API or direct local storage and room persistence.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from cluster_chat.client import (
    format_file_size,
    list_room_messages,
    send_room_file,
)

__all__ = [
    "main",
    "build_parser",
    "send_room_file",
    "list_room_messages",
    "format_file_size",
]


def _cli_send(args: argparse.Namespace) -> int:
    """Handle 'send' subcommand."""
    files = args.file or []
    text = (args.text or "").strip()

    if not text and not files:
        err_msg = "Must provide non-empty text (--text) or at least one file (--file)"
        if args.json:
            print(json.dumps({"ok": False, "error": err_msg}, indent=2))
        else:
            print(f"Error: {err_msg}", file=sys.stderr)
        return 1

    try:
        result = send_room_file(
            room=args.room,
            sender=args.sender,
            files=files,
            text=args.text or "",
            target_agent=args.target_agent,
            api_url=args.api_url,
            forward_telegram=not args.no_telegram,
            upload_dir=args.upload_dir,
            data_dir=args.data_dir,
        )
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    msg = result.get("message", {})
    attachments = result.get("attachments", msg.get("attachments", []))
    room_display = msg.get("room") or getattr(args, "room", "unknown")

    print(f"✓ Message sent successfully to room '{room_display}'")
    print(f"  Message ID:   {msg.get('message_id')}")
    print(f"  Sender:       {msg.get('sender')}")
    if msg.get("target_agent"):
        print(f"  Target Agent: {msg.get('target_agent')}")
    if msg.get("text"):
        print(f"  Text:         {msg.get('text')}")

    if attachments:
        print(f"  Attachments ({len(attachments)}):")
        for att in attachments:
            kind = att.get("kind", "file")
            fn = att.get("filename", "")
            sz = format_file_size(att.get("size_bytes"))
            print(f"    - [{kind}] {fn} ({sz})")
            print(f"      Local: {att.get('local_path')}")
            print(f"      URL:   {att.get('public_url')}")

    return 0


def _cli_list(args: argparse.Namespace) -> int:
    """Handle 'list' subcommand."""
    try:
        messages = list_room_messages(
            room=args.room,
            limit=args.limit,
            target_agent=args.target_agent,
            since=getattr(args, "since", None),
            after=getattr(args, "after", None),
            api_url=args.api_url,
            data_dir=args.data_dir,
        )
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"ok": True, "room": args.room, "count": len(messages), "messages": messages}, indent=2))
        return 0

    print(f"Messages in room '{args.room}' ({len(messages)} recent):")
    print("-" * 72)
    if not messages:
        print("  (no messages found)")
        print("-" * 72)
        return 0

    for m in messages:
        ts = m.get("created_at", "")
        sender = m.get("sender", "unknown")
        target = f" -> {m.get('target_agent')}" if m.get("target_agent") else ""
        mid = m.get("message_id", "")
        text = m.get("text", "")
        atts = m.get("attachments", [])

        print(f"[{ts}] {sender}{target} ({mid}):")
        if text:
            print(f"  {text}")
        if atts:
            print(f"  Attachments ({len(atts)}):")
            for att in atts:
                kind = att.get("kind", "file")
                fn = att.get("filename", "")
                sz = format_file_size(att.get("size_bytes"))
                print(f"    - [{kind}] {fn} ({sz})")
                if att.get("local_path"):
                    print(f"      Local: {att.get('local_path')}")
                if att.get("public_url"):
                    print(f"      URL:   {att.get('public_url')}")
        print()

    print("-" * 72)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="cluster-chat",
        description="Cluster Chat Bridge Agent CLI & SDK tool for file/photo uploads and messages.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 1.0.0",
        help="Show program's version number and exit",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available commands")

    # send command
    send_p = subparsers.add_parser("send", help="Send a message or upload files/photos to a room")
    send_p.add_argument(
        "--room", "-r",
        default="main",
        help="Room name (main, dm-argon, workers-room, or custom; default: main)",
    )
    send_p.add_argument(
        "--sender", "-s",
        default=os.environ.get("USER") or "agent",
        help="Sender identity (e.g. argon, mohammad, cookie, agy-1; default: $USER or agent)",
    )
    send_p.add_argument(
        "--target-agent", "--target", "-t",
        default=None,
        help="Target recipient agent (e.g. mohammad, argon)",
    )
    send_p.add_argument(
        "--text", "-m", "--caption", "-c",
        default="",
        help="Message text or photo/document caption",
    )
    send_p.add_argument(
        "--file", "-f",
        action="append",
        dest="file",
        default=[],
        help="Path to file or photo to attach (repeatable for multiple attachments)",
    )
    send_p.add_argument(
        "--api-url", "-u",
        default=os.environ.get("CLUSTER_CHAT_API_URL"),
        help="Cluster Chat HTTP server base URL (e.g. http://localhost:8080). If omitted, writes directly to local store.",
    )
    send_p.add_argument(
        "--no-telegram",
        action="store_true",
        help="Disable automatic Telegram forwarding for this message",
    )
    send_p.add_argument(
        "--upload-dir",
        default=None,
        help="Custom local upload directory (overrides CLUSTER_CHAT_UPLOAD_DIR)",
    )
    send_p.add_argument(
        "--data-dir",
        default=None,
        help="Custom local data directory (overrides CLUSTER_CHAT_DATA_DIR)",
    )
    send_p.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON response",
    )

    # list command
    list_p = subparsers.add_parser("list", help="List recent messages and attachments in a room")
    list_p.add_argument(
        "--room", "-r",
        default="main",
        help="Room name to query (default: main)",
    )
    list_p.add_argument(
        "--limit", "-n",
        type=int,
        default=10,
        help="Number of messages to retrieve (default: 10)",
    )
    list_p.add_argument(
        "--target-agent", "-t",
        default=None,
        help="Filter messages by target agent",
    )
    list_p.add_argument(
        "--since",
        default=None,
        help="Filter messages created after this timestamp or message ID",
    )
    list_p.add_argument(
        "--after",
        default=None,
        help="Filter messages created strictly after this message ID",
    )
    list_p.add_argument(
        "--api-url", "-u",
        default=os.environ.get("CLUSTER_CHAT_API_URL"),
        help="Cluster Chat HTTP server base URL. If omitted, queries local room store.",
    )
    list_p.add_argument(
        "--data-dir",
        default=None,
        help="Custom local data directory (overrides CLUSTER_CHAT_DATA_DIR)",
    )
    list_p.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON response",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    if argv is None:
        argv = sys.argv[1:]

    # Ergonomic shortcut: if invoked without a recognized subcommand, default to 'send'
    if argv and argv[0] not in ("send", "list", "-h", "--help", "--version"):
        argv = ["send"] + argv
    elif not argv:
        argv = ["--help"]

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "send":
        return _cli_send(args)
    elif args.subcommand == "list":
        return _cli_list(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
