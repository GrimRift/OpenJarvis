"""One answer to "which speech providers could run right now, and why not".

Settings, the Health page and the ``system_health`` tool all ask this; a
single place keeps them from disagreeing. Capability only, never choice:
whether a provider *could* serve. Gating on the browser's own selection
would make the Settings control impossible to switch on.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _entry(available: bool, reason: str = "", **extra: Any) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"available": available}
    if reason:
        entry["reason"] = reason
    entry.update(extra)
    return entry


def flux_status(speech_cfg: Any) -> Dict[str, Any]:
    try:
        from openjarvis.server.flux_routes import _unavailable_reason
        from openjarvis.speech import flux
    except Exception:
        return _entry(False, "Flux support is not installed")
    available = bool(flux.is_available() and getattr(speech_cfg, "flux_enabled", True))
    return _entry(available, "" if available else _unavailable_reason(speech_cfg))


def parakeet_status(speech_cfg: Any) -> Dict[str, Any]:
    try:
        from openjarvis.speech import parakeet
    except Exception as exc:  # pragma: no cover - import guard
        return _entry(False, f"Parakeet support is not installed: {exc}")
    if not getattr(speech_cfg, "parakeet_enabled", True):
        return _entry(
            False, "Parakeet is disabled on the server ([speech] parakeet_enabled)"
        )
    model_dir = parakeet.weights.model_dir(
        getattr(speech_cfg, "parakeet_model_dir", "")
    )
    quant = str(getattr(speech_cfg, "parakeet_quant", "") or "")
    reason = parakeet.unavailable_reason(model_dir=model_dir, quant=quant)
    engine = parakeet.loaded_engine()
    return _entry(
        reason is None,
        reason or "",
        device=engine.device if engine else None,
        requested_device=str(getattr(speech_cfg, "parakeet_device", "cuda")),
        loaded=engine is not None,
        model_dir=model_dir,
    )


def cartesia_status(speech_cfg: Any) -> Dict[str, Any]:
    if not os.environ.get("CARTESIA_API_KEY"):
        return _entry(False, "CARTESIA_API_KEY is not configured on the server")
    return _entry(True)


def chatterbox_status(speech_cfg: Any) -> Dict[str, Any]:
    try:
        from openjarvis.speech import chatterbox_tts
    except Exception as exc:  # pragma: no cover - import guard
        return _entry(False, f"Chatterbox support is not installed: {exc}")
    try:
        return chatterbox_tts.status(speech_cfg)
    except Exception as exc:
        logger.debug("Chatterbox status failed", exc_info=True)
        return _entry(False, f"Chatterbox status failed: {exc}")


def speech_providers(speech_cfg: Any) -> Dict[str, Dict[str, Dict[str, Any]]]:
    return {
        "stt": {
            "flux": flux_status(speech_cfg),
            "parakeet": parakeet_status(speech_cfg),
        },
        "tts": {
            "cartesia": cartesia_status(speech_cfg),
            "chatterbox": chatterbox_status(speech_cfg),
        },
    }


def default_tts_provider(speech_cfg: Any) -> str:
    value: Optional[str] = getattr(speech_cfg, "tts_provider", None)
    return (value or "cartesia").strip().lower()


def server_tts_backend(speech_cfg: Any):
    """The backend instance for server-side speech (moments, Waze, the
    digest): the configured provider, from the registry. Every such caller
    used to construct CartesiaTTSBackend directly, which is how a chosen
    local voice would have been silently ignored everywhere but the chat."""
    import openjarvis.speech  # noqa: F401 - registers the backends
    from openjarvis.core.registry import TTSRegistry

    key = default_tts_provider(speech_cfg)
    if not TTSRegistry.contains(key):
        key = "cartesia"
    backend_cls = TTSRegistry.get(key)
    if key == "chatterbox":
        return backend_cls(speech_cfg)
    return backend_cls()


def server_voice_id(speech_cfg: Any) -> str:
    """The voice server-side speech uses for the configured provider."""
    if default_tts_provider(speech_cfg) == "chatterbox":
        name = str(getattr(speech_cfg, "chatterbox_voice", "jarvis") or "jarvis")
        return f"chatterbox:{name}"
    return str(getattr(speech_cfg, "voice_id", "") or "")


__all__ = [
    "server_tts_backend",
    "server_voice_id",
    "cartesia_status",
    "chatterbox_status",
    "default_tts_provider",
    "flux_status",
    "parakeet_status",
    "speech_providers",
]
