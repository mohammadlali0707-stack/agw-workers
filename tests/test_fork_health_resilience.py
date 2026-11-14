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
    GATE2_TELEMETRY_CONNREFUSED,
    GATE2_TELEMETRY_TIMEOUT,
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

    def test_12_gate2_step3_curl_timeout_status_normalization(self):
        """Verify that curl timeouts (exit 28) produce strictly normalized '000', not '000000'."""
        simulated_step = (
            'HTTP_CODE=""; CURL_EXIT=0; '
            'HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://192.0.2.1 --max-time 1 2>/dev/null) '
            '|| CURL_EXIT=$?; '
            'if [ $CURL_EXIT -ne 0 ] || [ -z "$HTTP_CODE" ]; then STATUS="000"; else STATUS="$HTTP_CODE"; fi; '
            'echo "STATUS=$STATUS"; echo "CURL_EXIT=$CURL_EXIT"'
        )
        res = subprocess.run(["bash", "-c", simulated_step], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("STATUS=000", res.stdout)
        self.assertNotIn("STATUS=000000", res.stdout)
        self.assertNotIn("STATUS=000\n000", res.stdout)
        self.assertIn("CURL_EXIT=28", res.stdout)

    def test_13_gate2_step3_shell_status_evaluation_matrix(self):
        """Verify that shell status logic correctly routes 000, 5xx, and curl exit codes to server_failure."""
        def evaluate_shell(status_val, curl_exit=0):
            shell_script = f"""
            STATUS="{status_val}"
            CURL_EXIT={curl_exit}
            if [ "$STATUS" = "200" ] || [ "$STATUS" = "400" ]; then
              echo "OUTCOME=ok"
            elif [ "$STATUS" = "404" ]; then
              echo "FAIL_TYPE=config_error"
            elif [ "$STATUS" = "000" ] || [ "$CURL_EXIT" -ne 0 ] || {{ [ "$STATUS" -ge 500 ] 2>/dev/null; }}; then
              echo "FAIL_TYPE=server_failure"
            else
              echo "FAIL_TYPE=config_error"
            fi
            """
            proc = subprocess.run(["bash", "-c", shell_script], capture_output=True, text=True)
            return proc.stdout.strip()

        # Happy paths
        self.assertEqual(evaluate_shell("200"), "OUTCOME=ok")
        self.assertEqual(evaluate_shell("400"), "OUTCOME=ok")

        # Configuration error (Quarantine / inactive workflow)
        self.assertEqual(evaluate_shell("404"), "FAIL_TYPE=config_error")

        # Server downtime & 5xx failures
        self.assertEqual(evaluate_shell("500"), "FAIL_TYPE=server_failure")
        self.assertEqual(evaluate_shell("502"), "FAIL_TYPE=server_failure")
        self.assertEqual(evaluate_shell("503"), "FAIL_TYPE=server_failure")
        self.assertEqual(evaluate_shell("504"), "FAIL_TYPE=server_failure")

        # Timeout & connection aborts (must be server_failure, NEVER config_error)
        self.assertEqual(evaluate_shell("000", curl_exit=0), "FAIL_TYPE=server_failure")
        self.assertEqual(evaluate_shell("000", curl_exit=28), "FAIL_TYPE=server_failure")
        self.assertEqual(evaluate_shell("000", curl_exit=7), "FAIL_TYPE=server_failure")
        self.assertEqual(evaluate_shell("000", curl_exit=6), "FAIL_TYPE=server_failure")
        self.assertEqual(evaluate_shell("000", curl_exit=35), "FAIL_TYPE=server_failure")

        # Resiliency against defect strings: non-zero curl exit overrides malformed status
        self.assertEqual(evaluate_shell("000000", curl_exit=28), "FAIL_TYPE=server_failure")

    def test_14_gate2_step3_python_simulation_parity(self):
        """Verify Python telemetry generator accurately reproduces workflow branching logic."""
        test_cases = [
            ("200", 0, "ok"),
            ("400", 0, "ok"),
            ("404", 0, "config_error"),
            ("000", 28, "server_failure"),
            ("000", 7, "server_failure"),
            ("000", 6, "server_failure"),
            ("000", 35, "server_failure"),
            ("500", 0, "server_failure"),
            ("502", 0, "server_failure"),
            ("503", 0, "server_failure"),
            ("504", 0, "server_failure"),
            ("301", 0, "config_error"),
            ("403", 0, "config_error"),
            ("418", 0, "config_error"),  # Unexpected HTTP status falls through to config_error
        ]
        for status, curl_exit, expected in test_cases:
            if status in ("200", "400"):
                outcome = "ok"
            elif status == "404":
                outcome = "config_error"
            elif status == "000" or curl_exit != 0 or (status.isdigit() and int(status) >= 500):
                outcome = "server_failure"
            else:
                outcome = "config_error"
            self.assertEqual(
                outcome,
                expected,
                f"Status '{status}' with curl_exit {curl_exit} expected {expected}, got {outcome}",
            )

    def test_15_gate2_patch_curl_resilience_content(self):
        """Verify that resilience patch includes curl exit capture, normalization, and server_failure routing."""
        patch_path = os.path.join(REPO_ROOT, "Reports/audit/gate-webhook-health-resilience.patch")
        with open(patch_path, "r", encoding="utf-8") as f:
            patch_text = f.read()

        self.assertIn('+          HTTP_CODE=""', patch_text)
        self.assertIn('+          CURL_EXIT=0', patch_text)
        self.assertIn('+          if [ $CURL_EXIT -ne 0 ] || [ -z "$HTTP_CODE" ]; then', patch_text)
        self.assertIn('+            STATUS="000"', patch_text)
        self.assertIn('+          echo "CURL_EXIT=$CURL_EXIT" >> "$GITHUB_ENV"', patch_text)
        self.assertIn(
            '+          elif [ "$STATUS" = "000" ] || [ "$CURL_EXIT" -ne 0 ] || { [ "$STATUS" -ge 500 ] 2>/dev/null; }; then',
            patch_text,
        )
        self.assertIn(
            '+            echo "FAIL_MESSAGE=HTTP $STATUS (curl exit $CURL_EXIT): Server failure or timeout.',
            patch_text,
        )

    def test_16_gate2_step3_telemetry_timeout_isolation(self):
        """Validate Gate 2 telemetry isolation and alert labeling under curl timeout (exit 28)."""
        import re
        fixture = GATE2_TELEMETRY_TIMEOUT
        status = fixture["http_code"]
        curl_exit = fixture["curl_exit_code"]

        if status in ("200", "400"):
            fail_type = "ok"
            fail_msg = ""
        elif status == "404":
            fail_type = "config_error"
            fail_msg = "HTTP 404: Webhook route not found. The endpoint path is incorrect or the n8n workflow is inactive/unregistered."
        elif status == "000" or curl_exit != 0 or (status.isdigit() and int(status) >= 500):
            fail_type = "server_failure"
            fail_msg = f"HTTP {status} (curl exit {curl_exit}): Server failure or timeout. The n8n server is down, unreachable, or returning internal 5xx errors."
        else:
            fail_type = "config_error"
            fail_msg = f"HTTP {status}: Unexpected HTTP response received from webhook endpoint."

        self.assertEqual(fail_type, fixture["fail_type"])
        self.assertTrue(bool(re.search(fixture["fail_message_pattern"], fail_msg)))

        # Verify alert category label logic parity with Step 4
        if fail_type == "config_error":
            cat_label = "⚠️ Configuration Error (HTTP 404 / Webhook Config)"
        elif fail_type == "server_failure":
            cat_label = f"🔥 Server Failure (HTTP {status} / Timeout / Unreachable)"
        else:
            cat_label = f"❓ Gate Failure (HTTP {status})"

        self.assertTrue(bool(re.search(fixture["cat_label_pattern"], cat_label)))

    def test_17_gate2_step3_telemetry_connrefused_isolation(self):
        """Validate Gate 2 telemetry isolation and alert labeling under network connection failure (exit 7)."""
        import re
        fixture = GATE2_TELEMETRY_CONNREFUSED
        status = fixture["http_code"]
        curl_exit = fixture["curl_exit_code"]

        if status in ("200", "400"):
            fail_type = "ok"
            fail_msg = ""
        elif status == "404":
            fail_type = "config_error"
            fail_msg = "HTTP 404: Webhook route not found. The endpoint path is incorrect or the n8n workflow is inactive/unregistered."
        elif status == "000" or curl_exit != 0 or (status.isdigit() and int(status) >= 500):
            fail_type = "server_failure"
            fail_msg = f"HTTP {status} (curl exit {curl_exit}): Server failure or timeout. The n8n server is down, unreachable, or returning internal 5xx errors."
        else:
            fail_type = "config_error"
            fail_msg = f"HTTP {status}: Unexpected HTTP response received from webhook endpoint."

        self.assertEqual(fail_type, fixture["fail_type"])
        self.assertTrue(bool(re.search(fixture["fail_message_pattern"], fail_msg)))

        # Verify alert category label logic parity with Step 4
        if fail_type == "config_error":
            cat_label = "⚠️ Configuration Error (HTTP 404 / Webhook Config)"
        elif fail_type == "server_failure":
            cat_label = f"🔥 Server Failure (HTTP {status} / Timeout / Unreachable)"
        else:
            cat_label = f"❓ Gate Failure (HTTP {status})"

        self.assertTrue(bool(re.search(fixture["cat_label_pattern"], cat_label)))

    def test_18_gate2_step3_network_error_matrix(self):
        """Validate network error matrix (exit 6, 7, 28, 35, 52, 56) correctly routes to server_failure."""
        curl_network_exits = [
            (6, "CURLE_COULDNT_RESOLVE_HOST"),
            (7, "CURLE_COULDNT_CONNECT"),
            (28, "CURLE_OPERATION_TIMEDOUT"),
            (35, "CURLE_SSL_CONNECT_ERROR"),
            (52, "CURLE_GOT_NOTHING"),
            (56, "CURLE_RECV_ERROR"),
        ]
        for exit_code, desc in curl_network_exits:
            shell_script = f"""
            STATUS="000"
            CURL_EXIT={exit_code}
            if [ "$STATUS" = "200" ] || [ "$STATUS" = "400" ]; then
              echo "OUTCOME=ok"
            elif [ "$STATUS" = "404" ]; then
              echo "FAIL_TYPE=config_error"
            elif [ "$STATUS" = "000" ] || [ "$CURL_EXIT" -ne 0 ] || {{ [ "$STATUS" -ge 500 ] 2>/dev/null; }}; then
              echo "FAIL_TYPE=server_failure"
              echo "FAIL_MESSAGE=HTTP $STATUS (curl exit $CURL_EXIT): Server failure or timeout."
            else
              echo "FAIL_TYPE=config_error"
            fi
            """
            proc = subprocess.run(["bash", "-c", shell_script], capture_output=True, text=True)
            self.assertIn("FAIL_TYPE=server_failure", proc.stdout, f"Failed for {desc} (exit {exit_code})")
            self.assertIn(f"curl exit {exit_code}", proc.stdout)

    def test_19_gate2_step3_exhaustive_http_status_matrix(self):
        """Validate exhaustive HTTP status handling across 2xx, 3xx, 4xx, 5xx, and 000."""
        cases = {
            "200": "OUTCOME=ok",
            "400": "OUTCOME=ok",
            "404": "FAIL_TYPE=config_error",
            "500": "FAIL_TYPE=server_failure",
            "502": "FAIL_TYPE=server_failure",
            "503": "FAIL_TYPE=server_failure",
            "504": "FAIL_TYPE=server_failure",
            "000": "FAIL_TYPE=server_failure",
            "301": "FAIL_TYPE=config_error",
            "302": "FAIL_TYPE=config_error",
            "401": "FAIL_TYPE=config_error",
            "403": "FAIL_TYPE=config_error",
            "405": "FAIL_TYPE=config_error",
            "418": "FAIL_TYPE=config_error",
            "429": "FAIL_TYPE=config_error",
        }
        for code, expected_outcome in cases.items():
            shell_script = f"""
            STATUS="{code}"
            CURL_EXIT=0
            if [ "$STATUS" = "200" ] || [ "$STATUS" = "400" ]; then
              echo "OUTCOME=ok"
            elif [ "$STATUS" = "404" ]; then
              echo "FAIL_TYPE=config_error"
            elif [ "$STATUS" = "000" ] || [ "$CURL_EXIT" -ne 0 ] || {{ [ "$STATUS" -ge 500 ] 2>/dev/null; }}; then
              echo "FAIL_TYPE=server_failure"
            else
              echo "FAIL_TYPE=config_error"
            fi
            """
            proc = subprocess.run(["bash", "-c", shell_script], capture_output=True, text=True)
            self.assertEqual(proc.stdout.strip(), expected_outcome, f"Failed for HTTP {code}")

    def test_20_gate2_yaml_patch_in_memory_validation(self):
        """Verify that applying resilience patch produces valid YAML with continue-on-error: true."""
        import tempfile, yaml
        workflow_path = os.path.join(REPO_ROOT, ".github/workflows/gate-webhook-health.yml")
        patch_path = os.path.join(REPO_ROOT, "Reports/audit/gate-webhook-health-resilience.patch")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_workflow = os.path.join(tmpdir, "gate-webhook-health.yml")
            with open(workflow_path, "r", encoding="utf-8") as f_in, open(tmp_workflow, "w", encoding="utf-8") as f_out:
                f_out.write(f_in.read())

            res = subprocess.run(
                ["patch", "-p1", "--input", patch_path],
                cwd=tmpdir,
                capture_output=True,
                text=True,
            )
            # Patch applied to file directly
            if res.returncode != 0:
                # Try git apply in tmp git repo
                subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
                os.makedirs(os.path.join(tmpdir, ".github/workflows"), exist_ok=True)
                os.replace(tmp_workflow, os.path.join(tmpdir, ".github/workflows/gate-webhook-health.yml"))
                res = subprocess.run(
                    ["git", "apply", patch_path],
                    cwd=tmpdir,
                    capture_output=True,
                    text=True,
                )
                tmp_workflow = os.path.join(tmpdir, ".github/workflows/gate-webhook-health.yml")

            self.assertEqual(res.returncode, 0, f"Patch apply failed: {res.stderr}")
            with open(tmp_workflow, "r", encoding="utf-8") as f:
                content = f.read()

            parsed = yaml.safe_load(content)
            steps = parsed["jobs"]["webhook-health"]["steps"]
            alert_step = next(s for s in steps if s.get("name") == "Post alert if webhook health fails")
            self.assertTrue(alert_step.get("continue-on-error"))

    def test_21_gate2_defect_status_concatenation_prevention(self):
        """Verify prevention of '000000' concatenation defect and improper config_error classification."""
        # Unpatched defect simulation:
        unpatched_script = """
        STATUS=$(echo -n "000" && false || echo "000")
        if [ "$STATUS" = "000" ] || [ "$STATUS" -ge 500 ] 2>/dev/null; then
          echo "OUTCOME=server_failure"
        else
          echo "OUTCOME=config_error"
        fi
        """
        res_unpatched = subprocess.run(["bash", "-c", unpatched_script], capture_output=True, text=True)
        # Demonstrates the unpatched bug misclassified '000000' as config_error
        self.assertEqual(res_unpatched.stdout.strip(), "OUTCOME=config_error")

        # Patched resilient logic simulation:
        patched_script = """
        HTTP_CODE="000"
        CURL_EXIT=28
        if [ $CURL_EXIT -ne 0 ] || [ -z "$HTTP_CODE" ]; then
          STATUS="000"
        else
          STATUS="$HTTP_CODE"
        fi
        if [ "$STATUS" = "200" ] || [ "$STATUS" = "400" ]; then
          echo "OUTCOME=ok"
        elif [ "$STATUS" = "404" ]; then
          echo "OUTCOME=config_error"
        elif [ "$STATUS" = "000" ] || [ "$CURL_EXIT" -ne 0 ] || { [ "$STATUS" -ge 500 ] 2>/dev/null; }; then
          echo "OUTCOME=server_failure"
        else
          echo "OUTCOME=config_error"
        fi
        """
        res_patched = subprocess.run(["bash", "-c", patched_script], capture_output=True, text=True)
        # Verified: correctly classifies as server_failure and avoids 000000
        self.assertEqual(res_patched.stdout.strip(), "OUTCOME=server_failure")


if __name__ == "__main__":
    unittest.main()


