"""Offline-only, fail-closed interpretation of a Notion Agent session response.

No network, persistence, approval, retry, response delivery or side effects.
A completed session is not evidence that an individual tool action succeeded.
"""
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class SessionDecision:
    state: str
    reason: str


def classify_session(response: Any, *, expected_session_id: str, expected_agent_id: str) -> SessionDecision:
    """Classify a response from GET /v1/sessions/{id}, without copying its content.

    Identifiers must come from an already authenticated and correlated request, not
    from the same untrusted response. This helper cannot authenticate a response.
    """
    review = lambda reason: SessionDecision('manual_review', reason)
    if not isinstance(expected_session_id, str) or not expected_session_id.strip():
        return review('missing_expected_session')
    if not isinstance(expected_agent_id, str) or not expected_agent_id.strip():
        return review('missing_expected_agent')
    if not isinstance(response, dict) or response.get('object') != 'session':
        return review('invalid_response')
    if response.get('id') != expected_session_id or response.get('agent_id') != expected_agent_id:
        return review('identity_mismatch')
    status = response.get('status')
    if not isinstance(status, str):
        return review('invalid_status')
    actions = response.get('required_actions', [])
    if not isinstance(actions, list):
        return review('invalid_actions')
    if status == 'requires_action':
        if not actions:
            return review('missing_required_actions')
        for action in actions:
            if not isinstance(action, dict) or not isinstance(action.get('action_id'), str) or not action['action_id'].strip():
                return review('invalid_required_action')
            options = action.get('options')
            if not isinstance(options, list) or not options or any(not isinstance(o, dict) or o.get('id') not in ('approve', 'reject') for o in options):
                return review('invalid_required_action')
        return SessionDecision('await_human', 'required_action')
    if actions:
        return review('unexpected_required_actions')
    if status in ('queued', 'in_progress'):
        return SessionDecision('pending', status)
    if status == 'completed':
        return SessionDecision('completed_unverified', 'inspect_events_before_delivery')
    if status in ('failed', 'canceled', 'terminated'):
        return SessionDecision('stopped', status)
    return review('unknown_status')
