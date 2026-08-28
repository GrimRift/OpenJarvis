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


class TestPersonaReachesAsk:
    """serve.py gives the web agent a prompt builder; ask() got none.

    So Telegram, scheduled tasks and the proactive summary all ran without
    the persona, without SOUL/MEMORY/USER.md, and without the configured
    system-prompt rules. Asked who it was over Telegram, Sage replied "I'm
    OpenJarvis ... running locally on your hardware".
    """

    def _orchestrator_with_agent(self, accepts_builder: bool):
        from openjarvis.agents._stubs import AgentResult
        from openjarvis.core.registry import AgentRegistry

        captured = {}

        if accepts_builder:

            class _Agent:
                agent_id = "probe"
                accepts_tools = False

                def __init__(self, engine, model, *, prompt_builder=None, **kw):
                    captured["prompt_builder"] = prompt_builder

                def run(self, input, context=None, **kw):
                    return AgentResult(content="ok", turns=1)
        else:

            class _Agent:  # type: ignore[no-redef]
                agent_id = "probe"
                accepts_tools = False

                def __init__(self, engine, model, **kw):
                    captured["kwargs"] = kw

                def run(self, input, context=None, **kw):
                    return AgentResult(content="ok", turns=1)

        AgentRegistry.register("probe")(_Agent)
        from openjarvis.core.config import JarvisConfig

        engine = _FakeEngine(["qwen3.5:4b"])
        system = _system(engine)
        # SystemPromptBuilder validates these, so a MagicMock config makes it
        # raise and the builder degrade to None.
        real = JarvisConfig()
        system.config.memory_files = real.memory_files
        system.config.system_prompt = real.system_prompt
        system.config.agent.default_system_prompt = ""
        system.agent_name = "probe"
        system.tools = []
        system.capability_policy = None
        system.session_store = None
        system.trace_store = None
        return QueryOrchestrator(system), captured

    def test_agent_receives_the_configured_prompt_builder(self):
        orch, captured = self._orchestrator_with_agent(accepts_builder=True)
        orch.ask("who are you", agent="probe")
        assert captured["prompt_builder"] is not None

    def test_channel_builder_contains_the_configured_soul(self, tmp_path):
        """Telegram and scheduled requests use this same orchestrator path."""
        soul_path = tmp_path / "SOUL.md"
        soul_path.write_text("CHANNEL_SAGE_PERSONA_SENTINEL", encoding="utf-8")

        orch, captured = self._orchestrator_with_agent(accepts_builder=True)
        orch._system.config.memory_files.soul_path = str(soul_path)
        orch.ask("who are you", agent="probe")

        assert "CHANNEL_SAGE_PERSONA_SENTINEL" in captured[
            "prompt_builder"
        ].build()

    def test_builder_is_reused_across_calls(self):
        """SystemPromptBuilder freezes a prefix per instance for cache stability."""
        orch, captured = self._orchestrator_with_agent(accepts_builder=True)
        orch.ask("one", agent="probe")
        first = captured["prompt_builder"]
        orch.ask("two", agent="probe")
        assert captured["prompt_builder"] is first

    def test_agents_without_the_parameter_are_untouched(self):
        orch, captured = self._orchestrator_with_agent(accepts_builder=False)
        orch.ask("hi", agent="probe")
        assert "prompt_builder" not in captured.get("kwargs", {})

    def test_explicit_system_prompt_still_wins(self):
        """Operators pass their own prompt; it must not be overridden."""
        orch, captured = self._orchestrator_with_agent(accepts_builder=True)
        orch.ask("hi", agent="probe", system_prompt="OPERATOR PROMPT")
        assert captured.get("prompt_builder") is None


class TestReportedModelIsTheOneThatRan:
    """An agent may swap its own engine/model after construction. Reporting the
    passed-in one instead made a proactive digest running on gpt-5.6-luna log
    itself as qwen3.5:4b, which is exactly the fact the tiering decision rests
    on."""

    def _orchestrator(self, agent_model, agent_engine=None):
        from openjarvis.agents._stubs import AgentResult
        from openjarvis.core.config import JarvisConfig
        from openjarvis.core.registry import AgentRegistry

        class _Agent:
            agent_id = "override_probe"
            accepts_tools = False

            def __init__(self, engine, model, **kw):
                self._model = agent_model
                self._engine = agent_engine if agent_engine is not None else engine

            def run(self, input, context=None, **kw):
                return AgentResult(content="ok", turns=1)

        AgentRegistry.register("override_probe")(_Agent)

        engine = _FakeEngine(["qwen3.5:4b"])
        system = _system(engine)
        real = JarvisConfig()
        system.config.memory_files = real.memory_files
        system.config.system_prompt = real.system_prompt
        system.config.agent.default_system_prompt = ""
        system.agent_name = "override_probe"
        system.tools = []
        system.capability_policy = None
        system.session_store = None
        system.trace_store = None
        return QueryOrchestrator(system)

    def test_the_agents_own_model_is_reported(self):
        orch = self._orchestrator("gpt-5.6-luna")
        assert orch.ask("hi", agent="override_probe")["model"] == "gpt-5.6-luna"

    def test_an_agent_that_keeps_the_given_model_reports_it_unchanged(self):
        orch = self._orchestrator("qwen3.5:4b")
        assert orch.ask("hi", agent="override_probe")["model"] == "qwen3.5:4b"

    def test_a_swapped_engine_is_reported_under_its_registry_key(self):
        from openjarvis.core.registry import EngineRegistry

        class _Cloudish(_FakeEngine):
            def __init__(self):
                super().__init__(["gpt-5.6-luna"])

        EngineRegistry.register("cloudish")(_Cloudish)
        orch = self._orchestrator("gpt-5.6-luna", agent_engine=_Cloudish())
        assert orch.ask("hi", agent="override_probe")["engine"] == "cloudish"

    def test_an_unregistered_engine_falls_back_to_the_system_key(self):
        """Reporting must never crash a run that otherwise succeeded."""
        orch = self._orchestrator("m", agent_engine=_FakeEngine([]))
        assert orch.ask("hi", agent="override_probe")["engine"] == "ollama"
