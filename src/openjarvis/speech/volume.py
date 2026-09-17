"""How loud Sage is, per channel, shared by every tab and the server.

A master level scales five channels: chat replies (the browser's TTS),
the wake-word acknowledgement and "one moment" clips (browser, pre-
rendered), moments Sage speaks first (server, ffplay), reminders
(server, PowerShell player) and the chime. Effective level is master ×
channel, 0-1. Nothing is skipped at zero: a gain of 0 changes only the
level, so every path stays identical and nothing records as spoken that
was not attempted.

Kept in ``volume.json`` under the data directory, read at each play, so a
change on the Settings page reaches the next sound without a restart.

At 100% every player ran at unity, so the sliders could only make Sage
quieter than the files were rendered -- and the user found 100% not loud
enough on 17 September. ``gain()`` is the level with ``BOOST`` on top,
for players that can amplify (Web Audio, ffplay's volume filter, the
reminder samples); ``level()`` stays 0-1 for those that cannot (SAPI).
Anything that amplifies must limit, so a loud file does not distort.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Dict, Optional

from openjarvis.core.config import DEFAULT_CONFIG_DIR

logger = logging.getLogger(__name__)

CHANNELS = ("chat", "ack", "moments", "reminders", "chime")

#: 100% plays this much louder than the file. Mirrored in the browser
#: (``lib/volume.ts``); change both together.
BOOST = 1.2


@dataclass
class Volumes:
    master: float = 1.0
    chat: float = 1.0
    ack: float = 1.0
    moments: float = 1.0
    reminders: float = 1.0
    chime: float = 1.0

    def effective(self, channel: str) -> float:
        """master × channel, clamped to 0-1."""
        level = getattr(self, channel, 1.0)
        return max(0.0, min(1.0, float(self.master) * float(level)))

    def to_dict(self) -> Dict[str, float]:
        return {k: float(v) for k, v in asdict(self).items()}


def _clamp(value: Any, fallback: float = 1.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return fallback


def volume_path(config_dir: Optional[Path] = None) -> Path:
    return (config_dir or DEFAULT_CONFIG_DIR) / "volume.json"


def load_volumes(config_dir: Optional[Path] = None) -> Volumes:
    try:
        data = json.loads(volume_path(config_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Volumes()
    if not isinstance(data, dict):
        return Volumes()
    return Volumes(**{f.name: _clamp(data.get(f.name, 1.0)) for f in fields(Volumes)})


def save_volumes(volumes: Volumes, config_dir: Optional[Path] = None) -> None:
    path = volume_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(volumes.to_dict(), indent=2), encoding="utf-8")


def level(channel: str, config_dir: Optional[Path] = None) -> float:
    """The effective 0-1 level for *channel* right now."""
    return load_volumes(config_dir).effective(channel)


def gain(channel: str, config_dir: Optional[Path] = None) -> float:
    """The level with the boost: 0-``BOOST``, for a player that can amplify."""
    return level(channel, config_dir) * BOOST


__all__ = [
    "BOOST",
    "CHANNELS",
    "Volumes",
    "gain",
    "level",
    "load_volumes",
    "save_volumes",
]
