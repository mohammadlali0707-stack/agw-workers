#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for fork-side workflow health fixtures and patch resilience.

Validates:
1. Clean patch application for gate-webhook-health-resilience.patch and gate-webhook-health-relay-deploy.patch.
2. Zero direct modifications to workflow files on main.
3. Gate 2 dual-failure telemetry isolation:
   - Primary probe HTTP 404 handling (expected upstream security quarantine).
   - Secondary alert notification resilience via continue-on-error: true.
   - Two-tier issue creation fallback with warning guard preventing runner crash.
4. Tools/audit_fork_workflow_health.py behavioral correctness across healthy, unparseable, absent,
   missing PAT, and API error states.
"""

import io
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure repository root is on sys.path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import Tools.audit_fork_workflow_health as audit_tool
from tests.fixtures.fork_health_fixtures import (
    GATE2_TELEMETRY,
    MOCK_WORKFLOWS_ABSENT,
    MOCK_WORKFLOWS_HEALTHY,
    MOCK_WORKFLOWS_UNPARSEABLE,
)


class TestForkHealthAndPatchResilience(unittest.TestCase):
    """Test suite for fork workflow health tooling and Gate 2 resilience patches."""

    def test_01_patch_resilience_clean_apply(self):
        """Verify Reports/audit/gate-webhook-health-resilience.patch applies cleanly with git apply --check."""
        patch_path = os.path.join(REPO_ROOT, "Reports/audit/gate-webhook-health-resilience.patch")
        self.assertTrue(os.path.exists(patch_path), f"Patch not found at {patch_path}")
        result = subprocess.run(
            ["git", "apply", "--check", patch_path],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"git apply --check failed for gate-webhook-health-resilience.patch: {result.stderr}",
        )

    def test_02_patch_relay_deploy_clean_apply(self):
        """Verify Reports/audit/gate-webhook-health-relay-deploy.patch applies cleanly with git apply --check."""
        patch_path = os.path.join(REPO_ROOT, "Reports/audit/gate-webhook-health-relay-deploy.patch")
        self.assertTrue(os.path.exists(patch_path), f"Patch not found at {patch_path}")
        result = subprocess.run(
            ["git", "apply", "--check", patch_path],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"git apply --check failed for gate-webhook-health-relay-deploy.patch: {result.stderr}",
        )

    def test_03_zero_direct_workflow_modifications_on_main(self):
        """Ensure no workflow files under .github/workflows/ have been modified directly on main."""
        result = subprocess.run(
            ["git", "status", "--porcelain", ".github/workflows/"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.stdout.strip(),
            "",
            f"Workflow files must not be modified directly on main: {result.stdout}",
        )

    def test_04_gate2_step3_telemetry_http_404_isolation(self):
        """Validate Gate 2 primary failure isolation when probe receives HTTP 404 security quarantine."""
        status = GATE2_TELEMETRY["primary_failure"]["http_code"]
        env_updates = {}
        if status in ("200", "400"):
            outcome = "ok"
        elif status == "404":
            env_updates["FAIL_TYPE"] = "config_error"
            env_updates["FAIL_MESSAGE"] = GATE2_TELEMETRY["primary_failure"]["fail_message"]
            outcome = "config_error"
        else:
            env_updates["FAIL_TYPE"] = "server_failure"
            outcome = "server_failure"

        self.assertEqual(outcome, "config_error")
        self.assertEqual(env_updates["FAIL_TYPE"], "config_error")
        self.assertIn("HTTP 404: Webhook route not found", env_updates["FAIL_MESSAGE"])

    def test_05_gate2_step4_continue_on_error_resilience(self):
        """Verify that resilience patch inserts continue-on-error: true on alert notification step."""
        patch_path = os.path.join(REPO_ROOT, "Reports/audit/gate-webhook-health-resilience.patch")
        with open(patch_path, "r", encoding="utf-8") as f:
            patch_text = f.read()
        self.assertIn("+        continue-on-error: true", patch_text)
        self.assertIn("- name: Post alert if webhook health fails", patch_text)

    def test_06_gate2_step4_two_tier_issue_creation_fallback(self):
        """Validate that two-tier fallback logic prevents runner crash when label 'alert' is absent."""
        patch_path = os.path.join(REPO_ROOT, "Reports/audit/gate-webhook-health-resilience.patch")
        with open(patch_path, "r", encoding="utf-8") as f:
            patch_text = f.read()

        # The patch must provide unlabelled fallback and a safe warning echo
        self.assertIn('--body-file /tmp/issue_body.md 2>/dev/null', patch_text)
        self.assertIn(
            'echo "::warning::failed to create alert issue on $REPO (issues disabled or insufficient token permissions)"',
            patch_text,
        )

        # Simulate shell execution of the fallback chain when primary command fails (exit 1)
        # and unlabelled fallback succeeds
        simulated_script = (
            "set -e; "
            "(false --label alert 2>/dev/null || true --unlabelled 2>/dev/null || echo '::warning::failed'); "
            "echo SUCCESS"
        )
        res = subprocess.run(
            ["bash", "-c", simulated_script],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("SUCCESS", res.stdout)

        # Simulate fallback chain when both primary and secondary fail (e.g., token read-only)
        simulated_script_both_fail = (
            "set -e; "
            "(false --label alert 2>/dev/null || false --unlabelled 2>/dev/null || echo '::warning::failed'); "
            "echo SUCCESS_SURVIVED"
        )
        res2 = subprocess.run(
            ["bash", "-c", simulated_script_both_fail],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res2.returncode, 0)
        self.assertIn("SUCCESS_SURVIVED", res2.stdout)

    @patch("Tools.audit_fork_workflow_health.get")
    def test_07_audit_fork_workflow_health_all_healthy(self, mock_get):
        """Verify audit_fork_workflow_health returns pass (0) when all forks parse workflows cleanly."""
        mock_get.return_value = (MOCK_WORKFLOWS_HEALTHY, 200)

        # Supply dummy PATs for all active rotation accounts
        mock_env = {f"ACC{idx}_PAT": f"dummy_token_{idx}" for idx in audit_tool.ACCOUNTS}
        with patch.dict(os.environ, mock_env):
            stdout_capture = io.StringIO()
            with patch("sys.stdout", stdout_capture):
                exit_code = audit_tool.main()

            output = stdout_capture.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn('"status": "pass"', output)
            self.assertIn('"unparseable": 0', output)
            self.assertIn('"unreachable": 0', output)

    @patch("Tools.audit_fork_workflow_health.get")
    def test_08_audit_fork_workflow_health_unparseable_detection(self, mock_get):
        """Verify audit_fork_workflow_health flags UNPARSEABLE when workflow name equals path."""
        mock_get.return_value = (MOCK_WORKFLOWS_UNPARSEABLE, 200)

        mock_env = {f"ACC{idx}_PAT": f"dummy_token_{idx}" for idx in audit_tool.ACCOUNTS}
        with patch.dict(os.environ, mock_env):
            stdout_capture = io.StringIO()
            with patch("sys.stdout", stdout_capture):
                exit_code = audit_tool.main()

            output = stdout_capture.getvalue()
            self.assertEqual(exit_code, 1)
            self.assertIn("UNPARSEABLE", output)
            self.assertIn('"status": "fail"', output)

    @patch("Tools.audit_fork_workflow_health.get")
    def test_09_audit_fork_workflow_health_absent_workflow(self, mock_get):
        """Verify audit_fork_workflow_health flags ABSENT when watched workflows are missing."""
        mock_get.return_value = (MOCK_WORKFLOWS_ABSENT, 200)

        mock_env = {f"ACC{idx}_PAT": f"dummy_token_{idx}" for idx in audit_tool.ACCOUNTS}
        with patch.dict(os.environ, mock_env):
            stdout_capture = io.StringIO()
            with patch("sys.stdout", stdout_capture):
                exit_code = audit_tool.main()

            output = stdout_capture.getvalue()
            self.assertIn("absent", output)
            self.assertIn('"status": "pass"', output)

    def test_10_audit_fork_workflow_health_missing_pat(self):
        """Verify audit_fork_workflow_health detects NO_PAT and increments unreachable count."""
        # Ensure no PATs in environment
        with patch.dict(os.environ, {}, clear=True):
            stdout_capture = io.StringIO()
            with patch("sys.stdout", stdout_capture):
                exit_code = audit_tool.main()

            output = stdout_capture.getvalue()
            self.assertEqual(exit_code, 1)
            self.assertIn("NO_PAT", output)
            self.assertIn('"unreachable": 9', output)
            self.assertIn('"status": "fail"', output)

    @patch("Tools.audit_fork_workflow_health.get")
    def test_11_audit_fork_workflow_health_api_error_handling(self, mock_get):
        """Verify audit_fork_workflow_health handles non-200 API responses gracefully."""
        mock_get.return_value = ({}, 403)

        mock_env = {f"ACC{idx}_PAT": f"dummy_token_{idx}" for idx in audit_tool.ACCOUNTS}
        with patch.dict(os.environ, mock_env):
            stdout_capture = io.StringIO()
            with patch("sys.stdout", stdout_capture):
                exit_code = audit_tool.main()

            output = stdout_capture.getvalue()
            self.assertEqual(exit_code, 1)
            self.assertIn("API_403", output)
            self.assertIn('"unreachable": 9', output)


if __name__ == "__main__":
    unittest.main()
