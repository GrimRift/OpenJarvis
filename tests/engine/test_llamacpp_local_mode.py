"""llama.cpp as Sage's local engine, and local mode ("Prefer cloud model" off)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx

from openjarvis.core import model_preference
from openjarvis.core.model_preference import ModelPreference, save_preference
from openjarvis.core.types import Message, Role
from openjarvis.engine.openai_compat_engines import LlamaCppEngine

HOST = "http://testhost:8080"


def _reply() -> dict:
    return {
        "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        "model": "qwen3.5-9b",
    }


def _sent_body(respx_mock, **kwargs) -> dict:
    sent = {}

    def handler(request):
        sent.update(json.loads(request.content))
        return httpx.Response(200, json=_reply())

    respx_mock.post(f"{HOST}/v1/chat/completions").mock(side_effect=handler)
    LlamaCppEngine(host=HOST).generate(
        [Message(role=Role.USER, content="hello")], model="qwen3.5-9b", **kwargs
    )
    return sent


class TestLlamaCppRequestBody:
    def test_thinking_is_off_unless_asked(self, respx_mock):
        """Qwen3.5's template thinks by default; nothing but this turns it off."""
        body = _sent_body(respx_mock)
        assert body["chat_template_kwargs"] == {"enable_thinking": False}

    def test_think_true_turns_it_on(self, respx_mock):
        body = _sent_body(respx_mock, think=True)
        assert body["chat_template_kwargs"] == {"enable_thinking": True}
        assert "think" not in body

    def test_ollama_only_options_are_not_sent(self, respx_mock):
        body = _sent_body(respx_mock, keep_alive=0, num_ctx=16384)
        assert "keep_alive" not in body
        assert "num_ctx" not in body


def _prefer(tmp_path, monkeypatch, prefer_cloud: bool, local_model: str = "") -> None:
    monkeypatch.setattr(model_preference, "DEFAULT_CONFIG_DIR", tmp_path)
    save_preference(
        ModelPreference(prefer_cloud=prefer_cloud, local_model=local_model), tmp_path
    )


class TestLocalize:
    def test_prefer_cloud_changes_nothing(self, tmp_path, monkeypatch):
        _prefer(tmp_path, monkeypatch, True, "qwen3.5-9b")
        assert model_preference.localize("gpt-6-luna", "cloud") == (
            "gpt-6-luna",
            "cloud",
        )

    def test_local_mode_swaps_a_cloud_model_and_engine(self, tmp_path, monkeypatch):
        _prefer(tmp_path, monkeypatch, False, "qwen3.5-9b")
        assert model_preference.localize("gpt-6-luna", "cloud") == ("qwen3.5-9b", "")
        assert model_preference.localize("gpt-6-luna") == ("qwen3.5-9b", "")

    def test_local_mode_keeps_a_local_model(self, tmp_path, monkeypatch):
        _prefer(tmp_path, monkeypatch, False, "qwen3.5-9b")
        assert model_preference.localize("qwen3.6-35b-a3b") == ("qwen3.6-35b-a3b", "")

    def test_local_model_falls_back_to_the_configured_default(
        self, tmp_path, monkeypatch
    ):
        _prefer(tmp_path, monkeypatch, False, "")
        config = SimpleNamespace(
            intelligence=SimpleNamespace(default_model="qwen3.5-4b")
        )
        monkeypatch.setattr(
            "openjarvis.core.config.load_config", lambda *a, **k: config
        )
        assert model_preference.localize("gpt-6-luna")[0] == "qwen3.5-4b"


class TestLocalModeReachesBackgroundWork:
    def test_memory_extraction_uses_the_local_model(self, tmp_path, monkeypatch):
        """The memory setting says cloud; local mode still keeps it local."""
        from openjarvis.memory import settings as memory_settings
        from openjarvis.memory.extractor import FactExtractor

        _prefer(tmp_path, monkeypatch, False, "qwen3.5-9b")
        monkeypatch.setattr(
            memory_settings,
            "load_memory_settings",
            lambda: SimpleNamespace(extraction_mode="cloud", cloud_model="gpt-6-luna"),
        )
        extractor = FactExtractor.__new__(FactExtractor)
        extractor._model = "qwen3.5-4b"
        assert extractor._resolve_model("gpt-6-luna") == "qwen3.5-9b"

    def test_pictures_go_to_the_local_model(self, tmp_path, monkeypatch):
        from openjarvis.vision.ask import resolve_vision_model

        _prefer(tmp_path, monkeypatch, False, "qwen3.5-9b")
        config = SimpleNamespace(
            vision=SimpleNamespace(model="", local_model="qwen3-vl:8b"),
            intelligence=SimpleNamespace(default_model="gpt-6-luna"),
        )
        assert resolve_vision_model(config) == "qwen3.5-9b"

    def test_a_cloud_pinned_agent_runs_locally(self, tmp_path, monkeypatch):
        from openjarvis.agents._model_override import apply_configured_model

        _prefer(tmp_path, monkeypatch, False, "qwen3.5-9b")
        engine = object()
        args, _ = apply_configured_model(
            (engine, "qwen3.5-4b"), {}, "gpt-6-luna", "cloud", label="digest"
        )
        assert args == (engine, "qwen3.5-9b")
