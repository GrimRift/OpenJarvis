"""Focused tests for deterministic proactive approval reply parsing."""

from openjarvis.tools.approval_store import (
    STATUS_APPROVED,
    STATUS_EXECUTED,
    STATUS_FAILED,
    STATUS_PENDING,
    TIER_MEDIUM,
    ApprovalStore,
)
from openjarvis.tools.proactive_tools import (
    execute_approved_actions,
    looks_like_approval_response,
    parse_approval_response,
)


def _queue(store: ApprovalStore):
    return store.queue_action(
        action_type="email_archive",
        description="Archive a newsletter",
        payload={"message_id": "message-1"},
        permission_key="email_archive:newsletter",
        tier=TIER_MEDIUM,
    )


def test_bracketed_notification_reply_is_recognized():
    assert looks_like_approval_response("[abcdef123456] yes")
    assert looks_like_approval_response("always no {abcdef123456}")
    assert not looks_like_approval_response("yes, that sounds good")


def test_parser_approves_pending_action(tmp_path):
    store = ApprovalStore(db_path=str(tmp_path / "approvals.db"))
    action = _queue(store)

    processed = parse_approval_response(f"[{action.id}] yes", store)

    assert processed == [
        {"id": action.id, "approved": True, "remembered": False}
    ]
    assert store.get_action(action.id).status == STATUS_APPROVED


def test_parser_does_not_reopen_an_already_decided_action(tmp_path):
    store = ApprovalStore(db_path=str(tmp_path / "approvals.db"))
    action = _queue(store)
    store.update_status(action.id, STATUS_APPROVED)

    assert parse_approval_response(f"{action.id} no", store) == []
    assert store.get_action(action.id).status == STATUS_APPROVED


def test_duplicate_token_is_processed_only_once(tmp_path):
    store = ApprovalStore(db_path=str(tmp_path / "approvals.db"))
    action = _queue(store)

    processed = parse_approval_response(
        f"{action.id} yes and {action.id} yes",
        store,
    )

    assert len(processed) == 1
    assert store.get_action(action.id).status != STATUS_PENDING


def test_approved_draft_executes_without_external_side_effect(tmp_path):
    store = ApprovalStore(db_path=str(tmp_path / "approvals.db"))
    action = store.queue_action(
        action_type="sms_draft_reply",
        description="Create a harmless test draft",
        payload={"draft": "This is only a test draft."},
        permission_key="sms_draft_reply:test",
        tier=TIER_MEDIUM,
    )
    store.update_status(action.id, STATUS_APPROVED)

    results = execute_approved_actions([action.id], store)

    assert results[0]["success"] is True
    assert "Draft saved" in results[0]["message"]
    assert store.get_action(action.id).status == STATUS_EXECUTED


def test_failed_execution_remains_visible_and_retryable(tmp_path):
    store = ApprovalStore(db_path=str(tmp_path / "approvals.db"))
    action = _queue(store)
    store.update_status(action.id, STATUS_APPROVED)

    results = execute_approved_actions(
        [action.id],
        store,
        executor_fn=lambda _action: (False, "temporary failure"),
    )

    assert results[0]["success"] is False
    assert store.get_action(action.id).status == STATUS_FAILED
    assert [item.id for item in store.list_pending()] == [action.id]

    processed = parse_approval_response(f"{action.id} yes", store)
    assert processed[0]["approved"] is True
