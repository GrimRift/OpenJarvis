"""Regression tests for proactive scheduling and notification setup."""

from __future__ import annotations

import json
from types import SimpleNamespace
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


class TestNotificationVoice:
    def test_pending_actions_are_direct_and_decision_led(self):
        from openjarvis.agents.proactive_agent import ProactiveAgent

        agent = object.__new__(ProactiveAgent)
        pending = [
            SimpleNamespace(id="abc123", tier="medium", description="Send reply")
        ]

        notification = agent._build_notification([], pending)

        assert notification.startswith("Your decision is needed (1 action):")
        assert "Reply '{id} yes' or '{id} no'." in notification
        assert "Needs your approval" not in notification

    def test_completed_actions_use_composed_status_language(self):
        from openjarvis.agents.proactive_agent import ProactiveAgent

        agent = object.__new__(ProactiveAgent)
        notification = agent._build_notification(
            [{"success": True, "description": "Archived newsletter"}], []
        )

        assert notification.startswith("Handled automatically (1 action):")


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


# -- Configured model / engine -----------------------------------------------


class TestConfiguredModel:
    """A scheduled run reaches this agent via JarvisSystem.ask(), which takes
    no model argument — so [proactive] model/engine is the only way to judge
    tiering on something stronger than the server default."""

    @staticmethod
    def _apply(args, kwargs, model="", engine=""):
        from openjarvis.agents.proactive_agent import ProactiveAgent

        return ProactiveAgent._apply_configured_model(args, kwargs, model, engine)

    def test_no_config_leaves_arguments_untouched(self):
        engine = MagicMock()
        args, kwargs = self._apply((engine, "qwen3.5:4b"), {})
        assert args == (engine, "qwen3.5:4b")

    def test_model_replaces_the_positional_model(self):
        engine = MagicMock()
        args, _ = self._apply((engine, "qwen3.5:4b"), {}, model="gpt-5.6-luna")
        assert args == (engine, "gpt-5.6-luna")

    def test_model_replaces_a_keyword_model(self):
        """Tests and scripts construct the agent with keywords, not positionally."""
        engine = MagicMock()
        _, kwargs = self._apply(
            (), {"engine": engine, "model": "qwen3.5:4b"}, model="gpt-5.6-luna"
        )
        assert kwargs["model"] == "gpt-5.6-luna"

    def test_engine_is_swapped_when_resolvable(self):
        cloud = MagicMock()
        with patch(
            "openjarvis.engine._discovery.get_engine", return_value=("cloud", cloud)
        ):
            args, _ = self._apply(
                (MagicMock(), "m"), {}, model="gpt-5.6-luna", engine="cloud"
            )
        assert args[0] is cloud

    def test_unavailable_engine_falls_back_instead_of_crashing(self):
        original = MagicMock()
        with patch("openjarvis.engine._discovery.get_engine", return_value=None):
            args, _ = self._apply((original, "m"), {}, engine="cloud")
        assert args[0] is original

    def test_engine_resolution_failure_is_survived(self):
        original = MagicMock()
        with patch(
            "openjarvis.engine._discovery.get_engine", side_effect=RuntimeError("boom")
        ):
            args, _ = self._apply((original, "m"), {}, engine="cloud")
        assert args[0] is original


    def test_positional_construction_ends_up_on_the_configured_model(self):
        """The unit tests above cover _apply_configured_model in isolation. This
        one goes through the real __init__ the way orchestrator._run_agent calls
        it -- positionally, with the system model -- because that is the only
        path a scheduled digest actually takes."""
        from openjarvis.agents.proactive_agent import ProactiveAgent

        config = MagicMock()
        config.proactive.model = "gpt-5.6-luna"
        config.proactive.engine = ""

        engine = MagicMock()
        engine.list_models.return_value = ["qwen3.5:4b", "gpt-5.6-luna"]
        with patch("openjarvis.core.config.load_config", return_value=config):
            agent = ProactiveAgent(engine, "qwen3.5:4b")

        assert agent._model == "gpt-5.6-luna"


