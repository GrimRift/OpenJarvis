"""Regression tests for proactive scheduling and notification setup."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from openjarvis.agents.proactive_agent import (
    _PROACTIVE_CRON_PROMPT,
    _build_notification_channel,
    _index_digest,
    register_cron,
)
from openjarvis.core.registry import ChannelRegistry
from openjarvis.scheduler.scheduler import TaskScheduler
from openjarvis.scheduler.store import SchedulerStore


@pytest.fixture()
def scheduler(tmp_path):
    store = SchedulerStore(tmp_path / "scheduler.db")
    scheduler = TaskScheduler(store)
    yield scheduler
    scheduler.stop()
    store.close()


def _register(scheduler, *, schedule="0 5 * * *", channel="telegram:123"):
    return register_cron(
        scheduler,
        notification_channel_id=channel,
        cron_expr=schedule,
        hours_back=24,
        timezone="UTC",
    )


class TestRegisterCron:
    def test_reuses_exact_task_and_cancels_duplicates(self, scheduler):
        first = _register(scheduler)
        duplicate = scheduler.create_task(
            _PROACTIVE_CRON_PROMPT,
            "cron",
            "0 5 * * *",
            agent="proactive",
            metadata=first.metadata,
        )

        returned = _register(scheduler)

        assert returned.id in {first.id, duplicate.id}
        assert [task.id for task in scheduler.list_tasks(status="active")] == [
            returned.id
        ]
        cancelled_id = scheduler.list_tasks(status="cancelled")[0].id
        assert cancelled_id == ({first.id, duplicate.id} - {returned.id}).pop()

    def test_replaces_task_when_configuration_changes(self, scheduler):
        old = _register(scheduler, schedule="0 5 * * *", channel="telegram:old")

        new = _register(scheduler, schedule="0 7 * * *", channel="telegram:new")

        assert new.id != old.id
        assert new.schedule_value == "0 7 * * *"
        assert new.metadata["notification_channel_id"] == "telegram:new"
        assert scheduler.list_tasks(status="cancelled")[0].id == old.id

    def test_preserves_pause_across_restart(self, scheduler):
        paused = _register(scheduler)
        scheduler.pause_task(paused.id)

        returned = _register(scheduler, schedule="0 7 * * *")

        assert returned.id == paused.id
        assert returned.status == "paused"
        assert scheduler.list_tasks(status="active") == []

    def test_migrates_legacy_tasks_without_stable_key(self, scheduler):
        legacy = scheduler.create_task(
            _PROACTIVE_CRON_PROMPT,
            "cron",
            "0 5 * * *",
            agent="proactive",
            metadata={
                "notification_channel_id": "telegram:123",
                "hours_back": 24,
                "timezone": "UTC",
            },
        )

        current = _register(scheduler)

        assert current.id != legacy.id
        assert current.metadata["openjarvis_task_key"] == "proactive-daily"
        assert scheduler.list_tasks(status="cancelled")[0].id == legacy.id


class TestNotificationChannel:
    def test_telegram_is_configured_without_starting_polling(self):
        class FakeTelegram:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.connect = MagicMock()

        config = MagicMock()
        with (
            patch.object(ChannelRegistry, "contains", return_value=True),
            patch.object(ChannelRegistry, "get", return_value=FakeTelegram),
            patch("openjarvis.core.config.load_config", return_value=config),
            patch(
                "openjarvis.system._channel_kwargs.build_channel_kwargs",
                return_value={"bot_token": "configured-token"},
            ),
        ):
            channel = _build_notification_channel("telegram:123")

        assert channel.kwargs == {"bot_token": "configured-token"}
        channel.connect.assert_not_called()

    def test_non_telegram_channel_keeps_connect_lifecycle(self):
        class FakeChannel:
            def __init__(self, **kwargs):
                self.connect = MagicMock()

        with (
            patch.object(ChannelRegistry, "contains", return_value=True),
            patch.object(ChannelRegistry, "get", return_value=FakeChannel),
        ):
            channel = _build_notification_channel("twilio:15551234567")

        channel.connect.assert_called_once_with()


# -- Payload/description consistency -----------------------------------------

# Two real lines from a live run, kept verbatim: a small local model proposed
# deleting "Wells Fargo marketing email" (a subject copied from this agent's own
# few-shot example, matching no real message) while pointing at the Cartesia
# one, and named the Cartesia mail while pointing at the Microsoft one.
_MICROSOFT_ID = "gmail:1a03ca75b9465ae3"
_CARTESIA_ID = "gmail:1a03d08c6f15cf7f"
_REAL_DIGEST = f"""=== MESSAGES ===
[gmail id={_CARTESIA_ID}] From: Karan at Cartesia <karan@mail.cartesia.ai> \
— "What makes Cartesia different" (12h ago)
  Preview: Hey there, hope you've had fun with your first week
