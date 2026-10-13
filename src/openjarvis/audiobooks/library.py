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
from typing import Any, Dict, List, Optional, Tuple

from openjarvis.audiobooks import pieces as piece_rules
from openjarvis.audiobooks.importer import import_book
from openjarvis.audiobooks.queue import (
    RenderQueue,
    audio_dir,
    book_voice,
    check_voice,
    parse_chapters,
    read_status_file,
    update_status_file,
)
from openjarvis.audiobooks.store import BookStore

#: Seconds of audio per narrated word, from the 10-13 lotm-narrator renders
#: (153 wpm), for estimates before a chapter exists.
SECONDS_PER_WORD = 60 / 153


class BookNotFound(LookupError):
    pass


def _redo_summary(redo: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The last redo note as the page shows it."""
    if not redo:
        return None
    return {
        "note": redo.get("note") or "",
        "at": redo.get("at"),
        "understood": redo.get("understood") or [],
        "not_possible": redo.get("not_possible") or [],
    }


def _items(chapter: Dict[str, Any], text: str) -> List[piece_rules.Item]:
    return piece_rules.build(text, piece_rules.announcement(chapter))


def estimate_piece_starts(
    chapter: Dict[str, Any], text: str, seconds: float
) -> List[float]:
    """Each item's start second for a chapter rendered before timings were
    kept: the pauses are known, and speech is shared out by length."""
    items = _items(chapter, text)
    spoken = [it for it in items if it.text]
    gaps = sum(it.gap for it in items)
    chars = sum(len(it.text) for it in spoken) or 1
    # Each piece has a fixed lead-in and tail besides its words: without it
    # short lines ("Hmm.") came out too short and the lit line ran seconds
    # ahead of the voice (chapter 4, 2026-10-14).
    overhead = min(PIECE_OVERHEAD, 0.5 * max(0.0, seconds - gaps) / max(len(spoken), 1))
    rate = max(0.0, seconds - gaps - overhead * len(spoken)) / chars
    out, at = [], 0.0
    for it in items:
        out.append(round(at, 2))
        at += (len(it.text) * rate + overhead if it.text else 0.0) + it.gap
    return out


#: Seconds of every spoken piece that do not grow with its length (fitted to
#: Whisper word times of chapter 4).
PIECE_OVERHEAD = 1.0
#: A pause at least this long in the audio can be a paragraph break: the
#: renderer leaves 0.6 s after a paragraph (heard as ~1 s), 0.3 s after a sentence.
PAUSE_SECONDS = 0.75


def pauses_in(path: Path, frame: float = 0.02) -> List[Tuple[float, float]]:
    """(start, end) seconds of the quiet stretches in an audio file."""
    import shutil
    import subprocess

    import numpy as np

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return []
    raw = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(path)]
        + ["-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
        capture_output=True,
        check=False,
    ).stdout
    audio = np.frombuffer(raw, dtype="<f4")
    if not len(audio):
        return []
    n = int(8000 * frame)
    frames = audio[: len(audio) // n * n].reshape(-1, n)
    level = np.sqrt((frames**2).mean(axis=1))
    quiet = level < max(0.004, 0.06 * float(np.percentile(level, 90)))
    out, start = [], None
    for k, q in enumerate(np.append(quiet, False)):
        if q and start is None:
            start = k
        elif not q and start is not None:
            if (k - start) * frame >= PAUSE_SECONDS:
                out.append((start * frame, k * frame))
            start = None
    return out


#: Silence a piece carries at its ends besides the gap after it (measured
#: on chapter 4: paragraph gaps of 0.6 s come out as ~0.95-1.2 s pauses).
EDGE_SILENCE = 0.4


def align_to_pauses(
    chapter: Dict[str, Any], text: str, seconds: float, path: Path
) -> Optional[List[List[float]]]:
    """[paragraph, start] for a chapter rendered before exact timings were
    kept, from the pauses in its audio (user, 2026-10-14: the lit line ran
    seconds ahead of the voice).

    Each paragraph break is a long pause whose length is known in advance
    (a paragraph gap, a "..." beat, a scene break). One alignment over the
    whole chapter matches the breaks, in order, to the real pauses by
    length first and estimated time second, so a drifting estimate cannot
    pull a start onto the wrong pause. None when the audio cannot be read."""
    items = _items(chapter, text)
    pauses = [p for p in pauses_in(path) if p[1] - p[0] >= PAUSE_SECONDS]
    if not pauses:
        return None
    times = estimate_piece_starts(chapter, text, seconds)
    # The paragraph starts, each with the silence expected before it.
    want: List[Tuple[int, float, float]] = []  # (para, estimate, pause)
    before = 0.0
    for it, at in zip(items, times):
        if it.text is None:
            before += it.gap
            continue
        if it.para >= 0 and (not want or want[-1][0] != it.para) and before > 0:
            want.append((it.para, at, before))
        before = it.gap
    n, m = len(want), len(pauses)
    inf = float("inf")
    skip_start, skip_pause = 2.0, 1.2
    cost = [[inf] * (m + 1) for _ in range(n + 1)]
    move = [[0] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    for j in range(1, m + 1):
        cost[0][j] = cost[0][j - 1] + skip_pause
        move[0][j] = 2
    for i in range(1, n + 1):
        para, at, gap = want[i - 1]
        cost[i][0] = cost[i - 1][0] + skip_start
        move[i][0] = 1
        for j in range(1, m + 1):
            start, end = pauses[j - 1]
            best, how = cost[i][j - 1] + skip_pause, 2
            if cost[i - 1][j] + skip_start < best:
                best, how = cost[i - 1][j] + skip_start, 1
            off = abs(end - at)
            if off <= 40:
                fit = 2.0 * abs((end - start) - (gap + EDGE_SILENCE))
                if i > 1 and j > 1 and move[i - 1][j - 1] == 0:
                    # Following on from a match: the spacing to it should be
                    # what the text predicts (a steady drift cancels out).
                    step = (end - pauses[j - 2][1]) - (at - want[i - 2][1])
                    fit += 0.5 * abs(step)
                else:
                    fit += 0.08 * off
                if cost[i - 1][j - 1] + fit < best:
                    best, how = cost[i - 1][j - 1] + fit, 0
            cost[i][j], move[i][j] = best, how
    found: Dict[int, float] = {}
    i, j = n, m
    while i > 0 or j > 0:
        how = move[i][j]
        if how == 0:
            found[want[i - 1][0]] = pauses[j - 1][1]
            i, j = i - 1, j - 1
        elif how == 1:
            i -= 1
        else:
            j -= 1
    # Every paragraph start: the matched pause, else its estimate moved by
    # the drift of the last matched one.
    out: List[List[float]] = []
    drift = 0.0
    for para, at in paragraph_starts_estimate(items, times):
        if para in found:
            drift = found[para] - at
            start = found[para]
        else:
            start = at + drift
        if out and start <= out[-1][1]:
            start = out[-1][1] + 0.01
        out.append([para, round(start, 2)])
    return out


def paragraph_starts_estimate(
    items: List[piece_rules.Item], times: List[float]
) -> List[List[float]]:
    from openjarvis.audiobooks.render import paragraph_starts

    return paragraph_starts(items, times)


def estimate_starts(
    chapter: Dict[str, Any], text: str, seconds: float
) -> List[List[float]]:
    from openjarvis.audiobooks.render import paragraph_starts

    items = _items(chapter, text)
    return paragraph_starts(items, estimate_piece_starts(chapter, text, seconds))


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
        status = read_status_file(folder / "status.json")
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
                    "redo": _redo_summary(c.get("redo")),
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
            "pronunciations": book.get("pronunciations") or [],
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
        starts, exact = self._starts(book, chapter, text)
        return {
            "index": chapter["index"],
            "label": chapter["label"],
            "title": chapter["title"],
            "words": chapter["words"],
            "paragraphs": [p.strip() for p in text.split("\n\n") if p.strip()],
            # [paragraph, second] for the reader's follow-along (user,
            # 2026-10-14); estimated for chapters rendered before timings
            # were kept.
            "starts": starts,
            "starts_exact": exact,
        }

    # ------------------------------------------------------------ flags

    def _status(self, book_id: str, voice: str) -> Tuple[Path, Dict[str, Any]]:
        path = audio_dir(self.store, book_id, voice) / "status.json"
        return path, read_status_file(path)

    def _starts(
        self, book: Dict[str, Any], chapter: Dict[str, Any], text: str
    ) -> Tuple[Optional[List[List[float]]], bool]:
        """([paragraph, second], exact) for a rendered chapter, else (None,
        False). Exact when the render recorded them; for older chapters,
        aligned to the pauses in the audio once and kept."""
        voice = book_voice(book)
        entry = self._rendered(book["id"], voice).get(chapter["index"])
        if not entry:
            return None, False
        if entry.get("paragraphs"):
            return entry["paragraphs"], True
        if not entry.get("seconds"):
            return None, False
        if entry.get("paragraphs_aligned"):
            return entry["paragraphs_aligned"], False
        path = audio_dir(self.store, book["id"], voice) / entry["file"]
        starts = align_to_pauses(chapter, text, entry["seconds"], path)
        if starts is None:
            return estimate_starts(chapter, text, entry["seconds"]), False
        self._save_aligned(book["id"], voice, chapter["index"], starts)
        return starts, False

    def _save_aligned(
        self, book_id: str, voice: str, index: int, starts: List[List[float]]
    ) -> None:
        """Keep an older chapter's aligned timings: decoding it is ~1 s."""
        path = audio_dir(self.store, book_id, voice) / "status.json"

        def keep(status: Dict[str, Any]) -> None:
            if str(index) in status:
                status[str(index)]["paragraphs_aligned"] = starts

        try:
            update_status_file(path, keep)
        except OSError:
            pass  # computed again next time

    def chapter_flags(self, book_id: str, index: int) -> List[Dict[str, Any]]:
        """Lines the check could not get right in a chapter: ``piece``,
        ``text`` (what the voice was given), ``why``, ``at`` (second)."""
        book = self.store.load(book_id)
        voice = book_voice(book)
        _, status = self._status(book_id, voice)
        entry = status.get(str(int(index))) or {}
        flags = [dict(f) for f in entry.get("flags") or []]
        if flags and any("at" not in f for f in flags) and entry.get("seconds"):
            # Older chapter: the line's paragraph start (aligned to the audio)
            # plus how far into the paragraph the line is by estimate.
            chapter = next(c for c in book["chapters"] if c["index"] == int(index))
            text = self.store.chapter_text(book_id, int(index))
            items = _items(chapter, text)
            times = estimate_piece_starts(chapter, text, entry["seconds"])
            starts, _ = self._starts(book, chapter, text)
            para_at = {p: t for p, t in starts or []}
            first: Dict[int, float] = {}
            for it, t in zip(items, times):
                first.setdefault(it.para, t)
            for f in flags:
                k = f["piece"]
                if "at" in f or not 0 <= k < len(items):
                    continue
                para = items[k].para
                if para in para_at:
                    f["at"] = round(para_at[para] + times[k] - first[para], 2)
                else:
                    f["at"] = times[k]
        return flags

    def dismiss_flag(self, book_id: str, index: int, piece: int) -> int:
        """The user listened and the line is fine: drop its flag (kept under
        ``dismissed``). Returns how many flags the chapter still has."""
        voice = book_voice(self.store.load(book_id))
        path = audio_dir(self.store, book_id, voice) / "status.json"

        def drop(status: Dict[str, Any]) -> int:
            entry = status.get(str(int(index)))
            if not entry:
                raise FileNotFoundError(f"Chapter {index} is not rendered")
            flags = entry.get("flags") or []
            entry["flags"] = [f for f in flags if f.get("piece") != int(piece)]
            gone = [f for f in flags if f.get("piece") == int(piece)]
            entry["dismissed"] = (entry.get("dismissed") or []) + gone
            return len(entry["flags"])

        return update_status_file(path, drop)

    def redo_chapter(
        self, book_id: str, index: int, settings: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Render a chapter again (a flagged line, or new reading rules): the
        old file is moved to ``replaced/``, not deleted, and the chapter is
        queued at once. ``settings`` (redo_notes.interpret) come from the
        user's note: kept on the chapter, and book-wide pronunciations on
        the book; without a note an earlier note's settings stay."""
        if settings is not None:
            self._keep_redo(book_id, int(index), settings)
        book = self.store.load(book_id)
        voice = book_voice(book)
        folder = audio_dir(self.store, book_id, voice)
        path = folder / f"{int(index):04d}.m4a"
        if path.is_file():
            backup = folder / "replaced"
            backup.mkdir(exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            path.replace(backup / f"{int(index):04d}-{stamp}.m4a")
        update_status_file(
            folder / "status.json", lambda status: status.pop(str(int(index)), None)
        )
        return self.queue.add(book_id, "range", chapters=[int(index)], voice=voice)

    def _keep_redo(self, book_id: str, index: int, settings: Dict[str, Any]) -> None:
        book = self.store.load(book_id)
        sayings = {p["word"].lower(): p for p in book.get("pronunciations") or []}
        chapter_only = []
        for p in settings.get("pronunciations") or []:
            entry = {"word": p["word"], "say": p["say"]}
            if p.get("book_wide", True):
                sayings[p["word"].lower()] = entry
            else:
                chapter_only.append(entry)
        chapters = []
        for c in book["chapters"]:
            if c["index"] == index:
                c = {**c, "redo": {**settings, "pronunciations": chapter_only}}
            chapters.append(c)
        self.store.update(
            book_id, chapters=chapters, pronunciations=list(sayings.values())
        )

    # ------------------------------------------------------------ adding chapters

    def add_chapters_from_file(self, book_id: str, path: str | Path) -> Dict[str, Any]:
        from openjarvis.audiobooks.additions import add_from_file

        return add_from_file(book_id, path, self.store)

    def add_chapter_text(
        self, book_id: str, number: str, text: str, title: str = ""
    ) -> Dict[str, Any]:
        from openjarvis.audiobooks.additions import add_text

        return add_text(book_id, number, text, title, self.store)

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
