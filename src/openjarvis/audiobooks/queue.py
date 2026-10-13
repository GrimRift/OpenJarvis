"""The render queue: what to narrate, in what order. One file, three modes.

``<data>/books/queue.json`` holds jobs in the order they were asked for:

* ``book``    -- every chapter not flagged skip;
* ``range``   -- the chapters asked for ("chapters 40-60");
* ``rolling`` -- the next ``ahead`` chapters after ``position`` (where the
  listener is); it stays open and refills when the position moves.

The worker takes the first chapter of the first job that has no audio yet
for the job's voice; a finished ``book``/``range`` job is marked done. The
file is shared by the server's worker and the CLI, so every change is a
read-modify-write under a lock file.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from openjarvis.audiobooks.store import BookStore

#: The narrator the user picked on 2026-10-13; any local voice can be set per book.
DEFAULT_VOICE = "lotm-narrator"
MODES = ("book", "range", "rolling")
#: Share of the time the book voice may work (user, 2026-10-13: full speed
#: heated the PC and made it sluggish; "2/3 or 75%"). After a piece that took
#: g seconds it rests g * (1/speed - 1).
DEFAULT_SPEED = 2 / 3
MIN_SPEED = 0.1
#: Minutes the GPU rests after each chapter (user, 2026-10-13: "pause in
#: between chapters" -- to cool down; the length was left to me).
DEFAULT_BREAK_MINUTES = 5.0
_LOCK_STALE_SECONDS = 30.0
_VOICE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


def check_voice(voice: str) -> str:
    if not _VOICE_RE.fullmatch(voice or ""):
        raise ValueError(f"Bad voice name: {voice!r}")
    return voice


def audio_dir(store: BookStore, book_id: str, voice: str) -> Path:
    """Where a book's chapters are rendered in *voice*: one folder per voice,
    so switching narrator never overwrites what was made."""
    return store.root / book_id / "audio" / check_voice(voice)


def chapter_file(store: BookStore, book_id: str, voice: str, index: int) -> Path:
    return audio_dir(store, book_id, voice) / f"{index:04d}.m4a"


_status_lock = threading.RLock()
_log = logging.getLogger(__name__)


def read_status_file(path: Path) -> Dict[str, Any]:
    """A voice folder's status.json ({} when missing or unreadable)."""
    for _ in range(10):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:  # mid-swap on Windows
            time.sleep(0.05)
        except (OSError, ValueError):
            return {}
    return {}


def update_status_file(path: Path, change: Callable[[Dict[str, Any]], Any]) -> Any:
    """Read, change and write a status.json as one step. Every writer (the
    render worker, flag dismiss, redo, timing alignment) goes through here:
    a lock so no update is lost, and a retried swap because Windows refuses
    to replace a file the page is reading at that moment (review, 10-14)."""
    with _status_lock:
        status = read_status_file(path)
        result = change(status)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(status, indent=1, ensure_ascii=False), encoding="utf-8"
        )
        for _ in range(40):
            try:
                tmp.replace(path)
                return result
            except PermissionError:
                time.sleep(0.05)
        _log.warning("Could not update %s (kept busy by a reader)", path)
        raise PermissionError(f"{path} stayed locked")


def book_voice(book: Dict[str, Any]) -> str:
    return book.get("voice") or DEFAULT_VOICE


def parse_chapters(book: Dict[str, Any], spec: str) -> List[int]:
    """Chapter indexes for "40-60", "7", "1-3,9": by the book's own chapter
    numbers when it has them (so "32" is both halves 32.1 and 32.2), else by
    position. Unknown numbers are an error, not silently nothing."""
    chapters = book["chapters"]
    numbered = any(c.get("number") for c in chapters)
    wanted: List[int] = []
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:-\s*(\d+(?:\.\d+)?))?", part)
        if not m:
            raise ValueError(f"Not a chapter range: {part!r}")
        lo = float(m.group(1))
        hi = float(m.group(2)) if m.group(2) else lo
        if hi < lo:
            raise ValueError(f"Range runs backwards: {part!r}")
        hits = []
        for c in chapters:
            key = c["number"] if numbered else str(c["index"])
            if not key:
                continue
            value = float(key)
            whole = "." not in part and "." in key
            if lo <= (int(value) if whole else value) <= hi:
                hits.append(c["index"])
        if not hits:
            raise ValueError(f"No chapter {part} in {book['title']}")
        wanted.extend(h for h in hits if h not in wanted)
    return wanted


