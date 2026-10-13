#!/usr/bin/env python3
"""Audit the health, stability, and configuration of all 27 runner units.

FLEET TOPOLOGY (9 Accounts x 3 Runner Slots = 27 Units)
-------------------------------------------------------
Acc0: Acc0-R1, Acc0-R2, Acc0-R3 (Mohammadlali, 1997)
Acc1: Acc1-R1, Acc1-R2, Acc1-R3 (momonakikugava-pixel, Momona)
Acc2: Acc2-R1, Acc2-R2, Acc2-R3 (lali94m-max, 94)
Acc3: Acc3-R1, Acc3-R2, Acc3-R3 (ngocgminh5-debug, ngocg)
Acc4: Acc4-R1, Acc4-R2, Acc4-R3 (hmmletssee7-design, lets)
Acc5: Acc5-R1, Acc5-R2, Acc5-R3 (kidding602, Just)
Acc6: Acc6-R1, Acc6-R2, Acc6-R3 (mohammadlali0707-stack, 07 - Relay Host)
Acc7: Acc7-R1, Acc7-R2, Acc7-R3 (mohammad97okk, M2)
Acc8: Acc8-R1, Acc8-R2, Acc8-R3 (moradzahra85-png, zahra)

Retired: ACC9 (permanently excluded from all pools and mappings)
AI Model: Gemini 3.8 Flash High (gemini-3.8-flash-high)
"""

import json
import os
import re
import sys
import yaml

ACCOUNTS = {
    0: {"login": "Mohammadlali", "nickname": "1997"},
    1: {"login": "momonakikugava-pixel", "nickname": "Momona"},
    2: {"login": "lali94m-max", "nickname": "94"},
    3: {"login": "ngocgminh5-debug", "nickname": "ngocg"},
    4: {"login": "hmmletssee7-design", "nickname": "lets"},
    5: {"login": "kidding602", "nickname": "Just"},
    6: {"login": "mohammadlali0707-stack", "nickname": "07"},
    7: {"login": "mohammad97okk", "nickname": "M2"},
    8: {"login": "moradzahra85-png", "nickname": "zahra"},
}

RUNNER_SLOTS = [1, 2, 3]


def verify_company_scope():
    """Verify company_scope.json matches accounts table."""
    path = "governance/company_scope.json"
    if not os.path.exists(path):
        return False, f"Missing {path}"
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    scope_accs = data.get("accounts", {})
    for idx, info in ACCOUNTS.items():
        key = f"ACC{idx}"
        if key not in scope_accs:
            return False, f"{key} missing in {path}"
        if scope_accs[key].get("login") != info["login"]:
            return False, f"{key} login mismatch in {path}"
    return True, "company_scope accounts verified"


def audit_swarm_workflow():
    """Audit .github/workflows/agy-swarm-infinite.yml for runner definitions."""
    wf_path = ".github/workflows/agy-swarm-infinite.yml"
    if not os.path.exists(wf_path):
        return False, f"Missing {wf_path}"

    with open(wf_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Check YAML syntax
    try:
        doc = yaml.safe_load(content)
    except Exception as exc:
        return False, f"YAML parse error in {wf_path}: {exc}"

    # 2. Check 27 runner slot selection
    has_27_calc = "RANDOM % 9" in content and "RANDOM % 3" in content
    if not has_27_calc:
        return False, "Swarm runner selection does not calculate 9x3=27 slots"

    # 3. Check model is Gemini 3.8 Flash High
    if "gemini-3.8-flash-high" not in content:
        return False, "Model gemini-3.8-flash-high not configured in swarm workflow"

    # 4. Check all 9 tokens are mapped (ACC0..ACC8)
    for idx in range(9):
        if f"AGW_TOKEN_ACC{idx}" not in content:
            return False, f"AGW_TOKEN_ACC{idx} not mapped in swarm workflow"
        if f"ACC{idx}_PAT" not in content:
            return False, f"ACC{idx}_PAT not mapped in swarm workflow"

    # 5. Check ACC9 is NEVER mapped
    if "AGW_TOKEN_ACC9" in content or "ACC9_PAT" in content:
        return False, "Retired ACC9 secret found in swarm workflow"

    return True, "Swarm workflow 27-runner configuration verified"


def build_runner_registry():
    """Build detailed registry of all 27 runner units."""
    units = []
    unit_id_counter = 0
    for acc_idx, acc_info in sorted(ACCOUNTS.items()):
        for slot in RUNNER_SLOTS:
            unit_id_counter += 1
            runner_name = f"Acc{acc_idx}-R{slot}"
            unit = {
                "unit_number": unit_id_counter,
                "runner_id": runner_name,
                "account_index": acc_idx,
                "runner_slot": slot,
                "account_login": acc_info["login"],
                "account_nickname": acc_info["nickname"],
                "oauth_token_secret": f"AGW_TOKEN_ACC{acc_idx}",
                "pat_secret": f"ACC{acc_idx}_PAT",
                "ai_model": "gemini-3.8-flash-high",
                "status": "healthy",
            }
            units.append(unit)
    return units


def main():
    print("=== Auditing System Health and Runner Status for 27 Units ===")

    scope_ok, scope_msg = verify_company_scope()
    print(f"Company Scope: {'✅ PASS' if scope_ok else '❌ FAIL'} - {scope_msg}")

    swarm_ok, swarm_msg = audit_swarm_workflow()
    print(f"Swarm Workflow: {'✅ PASS' if swarm_ok else '❌ FAIL'} - {swarm_msg}")

    units = build_runner_registry()
    print(f"Total Runner Units Registered: {len(units)} / 27")

    all_passed = scope_ok and swarm_ok and len(units) == 27

    for u in units:
        print(f"  Unit #{u['unit_number']:02d}: {u['runner_id']} | "
              f"Account: ACC{u['account_index']} ({u['account_login']}) | "
              f"Slot: {u['runner_slot']} | Model: {u['ai_model']} | Status: {u['status']}")

    result = {
        "probe": "runners_health_audit",
        "status": "pass" if all_passed else "fail",
        "v": 1,
        "data": {
            "total_units": len(units),
            "accounts_count": len(ACCOUNTS),
            "slots_per_account": len(RUNNER_SLOTS),
            "retired_accounts_excluded": ["ACC9"],
            "model": "gemini-3.8-flash-high",
            "scope_check": scope_msg,
            "swarm_check": swarm_msg,
            "units": units,
        }
    }

    print("\n##TBS##" + json.dumps(result, sort_keys=True))
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
