#!/usr/bin/env python3
"""Run bilingual Notion synchronization across configured pages.

Usage:
    python3 -m tools.bilingual.run [--page <id>] [--dry-run | --no-dry-run] [--config <path>]

Exits with code 0 even if some pages have 'no_access'.
"""

import argparse
import json
import os
import sys
from typing import Dict, List, Optional

try:
    from tools.bilingual.notion_fa import sync_page
except (ImportError, ModuleNotFoundError):
    def sync_page(page_id: str, dry_run: bool = False) -> dict:
        """Fallback sync_page when tools/bilingual/notion_fa.py is not yet available."""
        return {"page": page_id, "status": "no_access", "blocks": 0}

KNOWN_PAGE_TITLES: Dict[str, str] = {
    "bd9e7f06f6424ef49af5976ced3b2c6f": "System Registry",
    "b512ec6b62184fe1b02d6a7795a11c13": "Onboarding runbook for Notion AI 2/3",
    "22814685831c4256b2af8da9dbc8e5bd": "SDD-006",
}


def normalize_id(page_id: str) -> str:
    """Normalize Notion page ID by removing dashes and whitespace."""
    return page_id.strip().replace("-", "").lower()


def load_pages_from_config(config_path: str) -> List[str]:
    """Load list of Notion page IDs from configuration file."""
    if not os.path.isfile(config_path):
        print(f"Warning: Configuration file not found at {config_path}", file=sys.stderr)
        return []
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        pages = data.get("pages", [])
        return [normalize_id(p) for p in pages if isinstance(p, str) and p.strip()]
    except Exception as exc:
        print(f"Error reading {config_path}: {exc}", file=sys.stderr)
        return []


def format_summary_table(results: List[dict], dry_run: bool) -> str:
    """Format synchronization results into a clean summary table."""
    lines = []
    header_banner = f"Bilingual Notion Sync Summary (dry_run={dry_run})"
    lines.append("=" * 88)
    lines.append(header_banner)
    lines.append("=" * 88)
    lines.append(f"{'Page ID':<34} | {'Status':<11} | {'Blocks':<6} | {'Title / Info'}")
    lines.append("-" * 34 + "-+-" + "-" * 11 + "-+-" + "-" * 6 + "-+-" + "-" * 30)

    counts = {"updated": 0, "unchanged": 0, "no_access": 0, "error": 0}

    for item in results:
        pid = item.get("page", "")
        status = item.get("status", "unknown")
        blocks = item.get("blocks", 0)
        title = item.get("title") or KNOWN_PAGE_TITLES.get(pid, item.get("error", "-"))

        counts[status] = counts.get(status, 0) + 1
        lines.append(f"{pid:<34} | {status:<11} | {str(blocks):<6} | {title}")

    lines.append("-" * 34 + "-+-" + "-" * 11 + "-+-" + "-" * 6 + "-+-" + "-" * 30)
    summary_line = (
        f"Total: {len(results)} | "
        f"Updated: {counts.get('updated', 0)} | "
        f"Unchanged: {counts.get('unchanged', 0)} | "
        f"No Access: {counts.get('no_access', 0)} | "
        f"Errors: {counts.get('error', 0)}"
    )
    lines.append(summary_line)
    lines.append("=" * 88)
    return "\n".join(lines)


def format_markdown_table(results: List[dict], dry_run: bool) -> str:
    """Format synchronization results as a GitHub Markdown table."""
    lines = [
        f"### 🌐 Bilingual Notion Sync Summary (dry_run: `{dry_run}`)",
        "",
        "| Page ID | Title / Info | Status | Blocks |",
        "|:---|:---|:---:|:---:|",
    ]
    for item in results:
        pid = item.get("page", "")
        status = item.get("status", "unknown")
        blocks = item.get("blocks", 0)
        title = item.get("title") or KNOWN_PAGE_TITLES.get(pid, item.get("error", "-"))
        status_badge = {
            "updated": "✅ updated",
            "unchanged": "⚪ unchanged",
            "no_access": "🔒 no_access",
            "error": "❌ error",
        }.get(status, status)
        lines.append(f"| `{pid}` | {title} | {status_badge} | {blocks} |")
    return "\n".join(lines)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync bilingual Notion pages.")
    parser.add_argument(
        "--page",
        dest="page",
        type=str,
        default=None,
        help="Optional single Notion page ID to sync",
    )
    parser.add_argument(
        "--config",
        dest="config",
        type=str,
        default=os.path.join("config", "bilingual_pages.json"),
        help="Path to bilingual pages configuration JSON",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=None,
        help="Run without modifying Notion (default: True)",
    )
    parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="Disable dry run mode (perform real modifications)",
    )
    return parser.parse_args(argv)


def resolve_dry_run(args_dry_run: Optional[bool]) -> bool:
    """Determine dry_run flag taking CLI args and environment variables into account."""
    if args_dry_run is not None:
        return args_dry_run

    env_val = os.environ.get("INPUT_DRY_RUN") or os.environ.get("DRY_RUN")
    if env_val is not None:
        return env_val.strip().lower() not in ("0", "false", "no", "off")

    return True


def run(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    dry_run = resolve_dry_run(args.dry_run)

    # Determine target page(s)
    single_page = args.page or os.environ.get("INPUT_PAGE") or os.environ.get("PAGE") or os.environ.get("BILINGUAL_PAGE")
    if single_page and single_page.strip():
        page_ids = [normalize_id(single_page)]
    else:
        page_ids = load_pages_from_config(args.config)

    if not page_ids:
        print("No pages configured or specified to sync.")

    results: List[dict] = []
    for pid in page_ids:
        try:
            res = sync_page(pid, dry_run=dry_run)
            if not isinstance(res, dict):
                res = {"page": pid, "status": "error", "blocks": 0, "error": f"Invalid return type: {type(res)}"}
        except Exception as exc:
            res = {"page": pid, "status": "error", "blocks": 0, "error": str(exc)}
        results.append(res)

    # Print summary table
    table_str = format_summary_table(results, dry_run=dry_run)
    print(table_str)

    # Write one-line summary for Agent Bus or step output
    counts = {"updated": 0, "unchanged": 0, "no_access": 0, "error": 0}
    for r in results:
        st = r.get("status", "unknown")
        counts[st] = counts.get(st, 0) + 1

    one_line_summary = (
        f"Bilingual sync (dry_run={dry_run}): {len(results)} pages processed "
        f"({counts.get('updated', 0)} updated, {counts.get('unchanged', 0)} unchanged, "
        f"{counts.get('no_access', 0)} no_access, {counts.get('error', 0)} errors)"
    )

    try:
        with open("summary.txt", "w", encoding="utf-8") as f:
            f.write(one_line_summary + "\n")
    except Exception as exc:
        print(f"Warning: Could not write summary.txt: {exc}", file=sys.stderr)

    # Write markdown summary to GITHUB_STEP_SUMMARY if available
    step_summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary_file:
        try:
            md_content = format_markdown_table(results, dry_run=dry_run)
            with open(step_summary_file, "a", encoding="utf-8") as f:
                f.write(md_content + "\n")
        except Exception as exc:
            print(f"Warning: Could not write GITHUB_STEP_SUMMARY: {exc}", file=sys.stderr)

    # Always exit 0 even if some or all pages are no_access
    return 0


if __name__ == "__main__":
    sys.exit(run())
