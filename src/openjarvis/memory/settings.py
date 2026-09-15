"""Memory settings the user can change without editing config.toml (M38).

A sidecar like ``presence.json``: the server has no safe way to rewrite the
hand-commented TOML, and a small JSON file is re-read whenever it matters.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from openjarvis.core.config import DEFAULT_CONFIG_DIR

_FILE = "memory_settings.json"
EXTRACTION_MODES = ("cloud", "local")


@dataclass
class MemorySettings:
    # Which model extracts facts from each exchange. "cloud" uses the model
    # below; "local" uses [memory] extraction_model from config.toml. The
    # 4b local model is where the stale and duplicate facts came from, so
    # cloud is the default; local stays one switch away.
    extraction_mode: str = "cloud"
    cloud_model: str = "gpt-5.6-luna"
    # The nightly clean-up (dedupe, contradictions, expiry).
    hygiene_enabled: bool = True
    hygiene_hour_local: int = 23
    # How long a removed fact stays restorable.
    restore_window_days: int = 7

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def settings_path(config_dir: Optional[Path] = None) -> Path:
    return (config_dir or DEFAULT_CONFIG_DIR) / _FILE


def load_memory_settings(config_dir: Optional[Path] = None) -> MemorySettings:
    try:
        raw = json.loads(settings_path(config_dir).read_text(encoding="utf-8"))
    except Exception:
        return MemorySettings()
    if not isinstance(raw, dict):
        return MemorySettings()
    settings = MemorySettings()
    if raw.get("extraction_mode") in EXTRACTION_MODES:
        settings.extraction_mode = raw["extraction_mode"]
    if isinstance(raw.get("cloud_model"), str) and raw["cloud_model"].strip():
        settings.cloud_model = raw["cloud_model"].strip()
    if isinstance(raw.get("hygiene_enabled"), bool):
        settings.hygiene_enabled = raw["hygiene_enabled"]
    hour = raw.get("hygiene_hour_local")
    if isinstance(hour, int) and not isinstance(hour, bool) and 0 <= hour <= 23:
        settings.hygiene_hour_local = hour
    days = raw.get("restore_window_days")
    if isinstance(days, int) and not isinstance(days, bool) and 1 <= days <= 90:
        settings.restore_window_days = days
    return settings


def save_memory_settings(
    settings: MemorySettings, config_dir: Optional[Path] = None
) -> None:
    path = settings_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings.to_dict(), indent=2), encoding="utf-8")


__all__ = [
    "EXTRACTION_MODES",
    "MemorySettings",
    "load_memory_settings",
    "save_memory_settings",
]
