"""Where generated images live: a PNG in the user's folder, a row in an index.

The file is the user's copy (``Pictures\\Sage\\2026-09-29_cafe-at-sunset.png``);
the index is how Sage finds it again. A reply's tool result carries only the
image id, so "make it brighter" after a reload resolves the id here, and the
app fetches the picture over ``GET /v1/images/{id}`` -- image data never goes
through a tool result, which the chat replays cut to 500 characters.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from openjarvis.core.paths import get_config_dir

_SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id          TEXT PRIMARY KEY,
    created     REAL NOT NULL,
    path        TEXT NOT NULL,
    prompt      TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'generate',
    model       TEXT NOT NULL DEFAULT '',
    quality     TEXT NOT NULL DEFAULT '',
    size        TEXT NOT NULL DEFAULT '',
    cost_usd    REAL,
    parent_id   TEXT
)
"""

#: Words that say nothing about the picture, left out of the file name.
_SLUG_STOP = {
    "a",
    "an",
    "the",
    "of",
    "and",
    "with",
    "in",
    "on",
    "at",
    "to",
    "for",
    "image",
    "picture",
    "photo",
    "please",
    "make",
    "me",
    "draw",
    "create",
    "generate",
    "show",
    "my",
    "it",
    "is",
    "that",
    "this",
}


def slug(prompt: str, max_words: int = 5, max_len: int = 40) -> str:
    """A short, file-safe name from a prompt: "cafe-at-sunset"."""
    text = unicodedata.normalize("NFKD", prompt or "")
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    words = [w for w in re.findall(r"[a-z0-9]+", text) if w not in _SLUG_STOP]
    name = "-".join(words[:max_words])[:max_len].strip("-")
    return name or "image"


def unique_path(folder: Path, day: str, name: str) -> Path:
    """``folder/day_name.png``, or ``_name-2``, ``-3``... if taken. Never overwrites."""
    candidate = folder / f"{day}_{name}.png"
    n = 2
    while candidate.exists():
        candidate = folder / f"{day}_{name}-{n}.png"
        n += 1
    return candidate


@dataclass
class ImageRecord:
    id: str
    created: float
    path: str
    prompt: str
    kind: str
    model: str
    quality: str
    size: str
    cost_usd: Optional[float]
    parent_id: Optional[str]


class ImageStore:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = str(db_path or (get_config_dir() / "images" / "index.db"))
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path, timeout=10)

    def save(
        self,
        png: bytes,
        *,
        folder: Path,
        prompt: str,
        kind: str,
        model: str,
        quality: str,
        size: str,
        cost_usd: Optional[float],
        parent_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> ImageRecord:
        """Write the PNG to *folder* and index it. Returns the new record."""
        moment = now or datetime.now()
        folder.mkdir(parents=True, exist_ok=True)
        with self._lock:
            path = unique_path(folder, moment.strftime("%Y-%m-%d"), slug(prompt))
            # "x" mode: a file appearing between the check and the write is
            # an error, not an overwrite.
            with open(path, "xb") as handle:
                handle.write(png)
            record = ImageRecord(
                id=f"img_{uuid.uuid4().hex[:12]}",
                created=moment.timestamp() if now else time.time(),
                path=str(path),
                prompt=prompt,
                kind=kind,
                model=model,
                quality=quality,
                size=size,
                cost_usd=cost_usd,
                parent_id=parent_id,
            )
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO images VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        record.id,
                        record.created,
                        record.path,
                        record.prompt,
                        record.kind,
                        record.model,
                        record.quality,
                        record.size,
                        record.cost_usd,
                        record.parent_id,
                    ),
                )
        return record

    def get(self, image_id: str) -> Optional[ImageRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM images WHERE id = ?", ((image_id or "").strip(),)
            ).fetchone()
        return ImageRecord(*row) if row else None

    def latest(self) -> Optional[ImageRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM images ORDER BY created DESC LIMIT 1"
            ).fetchone()
        return ImageRecord(*row) if row else None
