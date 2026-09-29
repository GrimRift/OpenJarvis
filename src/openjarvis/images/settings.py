"""Settings > Images, kept on the server.

The tools run server-side, and a Telegram or voice turn never passes through
the browser, so the model, quality and save folder cannot ride the chat
request the way the diagram mode does. "Open automatically" is the exception:
it only matters where an image is displayed, so the app keeps that one.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Dict

from openjarvis.core.paths import get_config_dir

#: Chosen by the user from the 2026-09-29 benchmark (notes/m40-benchmark).
DEFAULT_MODEL = "gpt-image-2.5-flare"
DEFAULT_QUALITY = "medium"
DEFAULT_SIZE = "1024x1024"

MODELS = ("gpt-image-2.5-flare", "gpt-image-2.5-sunburst", "gpt-image-2")
QUALITIES = ("low", "medium", "high", "auto")
SIZES = ("1024x1024", "1536x1024", "1024x1536", "auto")

_lock = threading.Lock()


def default_save_dir() -> str:
    # The literal path the user chose, not the Windows known folder: on this
    # machine Pictures is redirected into OneDrive, and they asked for a
    # local, unsynced folder.
    return str(Path.home() / "Pictures" / "Sage")


@dataclass
class ImageSettings:
    enabled: bool = True
    model: str = DEFAULT_MODEL
    quality: str = DEFAULT_QUALITY
    size: str = DEFAULT_SIZE
    save_dir: str = ""

    def resolved_save_dir(self) -> Path:
        return Path(self.save_dir or default_save_dir()).expanduser()

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["save_dir"] = str(self.resolved_save_dir())
        return data


def settings_path() -> Path:
    return get_config_dir() / "image_settings.json"


def _validated(data: Dict[str, Any], base: ImageSettings) -> ImageSettings:
    """Apply *data* over *base*, rejecting values the API would refuse."""
    known = {f.name for f in fields(ImageSettings)}
    merged = asdict(base)
    for key, value in (data or {}).items():
        if key not in known:
            continue
        merged[key] = value
    result = ImageSettings(**merged)
    result.enabled = bool(result.enabled)
    if result.model not in MODELS:
        raise ValueError(f"Unknown image model {result.model!r}")
    if result.quality not in QUALITIES:
        raise ValueError(f"Unknown quality {result.quality!r}")
    if result.size not in SIZES:
        raise ValueError(f"Unknown size {result.size!r}")
    result.save_dir = str(result.save_dir or "").strip()
    return result


def load(path: Path | None = None) -> ImageSettings:
    """The saved settings, or the defaults when there are none.

    A file that cannot be read falls back to the defaults as a whole rather
    than half-applying: a half-valid file is more confusing than none.
    """
    target = path or settings_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ImageSettings()
    except (OSError, ValueError):
        return ImageSettings()
    try:
        return _validated(raw if isinstance(raw, dict) else {}, ImageSettings())
    except ValueError:
        return ImageSettings()


def update(changes: Dict[str, Any], path: Path | None = None) -> ImageSettings:
    """Merge *changes* into the saved settings and write them. Raises ValueError."""
    target = path or settings_path()
    with _lock:
        result = _validated(changes, load(target))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
        return result
