"""The voice the user chose in Settings, as the server sees it.

The browser keeps its settings in localStorage, which server-side speech
(moments Sage starts, reminders, the digest) cannot read. So the Settings
page also writes the choice here -- ``voice_choice.json`` beside
``volume.json`` -- and every server-side voice reads it at the moment it
speaks. Nothing in the file is ever needed for a reply to the chat: that
path carries the choice with each request.

Precedence: this file, then ``[speech] tts_provider`` / ``chatterbox_voice``
/ ``voice_id`` in config.toml, which remain the defaults for a server no
browser has ever configured.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from openjarvis.core.paths import get_config_dir

logger = logging.getLogger(__name__)

PROVIDERS = ("cartesia", "chatterbox")


@dataclass
class VoiceChoice:
    tts_provider: str = ""
    voice_id: str = ""

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


def choice_path(config_dir: Optional[Path] = None) -> Path:
    # get_config_dir honours OPENJARVIS_HOME, so tests never read this
    # machine's real choice.
    return (config_dir or get_config_dir()) / "voice_choice.json"


def load_choice(config_dir: Optional[Path] = None) -> VoiceChoice:
    return _load_choice_from(config_dir)


def _load_choice_from(config_dir: Optional[Path]) -> VoiceChoice:
    try:
        data = json.loads(choice_path(config_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return VoiceChoice()
    if not isinstance(data, dict):
        return VoiceChoice()
    provider = str(data.get("tts_provider") or "").strip().lower()
    return VoiceChoice(
        tts_provider=provider if provider in PROVIDERS else "",
        voice_id=str(data.get("voice_id") or "").strip(),
    )


def save_choice(choice: VoiceChoice, config_dir: Optional[Path] = None) -> None:
    path = choice_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(choice.to_dict(), indent=2), encoding="utf-8")


def chosen_provider(speech_cfg: Any, config_dir: Optional[Path] = None) -> str:
    chosen = load_choice(config_dir).tts_provider
    if chosen:
        return chosen
    value = str(getattr(speech_cfg, "tts_provider", "") or "cartesia").strip().lower()
    return value if value in PROVIDERS else "cartesia"


def chosen_voice_id(speech_cfg: Any, config_dir: Optional[Path] = None) -> str:
    """The voice for the chosen provider; a stored id from the other
    provider is ignored, as the browser does."""
    choice = load_choice(config_dir)
    provider = chosen_provider(speech_cfg, config_dir)
    if choice.voice_id:
        is_local = choice.voice_id.startswith("chatterbox:")
        if is_local == (provider == "chatterbox"):
            return choice.voice_id
    if provider == "chatterbox":
        name = str(getattr(speech_cfg, "chatterbox_voice", "jarvis") or "jarvis")
        return f"chatterbox:{name}"
    return str(getattr(speech_cfg, "voice_id", "") or "")


__all__ = [
    "PROVIDERS",
    "VoiceChoice",
    "choice_path",
    "chosen_provider",
    "chosen_voice_id",
    "load_choice",
    "save_choice",
]
