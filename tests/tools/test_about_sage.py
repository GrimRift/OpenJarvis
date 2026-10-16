"""about_sage: Sage reading its own tools, settings, guide, history and code."""

from __future__ import annotations

import pytest

from openjarvis.agents.tool_routing import selected_tool_names
from openjarvis.tools import about_sage as mod
from openjarvis.tools.about_sage import AboutSageTool


@pytest.fixture(autouse=True)
def _fresh_code_budget(monkeypatch):
    monkeypatch.setattr(mod, "_code_calls", [])


@pytest.fixture
def data_dir(tmp_path):
    (tmp_path / "config.toml").write_text(
        '[engine]\ndefault = "llamacpp"\n'
        '[intelligence]\ndefault_model = "qwen3.5-9b"\n'
        '[agent]\ntools = "calculator,about_sage,not_a_tool"\n'
        'system_prompt = "long prompt"\n'
        '[channel.telegram]\nbot_token = "SECRET-TOKEN"\nenabled = true\n',
        encoding="utf-8",
    )
    return tmp_path


def test_app_settings_drop_secrets_and_non_scalars(data_dir):
    count = mod.save_app_settings(
        {
            "apiKey": "sk-123",
            "apiUrl": "http://x",
            "bargeDuckLevel": 0.15,
            "ttsVoiceId": "jarvis",
            "nested": {"a": 1},
        },
        data_dir,
    )
    saved = mod.load_app_settings(data_dir)["settings"]
    assert count == 2
    assert saved == {"bargeDuckLevel": 0.15, "ttsVoiceId": "jarvis"}


def test_settings_hide_secret_keys_and_find_the_dip_by_its_name(data_dir):
    mod.save_app_settings({"bargeDuckLevel": 0.15, "theme": "dark"}, data_dir)
    everything = mod.settings("", data_dir)
    assert "SECRET-TOKEN" not in everything
    assert "channel.telegram.bot_token = (set, hidden)" in everything
    assert "long prompt" not in everything
    dip = mod.settings("what's my dip set to", data_dir)
    assert "bargeDuckLevel = 0.15" in dip
    assert "theme" not in dip


def test_settings_say_when_the_app_never_reported(data_dir):
    assert "has not reported" in mod.settings("", data_dir)


def test_abilities_list_enabled_registered_tools_with_summaries(data_dir):
    from openjarvis.core.registry import ToolRegistry
    from openjarvis.tools.calculator import CalculatorTool

    ToolRegistry.register_value("about_sage", AboutSageTool)
    ToolRegistry.register_value("calculator", CalculatorTool)
    text = mod.abilities("", data_dir)
    assert "- about_sage: Facts about Sage ITSELF" in text
    assert "- calculator:" in text
    assert "not_a_tool" not in text
    assert "switched off" in text
    assert "calculator" not in mod.abilities("spotify", data_dir).split("\n", 1)[-1]


def test_how_to_finds_the_rewind_section():
    text = mod.how_to("how do I rewind a chat")
    assert text.startswith("## Chat")
    assert "rewind button" in text


def test_whats_new_reads_git_history():
    text = mod.whats_new(3650)
    assert text.startswith("Changes in the last 90 days") or "No changes" in text


def test_code_search_and_read_stay_inside_tracked_source():
    files = mod.source_files()
    assert files and all(f.startswith(mod.SOURCE_ROOTS) for f in files)
    assert not any(f.startswith("src/openjarvis/server/static/") for f in files)
    hits = mod.code("def route_tools")
    assert "src/openjarvis/agents/tool_routing.py:" in hits
    read = mod.code(path="agents/tool_routing.py", start_line=1)
    assert read.startswith("src/openjarvis/agents/tool_routing.py (lines 1-")
    assert "not one of Sage's readable source files" in mod.code(path="../.env")
    assert "not one of" in mod.code(path="C:/AI/OpenJarvis-Data/config.toml")


def test_code_folder_path_narrows_the_search_and_long_queries_rank_words():
    narrowed = mod.code("def route_tools", path="agents/")
    assert "src/openjarvis/agents/tool_routing.py:" in narrowed
    assert "frontend/" not in narrowed
    listing = mod.code(path="agents")
    assert listing.startswith("Files under 'agents':")
    ranked = mod.code("narrow groups routing pattern tokens")
    assert "Matches for" in ranked


def test_code_reads_stop_after_the_budget(monkeypatch):
    monkeypatch.setattr(mod, "_code_calls", [])
    for _ in range(mod.CODE_BUDGET):
        assert not mod.code("def route_tools").startswith("Enough code")
    assert mod.code("def route_tools").startswith("Enough code read")


def test_tool_dispatches_parts():
    result = AboutSageTool().execute(part="how_to", query="microphone")
    assert result.success and "Wake word microphone" in result.content
    assert AboutSageTool().execute(part="nonsense").metadata["part"] == "overview"


@pytest.mark.parametrize(
    "message",
    [
        "what can you do",
        "what's my dip set to?",
        "which model are you running",
        "what changed this week in sage",
        "how does your wake word work",
        "tell me about yourself",
        "how do I rewind a chat?",
        "which mic do you use for the wake word?",
        # 10-16: answered "I can't see a change log", without the tool.
        "Uh, can you look at the latest ones that we added or fixed about you?",
        "what did we fix today?",
        "any recent updates to you?",
    ],
)
def test_self_questions_route_the_tool(message):
    assert "about_sage" in selected_tool_names(message)


def test_a_follow_up_keeps_the_tool():
    from openjarvis.agents.tool_routing import TURN_SEPARATOR

    turns = TURN_SEPARATOR.join(
        [
            "Uh, can you look at the latest ones that we added or fixed about you?",
            "I'm asking about that.",
        ]
    )
    assert "about_sage" in selected_tool_names(turns)


@pytest.mark.parametrize(
    "message", ["can you open spotify", "what's the weather tomorrow", "who won F1"]
)
def test_plain_requests_do_not_route_the_tool(message):
    assert "about_sage" not in selected_tool_names(message)