class TestConnectorFailuresAreReported:
    """A source that failed to fetch is not a source with nothing in it.

    An expired Google token made every connector fail and this agent still
    reported "Nothing to report" — which reads exactly like a quiet inbox,
    the one summary a user acts on by doing nothing.
    """

    def _agent_with_collect(self, content: str, metadata: dict):
        from openjarvis.agents.proactive_agent import ProactiveAgent
        from openjarvis.tools.approval_store import ApprovalStore

        agent = ProactiveAgent(
            engine=MagicMock(),
            model="test-model",
            approval_store=ApprovalStore(db_path=":memory:"),
        )
        agent._executor = MagicMock()
        agent._executor.execute.return_value = MagicMock(
            success=True, content=content, metadata=metadata
        )
        agent._generate = MagicMock(return_value={"content": "```json\n[]\n```"})
        return agent

    def test_total_failure_does_not_claim_the_inbox_is_clear(self):
        agent = self._agent_with_collect(
            "=== ERRORS ===\nError fetching from 'gmail': invalid_grant",
            {
                "sources_failed": ["Error fetching from 'gmail': invalid_grant"],
                "total_items": 0,
            },
        )

        result = agent.run()

        assert "not an all-clear" in result.content
        assert "gmail" in result.content
        assert result.metadata["collection_failed"] is True

    def test_total_failure_queues_nothing(self):
        agent = self._agent_with_collect(
            "=== ERRORS ===\nError fetching from 'gmail': invalid_grant",
            {
                "sources_failed": ["Error fetching from 'gmail': invalid_grant"],
                "total_items": 0,
            },
        )

        result = agent.run()

        assert result.metadata["pending_approval"] == 0
        assert result.metadata["auto_executed"] == 0

    def test_partial_failure_is_flagged_alongside_the_summary(self):
        agent = self._agent_with_collect(
            "=== MESSAGES ===\n[gmail id=gmail:1] From: a — \"b\" (1h ago)",
            {
                "sources_failed": ["Error fetching from 'gcalendar': invalid_grant"],
                "total_items": 1,
            },
        )

        result = agent.run()

        assert "may be incomplete" in result.content
        assert "gcalendar" in result.content

    def test_a_clean_run_says_nothing_about_failures(self):
        agent = self._agent_with_collect(
            "=== MESSAGES ===\n[gmail id=gmail:1] From: a — \"b\" (1h ago)",
            {"sources_failed": [], "total_items": 1},
        )

        result = agent.run()

        assert "incomplete" not in result.content
        assert result.metadata["sources_failed"] == []