class RenderQueue:
    def __init__(self, store: BookStore) -> None:
        self.store = store
        self.path = store.root / "queue.json"
        self._lock = store.root / "queue.lock"

    # ------------------------------------------------------------ storage

    def load(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        data.setdefault("paused", False)
        data.setdefault("jobs", [])
        data.setdefault("speed", DEFAULT_SPEED)
        data.setdefault("break_minutes", DEFAULT_BREAK_MINUTES)
        return data

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.store.root.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + 10.0
        while True:
            try:
                fd = os.open(self._lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                break
            except FileExistsError:
                try:
                    if time.time() - self._lock.stat().st_mtime > _LOCK_STALE_SECONDS:
                        self._lock.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() > deadline:
                    raise TimeoutError("the render queue is locked") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            self._lock.unlink(missing_ok=True)

    def _mutate(self, change: Callable[[Dict[str, Any]], Any]) -> Any:
        with self._locked():
            data = self.load()
            result = change(data)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
            return result

    # ------------------------------------------------------------ changes

    def add(
        self,
        book_id: str,
        mode: str,
        *,
        chapters: Optional[List[int]] = None,
        voice: Optional[str] = None,
        ahead: int = 5,
        position: int = 0,
    ) -> Dict[str, Any]:
        """Queue a job. ``voice`` defaults to the book's narrator."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        book = self.store.load(book_id)
        if mode == "book":
            chapters = [c["index"] for c in book["chapters"] if not c["skip"]]
        elif mode == "range" and not chapters:
            raise ValueError("a range job needs chapters")
        known = {c["index"] for c in book["chapters"]}
        if chapters and not set(chapters) <= known:
            raise ValueError(f"unknown chapters: {sorted(set(chapters) - known)}")
        job = {
            "id": uuid.uuid4().hex[:8],
            "book": book_id,
            "voice": check_voice(voice or book_voice(book)),
            "mode": mode,
            "chapters": chapters or [],
            "ahead": max(1, int(ahead)),
            "position": int(position),
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "state": "active",
        }
        self._mutate(lambda d: d["jobs"].append(job))
        return job

    def set_paused(self, paused: bool) -> None:
        self._mutate(lambda d: d.__setitem__("paused", bool(paused)))

    def set_speed(self, speed: float) -> float:
        """How hard the book voice works: 1 = flat out, 0.5 = half the time."""
        speed = float(speed)
        if not MIN_SPEED <= speed <= 1.0:
            raise ValueError(f"speed must be between {MIN_SPEED} and 1")
        self._mutate(lambda d: d.__setitem__("speed", speed))
        return speed

    def set_break(self, minutes: float) -> float:
        """Minutes to rest after each chapter (0 = none)."""
        minutes = float(minutes)
        if not 0 <= minutes <= 240:
            raise ValueError("break must be between 0 and 240 minutes")
        self._mutate(lambda d: d.__setitem__("break_minutes", minutes))
        return minutes

    def cancel(self, job_id: str) -> int:
        """Cancel one job, or every open one with "all". Returns how many."""

        def change(d: Dict[str, Any]) -> int:
            n = 0
            for job in d["jobs"]:
                if job["state"] == "active" and job_id in ("all", job["id"]):
                    job["state"] = "cancelled"
                    n += 1
            return n

        return self._mutate(change)

    def set_position(self, book_id: str, index: int) -> int:
        """Where the listener is in *book_id*: rolling jobs refill after it."""

        def change(d: Dict[str, Any]) -> int:
            n = 0
            for job in d["jobs"]:
                if (
                    job["book"] == book_id
                    and job["mode"] == "rolling"
                    and job["state"] == "active"
                ):
                    job["position"] = int(index)
                    n += 1
            return n

        return self._mutate(change)

    def active(self, job_id: str) -> bool:
        return any(
            j["id"] == job_id and j["state"] == "active" for j in self.load()["jobs"]
        )

    # ------------------------------------------------------------ reading

    def wanted(self, job: Dict[str, Any], book: Dict[str, Any]) -> List[int]:
        """The chapters *job* wants rendered, in order."""
        if job["mode"] != "rolling":
            return list(job["chapters"])
        # Reading order is the list's: an added chapter (additions.py) has a
        # higher index than the ones around it.
        order = [c["index"] for c in book["chapters"]]
        at = order.index(job["position"]) + 1 if job["position"] in order else 0
        skip = {c["index"] for c in book["chapters"] if c["skip"]}
        return [i for i in order[at:] if i not in skip][: job["ahead"]]

    def is_rendered(self, job: Dict[str, Any], index: int) -> bool:
        return chapter_file(self.store, job["book"], job["voice"], index).is_file()

    def next_work(self) -> Optional[Tuple[Dict[str, Any], int]]:
        """(job, chapter index) to render next, or None. Finished book/range
        jobs, and jobs whose book is gone, are closed on the way."""
        finished: List[Tuple[str, str]] = []
        found = None
        for job in self.load()["jobs"]:
            if job["state"] != "active":
                continue
            try:
                book = self.store.load(job["book"])
            except (FileNotFoundError, ValueError):
                finished.append((job["id"], "cancelled"))
                continue
            todo = [i for i in self.wanted(job, book) if not self.is_rendered(job, i)]
            if todo:
                found = (job, todo[0])
                break
            if job["mode"] != "rolling":
                finished.append((job["id"], "done"))
        if finished:
            states = dict(finished)

            def close(d: Dict[str, Any]) -> None:
                for job in d["jobs"]:
                    if job["id"] in states and job["state"] == "active":
                        job["state"] = states[job["id"]]

            self._mutate(close)
        return found

    def progress(self, job: Dict[str, Any]) -> Tuple[int, int]:
        """(chapters rendered, chapters wanted) for *job* right now."""
        try:
            book = self.store.load(job["book"])
        except (FileNotFoundError, ValueError):
            return 0, 0
        wanted = self.wanted(job, book)
        return sum(1 for i in wanted if self.is_rendered(job, i)), len(wanted)
