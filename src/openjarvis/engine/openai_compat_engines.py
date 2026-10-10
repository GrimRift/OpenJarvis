"""Data-driven registration of OpenAI-compatible inference engines."""

from __future__ import annotations

import logging
from typing import Any, Dict

from openjarvis.core.registry import EngineRegistry
from openjarvis.engine._openai_compat import _OpenAICompatibleEngine

logger = logging.getLogger(__name__)

#: Ollama-only options. Callers written against Ollama pass them on every
#: call; llama.cpp sizes its context per model in the router's presets.
_OLLAMA_ONLY_KWARGS = frozenset({"keep_alive", "num_ctx", "think"})


@EngineRegistry.register("llamacpp")
class LlamaCppEngine(_OpenAICompatibleEngine):
    """llama.cpp's ``llama-server``, usually in router mode (one process that
    loads the model a request names, from a presets file)."""

    engine_id = "llamacpp"
    _default_host = "http://localhost:8080"
    _api_prefix = "/v1"

    def _request_kwargs(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Thinking off unless the caller asks, as the Ollama engine does.

        Qwen3.5's chat template thinks by default, and on llama.cpp nothing
        else turns it off: the thinking tokens eat the whole budget and the
        visible reply arrives late or empty. Ollama callers say ``think``;
        here that becomes the template's ``enable_thinking``.
        """
        body = {k: v for k, v in kwargs.items() if k not in _OLLAMA_ONLY_KWARGS}
        template_kwargs = dict(body.get("chat_template_kwargs") or {})
        template_kwargs.setdefault("enable_thinking", bool(kwargs.get("think")))
        body["chat_template_kwargs"] = template_kwargs
        return body

    def load_model(self, model: str) -> bool:
        """Ask the router to load *model* now; see ``llamacpp_server``."""
        from openjarvis.engine.llamacpp_server import load_model

        return load_model(self._host, model)


_ENGINES = {
    "vllm": ("VLLMEngine", "http://localhost:8000", "/v1"),
    "sglang": ("SGLangEngine", "http://localhost:30000", "/v1"),
    "mlx": ("MLXEngine", "http://localhost:8080", "/v1"),
    "lmstudio": ("LMStudioEngine", "http://localhost:1234", "/v1"),
    "exo": ("ExoEngine", "http://localhost:52415", "/v1"),
    "nexa": ("NexaEngine", "http://localhost:18181", "/v1"),
    "uzu": ("UzuEngine", "http://localhost:8000", ""),
    "apple_fm": ("AppleFmEngine", "http://localhost:8079", "/v1"),
    "lemonade": ("LemonadeEngine", "http://localhost:13305", "/v1"),
}

for _key, (_cls_name, _default_host, _api_prefix) in _ENGINES.items():
    _cls = type(
        _cls_name,
        (_OpenAICompatibleEngine,),
        {"engine_id": _key, "_default_host": _default_host, "_api_prefix": _api_prefix},
    )
    EngineRegistry.register(_key)(_cls)
    globals()[_cls_name] = _cls


def normalize_openai_base_url(url: str) -> str:
    """Strip a single trailing ``/v1`` segment from a user-supplied base URL.

    Users habitually pass ``http://host:8000/v1`` (the full OpenAI-compatible
    prefix); the engine's ``_api_prefix`` re-appends ``/v1`` to every request
    path, so a trailing copy would double up as ``/v1/v1``. Only a literal
    trailing ``/v1`` is stripped — proxy/gateway path prefixes are preserved.
    """
    base = url.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base


class OpenAICompatEngine(_OpenAICompatibleEngine):
    """Generic engine for an explicitly-provided OpenAI-compatible endpoint.

    Deliberately NOT registered in ``EngineRegistry``: it is only ever
    constructed with an explicit host (e.g. ``jarvis eval --base-url``), so
    registering it would just add a useless localhost discovery probe and
    interact with the per-test registry wipe.
    """

    engine_id = "openai-compat"
    _api_prefix = "/v1"


__all__ = [name for name, _, _ in _ENGINES.values()] + [
    "LlamaCppEngine",
    "OpenAICompatEngine",
    "normalize_openai_base_url",
]
