"""Per-call model override through ask() and into scheduled tasks.

Scheduled runs have no request context, so before this every task was pinned
to the server's default model regardless of what it needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from openjarvis.system.orchestrator import QueryOrchestrator


class _FakeEngine:
    """Stands in for an engine, recording what it was asked to generate."""

    def __init__(self, models=(), engine_id="fake"):
        self._models = list(models)
        self.engine_id = engine_id
        self.calls = []

    def list_models(self):
        return self._models

    def generate(self, messages, *, model, **kwargs):
        self.calls.append(model)
        return {"content": "ok", "usage": {}}


def _system(engine, model="qwen3.5:4b"):
    """Minimal stand-in for JarvisSystem with agent routing disabled."""
    s = MagicMock()
    s.engine = engine
    s.model = model
    s.engine_key = "ollama"
    s.agent_name = "none"
    s.config.intelligence.temperature = 0.7
    s.config.intelligence.max_tokens = 1024
    s.config.agent.context_from_memory = False
    return s


class TestEngineSelection:
    def test_no_override_uses_the_system_model(self):
        engine = _FakeEngine(["qwen3.5:4b"])
        orch = QueryOrchestrator(_system(engine))

        assert orch._engine_for_model(None) == (engine, "qwen3.5:4b", "ollama")

    def test_same_model_is_a_no_op(self):
        engine = _FakeEngine(["qwen3.5:4b"])
        orch = QueryOrchestrator(_system(engine))

        assert orch._engine_for_model("qwen3.5:4b") == (engine, "qwen3.5:4b", "ollama")

    def test_multiengine_that_lists_the_model_keeps_serving_it(self):
        """A MultiEngine already routes by prefix; do not resolve around it."""
        engine = _FakeEngine(["qwen3.5:4b", "gpt-5.6-luna"], engine_id="multi")
        orch = QueryOrchestrator(_system(engine))

        assert orch._engine_for_model("gpt-5.6-luna") == (
            engine,
            "gpt-5.6-luna",
            "ollama",
        )

    def test_cloud_model_on_a_single_backend_resolves_the_cloud_engine(self):
        """A local engine reports can_serve() True for anything, then 404s."""
        local = _FakeEngine(["qwen3.5:4b"])
        cloud = _FakeEngine(["gpt-5.6-luna"], engine_id="cloud")
        orch = QueryOrchestrator(_system(local))

        with patch(
            "openjarvis.engine._discovery.get_engine", return_value=("cloud", cloud)
        ):
            resolved_engine, resolved_model, key = orch._engine_for_model(
                "gpt-5.6-luna"
            )

        assert resolved_engine is cloud
        assert resolved_model == "gpt-5.6-luna"
        assert key == "cloud"

    def test_unavailable_cloud_engine_falls_back_to_the_active_one(self):
        local = _FakeEngine(["qwen3.5:4b"])
        orch = QueryOrchestrator(_system(local))

        with patch("openjarvis.engine._discovery.get_engine", return_value=None):
            resolved_engine, _, _ = orch._engine_for_model("gpt-5.6-luna")

        assert resolved_engine is local

    def test_engine_resolution_error_is_survived(self):
        local = _FakeEngine(["qwen3.5:4b"])
        orch = QueryOrchestrator(_system(local))

        with patch(
            "openjarvis.engine._discovery.get_engine", side_effect=RuntimeError("boom")
        ):
            resolved_engine, _, _ = orch._engine_for_model("gpt-5.6-luna")

        assert resolved_engine is local

    def test_unknown_local_model_stays_on_the_active_engine(self):
        """Not a cloud name, so never divert it to cloud."""
        local = _FakeEngine(["qwen3.5:4b"])
        orch = QueryOrchestrator(_system(local))

        assert orch._engine_for_model("llama9:70b") == (local, "llama9:70b", "ollama")

    def test_engine_without_list_models_does_not_crash(self):
        engine = MagicMock()
        engine.list_models.side_effect = RuntimeError("not supported")
        orch = QueryOrchestrator(_system(engine))

        resolved_engine, resolved_model, _ = orch._engine_for_model("llama9:70b")
        assert resolved_engine is engine
        assert resolved_model == "llama9:70b"


class TestAskUsesTheOverride:
    def test_generate_receives_the_overridden_model(self):
        engine = _FakeEngine(["qwen3.5:4b", "gpt-5.6-luna"])
        orch = QueryOrchestrator(_system(engine))

        orch.ask("hi", model="gpt-5.6-luna")

        assert engine.calls == ["gpt-5.6-luna"]

    def test_result_reports_the_model_that_actually_ran(self):
        engine = _FakeEngine(["qwen3.5:4b", "gpt-5.6-luna"])
        orch = QueryOrchestrator(_system(engine))

        result = orch.ask("hi", model="gpt-5.6-luna")

        assert result["model"] == "gpt-5.6-luna"

    def test_without_override_the_default_still_runs(self):
        engine = _FakeEngine(["qwen3.5:4b"])
        orch = QueryOrchestrator(_system(engine))

        result = orch.ask("hi")

        assert engine.calls == ["qwen3.5:4b"]
        assert result["model"] == "qwen3.5:4b"


class TestScheduledTaskCarriesModel:
    """The task table has no migration path, so the model rides in metadata."""

    @pytest.fixture()
    def store(self, tmp_path):
        from openjarvis.scheduler.store import SchedulerStore

        s = SchedulerStore(tmp_path / "sched.db")
        yield s
        s.close()

    def test_metadata_model_is_passed_to_ask(self, store):
        from openjarvis.scheduler.scheduler import TaskScheduler

        system = MagicMock()
        system.ask.return_value = "done"
        sched = TaskScheduler(store, system=system, poll_interval=1)

        task = sched.create_task(
            "check things",
            "once",
            "2026-01-01T00:00:00+00:00",
            agent="orchestrator",
            metadata={"model": "gpt-5.6-luna"},
        )
        sched._execute_task(task)

        assert system.ask.call_args.kwargs["model"] == "gpt-5.6-luna"

    def test_no_metadata_model_leaves_ask_on_the_default(self, store):
        from openjarvis.scheduler.scheduler import TaskScheduler

        system = MagicMock()
        system.ask.return_value = "done"
        sched = TaskScheduler(store, system=system, poll_interval=1)

        task = sched.create_task(
            "check things", "once", "2026-01-01T00:00:00+00:00", agent="orchestrator"
        )
        sched._execute_task(task)

        assert "model" not in system.ask.call_args.kwargs

    def test_schedule_task_tool_persists_the_model(self, store):
        from openjarvis.scheduler.scheduler import TaskScheduler
        from openjarvis.scheduler.tools import ScheduleTaskTool, set_scheduler

        sched = TaskScheduler(store, poll_interval=60)
        set_scheduler(sched)
        try:
            # The tool validates the name against reachable cloud models, so
            # pin that list rather than depending on live credentials.
            with patch(
                "openjarvis.scheduler.tools._available_cloud_models",
                return_value=["gpt-5.6-luna"],
            ):
                ScheduleTaskTool().execute(
                    prompt="summarise my inbox",
                    schedule_type="interval",
                    schedule_value="3600",
                    model="gpt-5.6-luna",
                )
        finally:
            set_scheduler(None)

        assert sched.list_tasks()[0].metadata["model"] == "gpt-5.6-luna"

    def test_schedule_task_tool_omits_model_when_not_given(self, store):
        from openjarvis.scheduler.scheduler import TaskScheduler
        from openjarvis.scheduler.tools import ScheduleTaskTool, set_scheduler

        sched = TaskScheduler(store, poll_interval=60)
        set_scheduler(sched)
        try:
            ScheduleTaskTool().execute(
                prompt="p", schedule_type="interval", schedule_value="3600"
            )
        finally:
            set_scheduler(None)

        assert "model" not in sched.list_tasks()[0].metadata

    def test_model_survives_a_store_round_trip(self, store):
        """The task is reloaded from SQLite before it ever runs."""
        from openjarvis.scheduler.scheduler import TaskScheduler

        sched = TaskScheduler(store, poll_interval=60)
        created = sched.create_task(
            "p",
            "interval",
            "3600",
            metadata={"model": "gpt-5.6-luna"},
        )

        reloaded = [t for t in sched.list_tasks() if t.id == created.id][0]
        assert reloaded.metadata["model"] == "gpt-5.6-luna"


class TestEngineReporting:
    """A cloud-served run must not be reported as the local engine."""

    def test_reports_the_engine_that_served_the_call(self):
        local = _FakeEngine(["qwen3.5:4b"])
        cloud = _FakeEngine(["gpt-5.6-luna"], engine_id="cloud")
        orch = QueryOrchestrator(_system(local))

        with patch(
            "openjarvis.engine._discovery.get_engine", return_value=("cloud", cloud)
        ):
            result = orch.ask("hi", model="gpt-5.6-luna")

        assert result["engine"] == "cloud"
        assert result["model"] == "gpt-5.6-luna"

    def test_unrouted_call_still_reports_the_system_engine(self):
        engine = _FakeEngine(["qwen3.5:4b"])
        orch = QueryOrchestrator(_system(engine))

        assert orch.ask("hi")["engine"] == "ollama"
