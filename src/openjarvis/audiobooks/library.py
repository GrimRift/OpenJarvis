"""The audiobook library as the Books page and Sage's chat tools see it.

One layer over the store, the queue and the worker's status file, so the
page (``server/book_routes.py``) and the tools (``tools/audiobook_tool.py``)
read and change the same things the same way -- a fourth path that drifted
is the trap AGENTS.md keeps naming.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.audiobooks.importer import import_book
from openjarvis.audiobooks.queue import (
    RenderQueue,
    audio_dir,
    book_voice,
    check_voice,
    parse_chapters,
)
from openjarvis.audiobooks.store import BookStore

#: Seconds of audio per narrated word, from the 10-13 lotm-narrator renders
#: (153 wpm), for estimates before a chapter exists.
SECONDS_PER_WORD = 60 / 153


class BookNotFound(LookupError):
    pass


class Library:
    def __init__(self, store: Optional[BookStore] = None) -> None:
        self.store = store or BookStore()
        self.queue = RenderQueue(self.store)

    # ------------------------------------------------------------ finding

    def find(self, ref: str) -> Dict[str, Any]:
        """A book by id, id prefix or a piece of its title; the only book
        when ``ref`` is empty. Raises BookNotFound with what matched."""
        books = self.store.list()
        ref = (ref or "").strip()
        if not ref and len(books) == 1:
            return self.store.load(books[0]["id"])
        exact = [b for b in books if b["id"] == ref]
        hits = exact or [
            b
            for b in books
            if ref
            and (b["id"].startswith(ref.lower()) or ref.lower() in b["title"].lower())
        ]
        if len(hits) != 1:
            names = (
                "; ".join(b["title"] for b in (hits or books)) or "no books imported"
            )
            what = "matches several books" if hits else "matches no book"
            raise BookNotFound(f"{ref or 'That'} {what} ({names})")
        return self.store.load(hits[0]["id"])

    # ------------------------------------------------------------ reading

    def worker_status(self) -> Dict[str, Any]:
        path = self.store.root / "render-status.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"state": "unknown"}

    def _rendered(self, book_id: str, voice: str) -> Dict[str, Dict[str, Any]]:
        """index -> status.json entry, for chapters whose file exists."""
        folder = audio_dir(self.store, book_id, voice)
        try:
            status = json.loads((folder / "status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            status = {}
        out = {}
        for key, entry in status.items():
            if (folder / entry.get("file", "")).is_file():
                out[int(key)] = entry
        for path in folder.glob("[0-9][0-9][0-9][0-9].m4a"):
            out.setdefault(int(path.stem), {"file": path.name, "seconds": None})
        return out

    def summaries(self) -> List[Dict[str, Any]]:
        """Every book for the page's list: title, author, voice, progress."""
        out = []
        for meta in self.store.list():
            voice = book_voice(meta)
            rendered = self._rendered(meta["id"], voice)
            out.append(
                {
                    "id": meta["id"],
                    "title": meta["title"],
                    "author": meta.get("author") or "",
                    "voice": voice,
                    "has_cover": bool(meta.get("cover")),
                    "chapters": meta.get("narrated_chapters") or meta["chapter_count"],
                    "rendered": len(rendered),
                    "words": meta.get("words"),
                    "position": meta.get("position"),
                    "imported_at": meta.get("imported_at"),
                }
            )
        return out

    def detail(self, book_id: str) -> Dict[str, Any]:
        """One book with every chapter's state for its narrator."""
        book = self.store.load(book_id)
        voice = book_voice(book)
        rendered = self._rendered(book_id, voice)
        status = self.worker_status()
        queue = self.queue.load()
        queued: set = set()
        jobs = []
        for job in queue["jobs"]:
            if job["state"] != "active" or job["book"] != book_id:
                continue
            wanted = self.queue.wanted(job, book)
            queued.update(i for i in wanted if job["voice"] == voice)
            done = (
                sum(1 for i in wanted if i in rendered)
                if job["voice"] == voice
                else None
            )
            jobs.append({**job, "wanted": len(wanted), "done": done})
        working = (
            status.get("book") == book_id
            and status.get("voice") == voice
            and status.get("state") in ("rendering", "waiting", "starting", "stopped")
        )
        chapters = []
        for c in book["chapters"]:
            entry = rendered.get(c["index"])
            if entry:
                state = "ready"
            elif working and status.get("chapter") == c["index"]:
                state = "rendering"
            elif c["index"] in queued:
                state = "queued"
            elif c["skip"]:
                state = "skipped"
            else:
                state = "not made"
            chapters.append(
                {
                    "index": c["index"],
                    "label": c["label"],
                    "number": c["number"],
                    "title": c["title"],
                    "words": c["words"],
                    "skip": c["skip"],
                    "skip_reason": c["skip_reason"],
                    "state": state,
                    "seconds": (entry or {}).get("seconds"),
                    "estimate_seconds": round(c["words"] * SECONDS_PER_WORD),
                    "flags": len((entry or {}).get("flags") or []),
                }
            )
        return {
            "id": book["id"],
            "title": book["title"],
            "author": book.get("author") or "",
            "voice": voice,
            "has_cover": bool(book.get("cover")),
            "words": book.get("words"),
            "missing_numbers": book.get("missing_numbers") or [],
            "position": book.get("position"),
            "position_seconds": book.get("position_seconds") or 0,
            "chapters": chapters,
            "jobs": jobs,
            "queue": {
                "paused": queue["paused"],
                "speed": queue["speed"],
                "break_minutes": queue["break_minutes"],
            },
            "worker": status,
        }

    def chapter_text(self, book_id: str, index: int) -> Dict[str, Any]:
        """A chapter to read on the page (user, 2026-10-13: read without a
        narrator): the same cleaned text the voice reads, as paragraphs;
        ``* * *`` stays a scene break and the notes keep their heading."""
        book = self.store.load(book_id)
        chapter = next((c for c in book["chapters"] if c["index"] == int(index)), None)
        if chapter is None:
            raise FileNotFoundError(f"No chapter #{index}")
        text = self.store.chapter_text(book_id, int(index))
        return {
            "index": chapter["index"],
            "label": chapter["label"],
            "title": chapter["title"],
            "words": chapter["words"],
            "paragraphs": [p.strip() for p in text.split("\n\n") if p.strip()],
        }

    def chapter_audio(
        self, book_id: str, index: int, voice: Optional[str] = None
    ) -> Path:
        book = self.store.load(book_id)
        voice = check_voice(voice or book_voice(book))
        path = audio_dir(self.store, book_id, voice) / f"{int(index):04d}.m4a"
        if not path.is_file():
            raise FileNotFoundError(f"Chapter {index} is not rendered in {voice} yet")
        return path

    def cover(self, book_id: str) -> Path:
        book = self.store.load(book_id)
        if not book.get("cover"):
            raise FileNotFoundError("This book has no cover")
        return self.store.root / book_id / book["cover"]

    # ------------------------------------------------------------ changing

    def import_file(self, path: str | Path) -> Dict[str, Any]:
        return import_book(path, self.store)

    def set_voice(self, book_id: str, voice: str) -> Dict[str, Any]:
        return self.store.update(book_id, voice=check_voice(voice))

    def render(
        self,
        book_id: str,
        *,
        chapters: str = "",
        ahead: int = 0,
        voice: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Queue the whole book, a chapter range ("40-60") or, with
        ``ahead``, keep that many chapters ready after the listener."""
        book = self.store.load(book_id)
        if ahead:
            return self.queue.add(
                book_id,
                "rolling",
                ahead=ahead,
                position=book.get("position") or 0,
                voice=voice,
            )
        if chapters:
            return self.queue.add(
                book_id, "range", chapters=parse_chapters(book, chapters), voice=voice
            )
        return self.queue.add(book_id, "book", voice=voice)

    def set_position(
        self, book_id: str, index: int, seconds: float = 0.0
    ) -> Dict[str, Any]:
        """Where the listener is: rolling jobs render ahead of it, and the page
        resumes there (user, 2026-10-13: follow playback automatically)."""
        known = {c["index"] for c in self.store.load(book_id)["chapters"]}
        if int(index) not in known:
            raise ValueError(f"No chapter #{index}")
        self.queue.set_position(book_id, int(index))
        return self.store.update(
            book_id,
            position=int(index),
            position_seconds=round(max(0.0, float(seconds)), 1),
            position_at=time.time(),
        )

    def describe(self, book_id: str) -> str:
        """A short spoken summary: how far the book is and what is happening."""
        d = self.detail(book_id)
        ready = [c for c in d["chapters"] if c["state"] == "ready"]
        narrated = [c for c in d["chapters"] if not c["skip"]]
        hours = sum(c["seconds"] or 0 for c in ready) / 3600
        parts = [
            f"{d['title']}: {len(ready)} of {len(narrated)} chapters are ready "
            f"in {d['voice']} ({hours:.1f} hours of audio)."
        ]
        w = d["worker"]
        if d["queue"]["paused"]:
            parts.append("Rendering is paused.")
        elif w.get("book") == d["id"] and w.get("state") == "rendering":
            done, total = w.get("piece") or 0, w.get("pieces") or 0
            pct = f" ({round(100 * done / total)}%)" if total else ""
            parts.append(f"Chapter {w.get('chapter')} is being made{pct}.")
        elif w.get("state") == "waiting" and w.get("book") == d["id"]:
            parts.append(f"Waiting: {w.get('reason')}.")
        elif w.get("state") == "resting":
            parts.append("Resting between chapters.")
        queued = [c for c in d["chapters"] if c["state"] == "queued"]
        if queued:
            parts.append(f"{len(queued)} more chapters are queued.")
        if d["position"]:
            label = next(
                (c["label"] for c in d["chapters"] if c["index"] == d["position"]), None
            )
            if label:
                parts.append(f"You're at {label}.")
        return " ".join(parts)