[gmail id={_MICROSOFT_ID}] From: Microsoft <microsoft-noreply@microsoft.com> \
— "Your PC Game Pass subscription will end soon" (14h ago)
[imessage] Someone (2h ago)
[gcalendar id=gcalendar:evt123] 09:00 — Standup (09:00-09:15)
"""


class TestIndexDigest:
    def test_indexes_only_lines_carrying_an_id(self):
        index = _index_digest(_REAL_DIGEST)
        assert set(index) == {_CARTESIA_ID, _MICROSOFT_ID, "gcalendar:evt123"}
        assert index[_MICROSOFT_ID].startswith("From: Microsoft")
        assert index["gcalendar:evt123"] == "09:00 — Standup (09:00-09:15)"

    def test_empty_digest_yields_empty_index(self):
        assert _index_digest("") == {}


class TestProposalValidation:
    """Queued descriptions must describe the item the payload actually targets."""

    def _run_with(self, tmp_path, proposals):
        from openjarvis.agents.proactive_agent import ProactiveAgent
        from openjarvis.tools.approval_store import ApprovalStore

        store = ApprovalStore(db_path=str(tmp_path / "approvals.db"))
        agent = ProactiveAgent(
            engine=MagicMock(),
            model="test-model",
            approval_store=store,
        )
        collect = MagicMock(success=True, content=_REAL_DIGEST)
        agent._executor = MagicMock()
        agent._executor.execute.return_value = collect
        agent._generate = MagicMock(
            return_value={"content": "```json\n" + json.dumps(proposals) + "\n```"}
        )
        agent.run()
        return store.list_pending()

    def test_mislabelled_proposal_is_described_from_its_real_target(self, tmp_path):
        pending = self._run_with(
            tmp_path,
            [
                {
                    "action_type": "email_delete",
                    "description": "Delete Cartesia co-founder story email",
                    "payload": {"doc_id": _MICROSOFT_ID, "message_id": "x"},
                    "permission_key": "email_delete:from:cartesia.ai",
                    "tier": "low",
                    "reasoning": "not interested",
                }
            ],
        )
        assert len(pending) == 1
        # Names the message it will actually delete, not the one it claimed.
        assert "Microsoft" in pending[0].description
        assert "Cartesia" not in pending[0].description

    def test_proposal_with_unknown_doc_id_is_dropped(self, tmp_path):
        pending = self._run_with(
            tmp_path,
            [
                {
                    "action_type": "email_delete",
                    "description": "Delete Wells Fargo marketing email",
                    "payload": {"doc_id": "gmail:invented", "message_id": "x"},
                    "permission_key": "email_delete:from:wf.com",
                    "tier": "low",
                    "reasoning": "marketing",
                }
            ],
        )
        assert pending == []

    def test_duplicate_doc_id_is_queued_once(self, tmp_path):
        pending = self._run_with(
            tmp_path,
            [
                {
                    "action_type": "email_delete",
                    "description": "Delete Microsoft email",
                    "payload": {"doc_id": _MICROSOFT_ID},
                    "permission_key": "email_delete:from:microsoft.com",
                    "tier": "low",
                    "reasoning": "renewal notice",
                },
                {
                    "action_type": "email_delete",
                    "description": "Delete Cartesia email",
                    "payload": {"doc_id": _MICROSOFT_ID},
                    "permission_key": "email_delete:from:cartesia.ai",
                    "tier": "low",
                    "reasoning": "story email",
                },
            ],
        )
        assert len(pending) == 1
