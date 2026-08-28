"""Tests for per-request tool-schema routing.

Tool schemas dominate the input context (3,791 tokens against 1,540 for the
whole system prompt on the live 23-tool setup) and are re-sent every turn.
Routing trims them, so the tests that matter most are the ones proving a
capability is never hidden.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openjarvis.agents.tool_routing import route_tools, routing_text


def _schema(name: str) -> dict:
    return {
        "type": "function",
        "function": {"name": name, "description": f"{name} tool", "parameters": {}},
    }


ALL_NAMES = [
    "calculator",
    "retrieval",
    "file_read",
    "directory_list",
    "file_write",
    "apply_patch",
    "git_status",
    "git_diff",
    "git_log",
    "coding_command",
    "git_commit",
    "web_search",
    "notify_windows",
    "check_class_schedule",
    "notify_class_schedule",
    "open_app",
    "spotify_control",
    "schedule_task",
    "list_scheduled_tasks",
    "pause_scheduled_task",
    "resume_scheduled_task",
    "cancel_scheduled_task",
    "world_time",
]
ALL_SCHEMAS = [_schema(n) for n in ALL_NAMES]


def _routed(text: str) -> set:
    return {
        s["function"]["name"] for s in route_tools(ALL_SCHEMAS, routing_text(text))
    }


class TestNothingIsHidden:
    """A missing tool reads to the user as "Sage can't do that", so these are
    the tests that must not be relaxed."""

    @pytest.mark.parametrize(
        "text,required",
        [
            ("what time is it in Japan?", "world_time"),
            ("check my inbox", "retrieval"),
            ("search the web for the RTX 5090 price", "web_search"),
            ("what's 15% of 2400?", "calculator"),
            ("notify me when it's done", "notify_windows"),
            ("play a song", "spotify_control"),
            ("next song", "spotify_control"),
            ("pause the music", "spotify_control"),
            ("open obsidian", "open_app"),
            ("launch notepad", "open_app"),
            ("what is my class schedule for today?", "check_class_schedule"),
            ("do i have class tomorrow", "check_class_schedule"),
            ("remind me to sleep at 10pm every day", "schedule_task"),
            ("schedule a daily digest", "schedule_task"),
            ("list my scheduled tasks", "list_scheduled_tasks"),
            ("cancel that reminder", "cancel_scheduled_task"),
            ("pause the 9am task", "pause_scheduled_task"),
            ("read C:/AI/OpenJarvis-Lab/README.md", "file_read"),
            ("what files are in the project folder?", "directory_list"),
            ("what changed in the repo?", "git_diff"),
            ("show me the git log", "git_log"),
            ("run the tests", "coding_command"),
            ("commit this", "git_commit"),
            ("write a patch for that bug", "apply_patch"),
        ],
    )
    def test_the_obvious_tool_survives_routing(self, text, required):
        assert required in _routed(text)

    def test_core_tools_are_always_sent(self):
        core = {
            "retrieval",
            "web_search",
            "world_time",
            "calculator",
            "notify_windows",
        }
        assert core <= _routed("hello")
        assert core <= _routed("")

    def test_an_ungrouped_tool_is_always_sent(self):
        """Adding a tool must not silently hide it. It has to be assigned to a
        group deliberately before routing can ever drop it."""
        schemas = ALL_SCHEMAS + [_schema("brand_new_tool")]
        kept = {
            s["function"]["name"] for s in route_tools(schemas, routing_text("hello"))
        }
        assert "brand_new_tool" in kept

    def test_a_follow_up_inherits_the_topic(self):
        prior = [MagicMock(content="open spotify")]
        text = routing_text("do it", prior)
        assert "spotify_control" in {
            s["function"]["name"] for s in route_tools(ALL_SCHEMAS, text)
        }

    def test_dict_shaped_history_also_works(self):
        prior = [{"role": "user", "content": "commit the changes"}]
        text = routing_text("go ahead", prior)
        assert "git_commit" in {
            s["function"]["name"] for s in route_tools(ALL_SCHEMAS, text)
        }


class TestItActuallyTrims:
    def test_a_conversational_turn_drops_the_gated_groups(self):
        kept = _routed("who are you?")
        assert kept == {
            "retrieval",
            "web_search",
            "world_time",
            "calculator",
            "notify_windows",
        }

    def test_order_is_preserved_for_prompt_cache_stability(self):
        routed = route_tools(ALL_SCHEMAS, routing_text("commit this"))
        names = [s["function"]["name"] for s in routed]
        assert names == [n for n in ALL_NAMES if n in set(names)]

    def test_routing_is_deterministic(self):
        assert _routed("play a song") == _routed("play a song")

    def test_an_empty_tool_list_stays_empty(self):
        assert route_tools([], routing_text("anything")) == []

    def test_a_schema_without_a_name_is_kept(self):
        odd = [{"type": "function", "function": {}}]
        assert route_tools(odd, routing_text("hello")) == odd


class TestOrchestratorWiring:
    def _agent(self, **kwargs):
        from openjarvis.agents.orchestrator import OrchestratorAgent

        return OrchestratorAgent(MagicMock(), "m", tools=[], **kwargs)

    def test_routing_can_be_switched_off(self):
        assert self._agent(route_tools=False)._route_tools is False

    def test_routing_is_on_by_default(self):
        assert self._agent(route_tools=True)._route_tools is True

    def _run_and_capture_tools(self, text: str, *, route: bool):
        """Run one turn and return the tool payload the engine actually got."""
        engine = MagicMock()
        engine.generate.return_value = {
            "content": "done",
            "usage": {},
            "finish_reason": "stop",
        }
        agent = self._agent(route_tools=route)
        agent._engine = engine
        # Non-empty _tools is what gates the payload; the schemas themselves
        # come from the executor.
        agent._tools = [object()]
        agent._executor.get_openai_tools = MagicMock(return_value=ALL_SCHEMAS)

        agent.run(text)
        sent = engine.generate.call_args.kwargs.get("tools", [])
        return [s["function"]["name"] for s in sent]

    def test_a_conversational_turn_sends_a_trimmed_payload(self):
        names = self._run_and_capture_tools("who are you?", route=True)
        assert "spotify_control" not in names
        assert "git_commit" not in names
        assert "retrieval" in names

    def test_an_action_request_still_sends_its_tool(self):
        names = self._run_and_capture_tools("play a song", route=True)
        assert "spotify_control" in names

    def test_switching_routing_off_sends_everything(self):
        names = self._run_and_capture_tools("who are you?", route=False)
        assert set(names) == set(ALL_NAMES)
