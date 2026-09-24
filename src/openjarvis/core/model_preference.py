"""The user's "Prefer cloud model" choice, where the server can see it.

The switch lives in the browser's settings, which the server never saw, so
work the server starts on its own -- a scheduled task, a reminder that looks
something up -- ran on the configured default, the local qwen3.5:4b. One
reminder at 08:00 on 24 September loaded 3.6 GB onto an 8 GB card already
holding the voice engine, and the whole laptop lagged. The browser now
reports the choice here, and background work asks what to run on.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from openjarvis.core.config import DEFAULT_CONFIG_DIR

logger = logging.getLogger(__name__)

_FILE = "model_preference.json"

#: Used until the browser has said otherwise: the default in its settings.
DEFAULT_CLOUD_MODEL = "gpt-5.6-luna"


@dataclass
class ModelPreference:
    prefer_cloud: bool = True
    cloud_model: str = DEFAULT_CLOUD_MODEL


def preference_path(config_dir: Optional[Path] = None) -> Path:
    return (config_dir or DEFAULT_CONFIG_DIR) / _FILE


def load_preference(config_dir: Optional[Path] = None) -> ModelPreference:
    try:
        raw = json.loads(preference_path(config_dir).read_text(encoding="utf-8"))
    except Exception:
        return ModelPreference()
    pref = ModelPreference()
    if isinstance(raw, dict):
        if isinstance(raw.get("prefer_cloud"), bool):
            pref.prefer_cloud = raw["prefer_cloud"]
        model = raw.get("cloud_model")
        if isinstance(model, str) and model.strip():
            pref.cloud_model = model.strip()
    return pref


def save_preference(pref: ModelPreference, config_dir: Optional[Path] = None) -> None:
    path = preference_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(pref), indent=2), encoding="utf-8")


def background_model(config_dir: Optional[Path] = None) -> Optional[str]:
    """The model background work should run on, or None for the default.

    The cloud model when the user prefers it and its provider is configured;
    otherwise None, and the configured (local) default is used.
    """
    pref = load_preference(config_dir)
    if not pref.prefer_cloud:
        return None
    try:
        from openjarvis.engine.cloud import CloudEngine

        if pref.cloud_model in CloudEngine().list_models():
            return pref.cloud_model
    except Exception:  # noqa: BLE001 -- fall back to the default, never fail a task
        logger.debug("Cloud availability check failed", exc_info=True)
    return None


def unload_local_model(model: str, host: str = "http://127.0.0.1:11434") -> None:
    """Ask Ollama to drop *model* from memory now, not five minutes from now.

    Its keep-alive held 3.6 GB of VRAM for five minutes after a background
    task that took seconds. Best-effort: a model already gone, or no Ollama,
    is not an error.
    """
    if not model:
        return
    try:
        import httpx

        httpx.post(
            f"{host}/api/generate",
            json={"model": model, "keep_alive": 0},
            timeout=5.0,
        )
    except Exception:  # noqa: BLE001
        logger.debug("Could not unload %s", model, exc_info=True)


__all__ = [
    "ModelPreference",
    "background_model",
    "load_preference",
    "save_preference",
    "unload_local_model",
]
