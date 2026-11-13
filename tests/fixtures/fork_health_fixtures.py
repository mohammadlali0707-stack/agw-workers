#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test fixtures for fork workflow health checks and Gate 2 resilience verification.

Provides mock API responses, sanitized telemetry signatures, and simulated step behaviors
for testing Tools/audit_fork_workflow_health.py and Gate 2 dual-failure handling.
"""

# Mock response for a healthy fork repository
MOCK_WORKFLOWS_HEALTHY = {
    "total_count": 2,
    "workflows": [
        {
            "id": 101,
            "name": "AGW Worker",
            "path": ".github/workflows/agw-worker.yml",
            "state": "active",
        },
        {
            "id": 102,
            "name": "AGY Lead Plan",
            "path": ".github/workflows/agy-lead-plan.yml",
            "state": "active",
        },
    ],
}

# Mock response when GitHub failed to parse the workflow (name equals path fallback)
MOCK_WORKFLOWS_UNPARSEABLE = {
    "total_count": 2,
    "workflows": [
        {
            "id": 103,
            "name": ".github/workflows/agw-worker.yml",
            "path": ".github/workflows/agw-worker.yml",
            "state": "active",
        },
        {
            "id": 104,
            "name": "AGY Lead Plan",
            "path": ".github/workflows/agy-lead-plan.yml",
            "state": "active",
        },
    ],
}

# Mock response when watched workflows are absent
MOCK_WORKFLOWS_ABSENT = {
    "total_count": 1,
    "workflows": [
        {
            "id": 105,
            "name": "Other Workflow",
            "path": ".github/workflows/other.yml",
            "state": "active",
        }
    ],
}

# Gate 2 Dual-Failure Telemetry Constants (Sanitized)
GATE2_TELEMETRY = {
    "endpoint": "https://<sanitized-gateway-host>/webhook/github-runner-trigger",
    "primary_failure": {
        "step": "Verify webhook endpoint is reachable",
        "http_code": "404",
        "fail_type": "config_error",
        "fail_message": "HTTP 404: Webhook route not found. The endpoint path is incorrect or the n8n workflow is inactive/unregistered.",
        "cause": "Upstream security quarantine (credential rotation advisory)",
    },
    "secondary_failure_unpatched": {
        "step": "Post alert if webhook health fails",
        "error": "could not add label: alert not found",
        "exit_code": 1,
        "mechanism": "Fork GITHUB_TOKEN has restricted permissions; label 'alert' does not exist; set -e crashes runner",
    },
    "secondary_failure_patched": {
        "step": "Post alert if webhook health fails",
        "continue_on_error": True,
        "fallback_chain": [
            "gh issue create --label 'alert' 2>/dev/null",
            "gh issue create [unlabelled] 2>/dev/null",
            "echo ::warning::failed to create alert issue",
        ],
        "exit_code": 0,
        "runner_crash_prevented": True,
        "body_file": "/tmp/issue_body.md",
    },
}

# Gate 2 Timeout and Network Failure Telemetry Constants
GATE2_TELEMETRY_TIMEOUT = {
    "step": "Verify webhook endpoint is reachable",
    "http_code": "000",
    "curl_exit_code": 28,
    "fail_type": "server_failure",
    "fail_message_pattern": "HTTP 000.*Server failure or timeout",
    "cat_label_pattern": "Server Failure.*Timeout",
}

GATE2_TELEMETRY_CONNREFUSED = {
    "step": "Verify webhook endpoint is reachable",
    "http_code": "000",
    "curl_exit_code": 7,
    "fail_type": "server_failure",
    "fail_message_pattern": "HTTP 000.*Server failure or timeout",
    "cat_label_pattern": "Server Failure.*Timeout",
}

