#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Delayed Plan Toggle for Notion Tasks
-----------------------------------
Creates a Notion task page with 'Plan' initially set to False,
waits for a configurable delay (default: 2.0 seconds), and then
patches the 'Plan' checkbox property to True.

This two-phase commit triggers Notion database update automations
and webhooks that require a state transition (False -> True) rather
than initial page creation.
"""

import os
import sys
import time
import json
import argparse
import requests

NOTION_VERSION = "2022-06-28"

def create_delayed_plan_task(
    title: str,
    database_id: str,
    token: str = None,
    delay_seconds: float = 2.0,
    status: str = "Not started",
    extra_properties: dict = None
) -> dict:
    """Creates a task with Plan=False, sleeps delay_seconds, then patches Plan=True."""
    if not token:
        token = os.environ.get("NOTION_TOKEN") or os.environ.get("NOTION_API_TOKEN")
    if not token:
        raise ValueError("Missing Notion API Token. Provide via --token or NOTION_TOKEN env var.")

    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json"
    }

    # Phase 1: Create page with Plan: false
    initial_properties = {
        "Name": {
            "title": [{"text": {"content": title}}]
        },
        "Plan": {
            "checkbox": False
        }
    }
    if extra_properties:
        initial_properties.update(extra_properties)

    create_payload = {
        "parent": {"database_id": database_id},
        "properties": initial_properties
    }

    create_res = requests.post("https://api.notion.com/v1/pages", headers=headers, json=create_payload, timeout=15)
    if create_res.status_code not in (200, 201):
        raise RuntimeError(f"Failed to create Notion page: {create_res.status_code} - {create_res.text}")

    page_data = create_res.json()
    page_id = page_data["id"]
    page_url = page_data.get("url", f"https://notion.so/{page_id.replace('-', '')}")

    print(f"[*] Phase 1 complete: Page '{title}' created with Plan=False (ID: {page_id})")

    # Phase 2: Delay
    print(f"[*] Waiting {delay_seconds:.1f}s delay before toggling Plan property...")
    time.sleep(delay_seconds)

    # Phase 3: Update page with Plan: true
    patch_payload = {
        "properties": {
            "Plan": {
                "checkbox": True
            }
        }
    }

    patch_res = requests.patch(f"https://api.notion.com/v1/pages/{page_id}", headers=headers, json=patch_payload, timeout=15)
    if patch_res.status_code not in (200, 201):
        raise RuntimeError(f"Failed to patch Plan property: {patch_res.status_code} - {patch_res.text}")

    updated_page = patch_res.json()
    print(f"[+] Phase 2 complete: Page '{title}' updated with Plan=True!")

    return {
        "id": page_id,
        "url": page_url,
        "title": title,
        "plan_state": updated_page.get("properties", {}).get("Plan", {}).get("checkbox", True),
        "delay_applied_seconds": delay_seconds
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Delayed Plan Toggle for Notion Tasks")
    parser.add_argument("--title", required=True, help="Title of the Notion task")
    parser.add_argument("--database-id", default="3dcbefc7-ddfb-80e1-8e26-ced6033af37f", help="Target Notion Database ID")
    parser.add_argument("--token", default=None, help="Notion Integration Token (defaults to NOTION_TOKEN env var)")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay in seconds before toggling Plan=True")
    args = parser.parse_args()

    result = create_delayed_plan_task(
        title=args.title,
        database_id=args.database_id,
        token=args.token,
        delay_seconds=args.delay
    )
    print(json.dumps(result, indent=2))