class TestEmptyClassificationIsRetried:
    """An empty answer must not become "Nothing requires your attention."

    Live on 2026-08-29 at 05:00 the classification step returned 0 chars, so
    0 proposals were parsed and the run reported an all-clear over a digest
    that had collected items. The agent already refuses to draw that
    conclusion when its sources fail (see the collection_failed branch); a
    model that says nothing is the same silence.
    """

    def _agent(self, tmp_path, responses):
        from openjarvis.agents.proactive_agent import ProactiveAgent
        from openjarvis.tools.approval_store import ApprovalStore

        store = ApprovalStore(db_path=str(tmp_path / "approvals.db"))
        agent = ProactiveAgent(
            engine=MagicMock(),
            model="test-model",
            approval_store=store,
        )
        agent._executor = MagicMock()
        agent._executor.execute.return_value = MagicMock(
            success=True, content=_REAL_DIGEST
        )
        agent._generate = MagicMock(side_effect=list(responses))
        return agent

    def test_a_good_answer_is_not_retried(self, tmp_path):
        agent = self._agent(tmp_path, [{"content": "```json\n[]\n```"}])
        agent.run()
        assert agent._generate.call_count == 1

    def test_an_empty_answer_is_retried_once(self, tmp_path):
        agent = self._agent(
            tmp_path,
            [{"content": "", "finish_reason": "stop"}, {"content": "```json\n[]\n```"}],
        )
        result = agent.run()
        assert agent._generate.call_count == 2
        assert result.metadata["classification_empty"] is False

    def test_a_response_that_is_only_thinking_counts_as_empty(self, tmp_path):
        agent = self._agent(
            tmp_path,
            [
                {"content": "<think>weighing it up</think>", "finish_reason": "stop"},
                {"content": "```json\n[]\n```"},
            ],
        )
        agent.run()
        assert agent._generate.call_count == 2

    def test_running_out_of_budget_retries_with_more_headroom(self, tmp_path):
        agent = self._agent(
            tmp_path,
            [
                {"content": "", "finish_reason": "length"},
                {"content": "```json\n[]\n```"},
            ],
        )
        agent.run()
        retry_kwargs = agent._generate.call_args_list[1].kwargs
        assert retry_kwargs["max_tokens"] > agent._max_tokens

    def test_a_blank_answer_does_not_buy_headroom_it_does_not_need(self, tmp_path):
        agent = self._agent(
            tmp_path,
            [
                {"content": "", "finish_reason": "stop"},
                {"content": "```json\n[]\n```"},
            ],
        )
        agent.run()
        assert "max_tokens" not in agent._generate.call_args_list[1].kwargs

    def test_the_retry_is_bounded_to_one(self, tmp_path):
        agent = self._agent(
            tmp_path,
            [
                {"content": "", "finish_reason": "stop"},
                {"content": "", "finish_reason": "stop"},
            ],
        )
        agent.run()
        assert agent._generate.call_count == 2

    def test_still_empty_is_recorded_rather_than_announced(self, tmp_path):
        """The user chose to be told in the log, not at 05:00."""
        agent = self._agent(
            tmp_path,
            [
                {"content": "", "finish_reason": "stop"},
                {"content": "", "finish_reason": "stop"},
            ],
        )
        result = agent.run()
        assert result.metadata["classification_empty"] is True


class TestCollectedSourcesAreReadable:
    """Every configured source must be one this machine can actually read.

    iMessage cannot work on Windows, Slack was never connected, and the Tasks
    API was never enabled for the Google project. All three were still in the
    list, so every run ended with "I couldn't read imessage, slack,
    google_tasks this run, so this update may be incomplete" — which teaches
    the user to ignore the one sentence whose job is to say the summary is
    incomplete. That warning only means something when it is rare.
    """

    UNREADABLE = {"imessage", "slack", "google_tasks"}

    def _collected_sources(self, tmp_path) -> list[str]:
        from openjarvis.agents.proactive_agent import ProactiveAgent
        from openjarvis.tools.approval_store import ApprovalStore

        store = ApprovalStore(db_path=str(tmp_path / "approvals.db"))
        agent = ProactiveAgent(
            engine=MagicMock(), model="test-model", approval_store=store
        )
        agent._executor = MagicMock()
        agent._executor.execute.return_value = MagicMock(
            success=True, content=_REAL_DIGEST
        )
        agent._generate = MagicMock(return_value={"content": "```json\n[]\n```"})
        agent.run()

        collect = agent._executor.execute.call_args_list[0].args[0]
        return json.loads(collect.arguments)["sources"]

    def test_no_permanently_unreadable_source_is_requested(self, tmp_path):
        requested = set(self._collected_sources(tmp_path))
        assert not (requested & self.UNREADABLE), (
            f"{requested & self.UNREADABLE} cannot be read on this machine; "
            "requesting them makes every digest report itself incomplete."
        )

    def test_it_still_collects_something(self, tmp_path):
        """Emptying the list would satisfy the check above and collect nothing."""
        assert "gmail" in self._collected_sources(tmp_path)
