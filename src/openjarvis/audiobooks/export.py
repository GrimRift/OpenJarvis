"""Getting a narrated book off the PC: M4B parts and chapters by Telegram.

Delivery as scoped (M42, 2026-10-13), with the user's choices at build:

* **M4B parts** of ``PART_SIZE`` (25) narrated chapters each, in
  ``D:\\Audiobooks\\<Book>\\Part 01 (Ch 1-25).m4b``: one chapter marker per
  chapter (titled like the chapter's announcement), title/album/author tags,
  the book's cover. Built by STREAM COPY of the chapter M4As (AAC was chosen
  per chapter so parts need no re-encode): ffmpeg's concat demuxer plus an
  ffmetadata file with the CHAPTER entries.
* **The same file copied to OneDrive** (``OneDrive\\Sage\\Audiobooks\\<Book>``)
  so it reaches the phone.
* **Written automatically** when every chapter of a part is rendered
  (``auto_export``, called by the render worker), and on request
  (``export_parts``; ``partial=True`` also writes parts still being made,
  e.g. "Part 01 (Ch 1-3)", replaced by the full part later).
* **Telegram on request** (``send_chapters``): one chapter file per message
  through ``[notifications] channel`` (bot uploads cap at 50 MB per file; a
  22-min chapter is ~11 MB).

What was exported is recorded per voice in ``audio/<voice>/exports.json``
(chapters and the size/mtime of each chapter file), so a part is rewritten
only when its chapters changed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from openjarvis.audiobooks import pieces as piece_rules
from openjarvis.audiobooks.queue import (
    audio_dir,
    book_voice,
    chapter_file,
    parse_chapters,
)
from openjarvis.audiobooks.store import BookStore

logger = logging.getLogger(__name__)

#: Narrated chapters per M4B part (user, 2026-10-13: 25, ~9 h per part).
PART_SIZE = 25
#: Where parts go (user, 2026-10-13). ``SAGE_AUDIOBOOKS_DIR`` overrides.
DEFAULT_ROOT = Path("D:/Audiobooks")
#: Subfolder of OneDrive the parts are copied to (user, 2026-10-13).
#: ``SAGE_AUDIOBOOKS_ONEDRIVE`` overrides; set it to "off" for no copy.
ONEDRIVE_SUBDIR = Path("Sage") / "Audiobooks"
#: Telegram bots may upload at most 50 MB per file.
TELEGRAM_MAX_BYTES = 50 * 1024 * 1024
#: Chapter durations in a part may drift from their sum by AAC frame padding.
DURATION_TOLERANCE = 1.0


@dataclass(frozen=True)
class Destinations:
    """Where parts are written: the PC folder and (optionally) OneDrive."""

    root: Path
    onedrive: Optional[Path]


def destinations() -> Destinations:
    root = Path(os.environ.get("SAGE_AUDIOBOOKS_DIR") or DEFAULT_ROOT)
    spec = os.environ.get("SAGE_AUDIOBOOKS_ONEDRIVE")
    if spec is not None:
        onedrive = None if spec.strip().lower() in ("", "off", "0") else Path(spec)
    else:
        base = os.environ.get("OneDrive") or str(Path.home() / "OneDrive")
        onedrive = Path(base) / ONEDRIVE_SUBDIR if Path(base).is_dir() else None
    return Destinations(root, onedrive)


def auto_export_enabled() -> bool:
    return os.environ.get("SAGE_AUDIOBOOKS_AUTO_EXPORT", "1") != "0"


# ------------------------------------------------------------------ parts


@dataclass
class Part:
    number: int
    chapters: List[Dict[str, Any]]
    of: int  # how many parts the book has

    @property
    def indexes(self) -> List[int]:
        return [c["index"] for c in self.chapters]

    def name(
        self,
        last: Optional[Dict[str, Any]] = None,
        first: Optional[Dict[str, Any]] = None,
    ) -> str:
        """'Part 01 (Ch 1-25)'; ``first``/``last`` = the chapters actually at
        the ends of the file when a partial part is written."""
        width = max(2, len(str(self.of)))
        start = _chapter_no(first or self.chapters[0])
        end = _chapter_no(last or self.chapters[-1])
        span = start if start == end else f"{start}-{end}"
        return f"Part {self.number:0{width}d} (Ch {span})"


def _chapter_no(chapter: Dict[str, Any]) -> str:
    return str(chapter.get("number") or chapter["index"])


def plan_parts(book: Dict[str, Any], part_size: int = PART_SIZE) -> List[Part]:
    """The book's narrated (non-skip) chapters in runs of ``part_size``."""
    narrated = [c for c in book["chapters"] if not c.get("skip")]
    runs = [narrated[i : i + part_size] for i in range(0, len(narrated), part_size)]
    return [Part(n, run, len(runs)) for n, run in enumerate(runs, 1)]


def part_of(
    book: Dict[str, Any], index: int, part_size: int = PART_SIZE
) -> Optional[Part]:
    return next((p for p in plan_parts(book, part_size) if index in p.indexes), None)


def _safe_name(text: str) -> str:
    """A Windows-safe file/folder name."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", text)
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")
    return name[:120] or "Book"


def book_folder_name(book: Dict[str, Any], voice: str) -> str:
    """The book's title; another voice than the book's own gets its own
    folder, so exporting it never replaces the narrator's parts."""
    name = _safe_name(book["title"])
    return name if voice == book_voice(book) else f"{name} ({_safe_name(voice)})"


# ---------------------------------------------------------------- records


def _exports_path(store: BookStore, book_id: str, voice: str) -> Path:
    return audio_dir(store, book_id, voice) / "exports.json"


def _load_exports(store: BookStore, book_id: str, voice: str) -> Dict[str, Any]:
    try:
        return json.loads(
            _exports_path(store, book_id, voice).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return {}


def _save_exports(
    store: BookStore, book_id: str, voice: str, data: Dict[str, Any]
) -> None:
    path = _exports_path(store, book_id, voice)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _fingerprint(files: Iterable[Path]) -> List[List[Any]]:
    out = []
    for f in files:
        st = f.stat()
        out.append([f.name, st.st_size, int(st.st_mtime)])
    return out


def part_status(
    book_id: str,
    voice: Optional[str] = None,
    *,
    store: Optional[BookStore] = None,
    part_size: int = PART_SIZE,
) -> List[Dict[str, Any]]:
    """Every part of the book for *voice*: chapters, how many are rendered,
    and whether (and where) it is exported and still up to date."""
    store = store or BookStore()
    book = store.load(book_id)
    voice = voice or book_voice(book)
    records = _load_exports(store, book_id, voice)
    out = []
    for part in plan_parts(book, part_size):
        files = [chapter_file(store, book_id, voice, i) for i in part.indexes]
        made = [f for f in files if f.is_file()]
        record = records.get(str(part.number))
        current = bool(
            record
            and Path(record["local"]).is_file()
            and record["fingerprint"] == _fingerprint(made)
        )
        out.append(
            {
                "part": part.number,
                "name": part.name(),
                "chapters": part.indexes,
                "rendered": len(made),
                "total": len(files),
                "complete": len(made) == len(files),
                "exported": record if current else None,
            }
        )
    return out


# ----------------------------------------------------------------- ffmpeg


def _tool(name: str) -> str:
    exe = shutil.which(name)
    if not exe:
        raise RuntimeError(f"{name} is not on PATH; it is needed to export parts")
    return exe


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        [
            _tool("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


def _meta_escape(text: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", text)


def _concat_line(path: Path) -> str:
    return "file '" + str(path).replace("'", "'\\''") + "'\n"


def build_m4b(
    out: Path,
    chapters: Sequence[tuple],
    tags: Dict[str, str],
    cover: Optional[Path] = None,
) -> float:
    """Join chapter M4As (``(path, title)`` pairs) into one M4B by stream
    copy, with a chapter marker per file. Returns the total seconds."""
    lines = [";FFMETADATA1"]
    lines += [f"{key}={_meta_escape(value)}" for key, value in tags.items()]
    start = 0
    for path, title in chapters:
        end = start + round(probe_duration(path) * 1000)
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={start}",
            f"END={end}",
            f"title={_meta_escape(title)}",
        ]
        start = end
    with tempfile.TemporaryDirectory() as tmp:
        listing = Path(tmp) / "list.txt"
        listing.write_text(
            "".join(_concat_line(p) for p, _ in chapters), encoding="utf-8"
        )
        meta = Path(tmp) / "meta.txt"
        meta.write_text("\n".join(lines) + "\n", encoding="utf-8")
        cmd = [
            _tool("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-i",
            str(meta),
        ]
        if cover:
            cmd += ["-i", str(cover)]
        cmd += ["-map", "0:a", "-map_metadata", "1", "-map_chapters", "1"]
        if cover:
            cmd += ["-map", "2:v", "-disposition:v:0", "attached_pic"]
        partial = out.with_name(f"~${out.name}.tmp")
        cmd += ["-c", "copy", "-movflags", "+faststart", "-f", "mp4", str(partial)]
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as exc:
            partial.unlink(missing_ok=True)
            raise RuntimeError(
                f"ffmpeg could not build {out.name}: {exc.stderr.strip()[-500:]}"
            ) from exc
        partial.replace(out)
    return start / 1000


def _copy_into(src: Path, folder: Path) -> Path:
    """Copy *src* into *folder* under a temp name OneDrive skips, then swap."""
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / src.name
    tmp = folder / f"~${src.name}.tmp"
    shutil.copyfile(src, tmp)
    tmp.replace(final)
    return final


def _drop_other_versions(folder: Path, part: Part, keep: str) -> None:
    """An earlier partial 'Part 01 (Ch 1-3).m4b' goes when the part grows."""
    if not folder.is_dir():
        return
    prefix = part.name().split(" (")[0] + " ("
    for old in folder.glob("Part *.m4b"):
        if old.name.startswith(prefix) and old.name != keep:
            old.unlink(missing_ok=True)


# ----------------------------------------------------------------- export


def export_part(
    book_id: str,
    part_number: int,
    voice: Optional[str] = None,
    *,
    partial: bool = False,
    force: bool = False,
    store: Optional[BookStore] = None,
    dest: Optional[Destinations] = None,
    part_size: int = PART_SIZE,
) -> Optional[Path]:
    """Write one part (PC folder + OneDrive copy). Returns the PC file, or
    None when nothing was written: not every chapter is rendered (unless
    ``partial``; then every rendered chapter of the part, gaps skipped -- the
    chapter markers say which are in), or the file is already up to date
    (unless ``force``)."""
    store = store or BookStore()
    dest = dest or destinations()
    book = store.load(book_id)
    voice = voice or book_voice(book)
    part = next(
        (p for p in plan_parts(book, part_size) if p.number == part_number), None
    )
    if part is None:
        raise ValueError(f"{book['title']} has no part {part_number}")
    present = [
        (chapter, path)
        for chapter in part.chapters
        if (path := chapter_file(store, book_id, voice, chapter["index"])).is_file()
    ]
    if not present or (len(present) < len(part.chapters) and not partial):
        return None
    files = [p for _, p in present]
    fingerprint = _fingerprint(files)
    records = _load_exports(store, book_id, voice)
    record = records.get(str(part.number))
    if (
        not force
        and record
        and record["fingerprint"] == fingerprint
        and Path(record["local"]).is_file()
    ):
        return None

    whole = len(present) == len(part.chapters)
    name = part.name() if whole else part.name(present[-1][0], present[0][0])
    filename = f"{_safe_name(name)}.m4b"
    folder = dest.root / book_folder_name(book, voice)
    folder.mkdir(parents=True, exist_ok=True)
    cover = store.root / book_id / book["cover"] if book.get("cover") else None
    author = book.get("author") or ""
    out = folder / filename
    seconds = build_m4b(
        out,
        [(p, piece_rules.announcement(c).rstrip(".")) for c, p in present],
        {
            "title": f"{book['title']} - {name}",
            "album": book["title"],
            "artist": author,
            "album_artist": author,
            "genre": "Audiobook",
            "track": f"{part.number}/{part.of}",
            "media_type": "2",
            "comment": f"Sage voice: {voice}",
        },
        cover if cover and cover.is_file() else None,
    )
    _drop_other_versions(folder, part, filename)
    copy = None
    if dest.onedrive is not None:
        target = dest.onedrive / book_folder_name(book, voice)
        try:
            copy = str(_copy_into(out, target))
            _drop_other_versions(target, part, filename)
        except OSError:
            logger.warning("Could not copy %s to OneDrive", out.name, exc_info=True)
    records[str(part.number)] = {
        "file": filename,
        "local": str(out),
        "onedrive": copy,
        "chapters": [c["index"] for c, _ in present],
        "partial": len(present) < len(part.chapters),
        "seconds": round(seconds, 1),
        "bytes": out.stat().st_size,
        "fingerprint": fingerprint,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _save_exports(store, book_id, voice, records)
    logger.info(
        "audiobook %s: exported %s (%d chapters, %.0f s)%s",
        book_id,
        filename,
        len(present),
        seconds,
        " + OneDrive" if copy else "",
    )
    return out


def export_parts(
    book_id: str,
    voice: Optional[str] = None,
    parts: Optional[Iterable[int]] = None,
    *,
    partial: bool = False,
    force: bool = False,
    store: Optional[BookStore] = None,
    dest: Optional[Destinations] = None,
    part_size: int = PART_SIZE,
) -> List[Path]:
    """Export the given parts (default: all) that can be and are not already
    up to date. Returns the PC files written."""
    store = store or BookStore()
    book = store.load(book_id)
    numbers = list(parts) if parts else [p.number for p in plan_parts(book, part_size)]
    written = []
    for number in numbers:
        path = export_part(
            book_id,
            number,
            voice,
            partial=partial,
            force=force,
            store=store,
            dest=dest,
            part_size=part_size,
        )
        if path is not None:
            written.append(path)
    return written


def auto_export(
    book_id: str,
    voice: str,
    index: int,
    *,
    store: Optional[BookStore] = None,
    dest: Optional[Destinations] = None,
) -> Optional[Path]:
    """Called after chapter *index* is rendered: writes its part once every
    chapter of that part exists (user's choice, 2026-10-13)."""
    if not auto_export_enabled():
        return None
    store = store or BookStore()
    part = part_of(store.load(book_id), index, PART_SIZE)
    if part is None:
        return None
    return export_part(
        book_id, part.number, voice, store=store, dest=dest, part_size=PART_SIZE
    )


# --------------------------------------------------------------- telegram


@dataclass
class SendResult:
    sent: List[int] = field(default_factory=list)
    not_made: List[int] = field(default_factory=list)
    too_big: List[int] = field(default_factory=list)
    failed: List[int] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.sent) and not self.failed and not self.error

    def describe(self, book: Dict[str, Any]) -> str:
        """One plain sentence or two for the user."""
        if self.error:
            return f"Could not send: {self.error}."

        def names(indexes: List[int]) -> str:
            by_index = {c["index"]: c for c in book["chapters"]}
            return ", ".join(_chapter_no(by_index[i]) for i in indexes)

        out = []
        if self.sent:
            out.append(f"Sent chapter(s) {names(self.sent)} to your phone.")
        if self.not_made:
            out.append(f"Not narrated yet: {names(self.not_made)}.")
        if self.too_big:
            out.append(f"Over Telegram's 50 MB limit: {names(self.too_big)}.")
        if self.failed:
            out.append(
                f"Telegram refused or could not be reached for {names(self.failed)}."
            )
        return " ".join(out) or "No chapters to send."


def _phone_channel() -> tuple:
    """``(channel, chat_id)`` from ``[notifications] channel``, or raises."""
    from openjarvis.core.config import load_config

    spec = (load_config().notifications.channel or "").strip()
    if not spec:
        raise RuntimeError("no phone channel is configured ([notifications] channel)")
    from openjarvis.agents.proactive_agent import _build_notification_channel

    channel = _build_notification_channel(spec)
    if channel is None or not hasattr(channel, "send_audio"):
        raise RuntimeError(f"the {spec.partition(':')[0]} channel cannot send audio")
    return channel, spec.partition(":")[2]


def send_chapters(
    book_id: str,
    voice: Optional[str] = None,
    chapters: Any = None,
    *,
    store: Optional[BookStore] = None,
    channel: Any = None,
    chat_id: str = "",
    on_progress: Optional[Callable[[SendResult, int], None]] = None,
) -> SendResult:
    """Send rendered chapters to the phone, one Telegram audio message each.

    ``chapters`` is a spec in the book's numbering ("5-10", "32" = both
    halves) or a list of chapter indexes. Chapters not narrated yet, or over
    50 MB, are reported, not sent."""
    store = store or BookStore()
    book = store.load(book_id)
    voice = voice or book_voice(book)
    if isinstance(chapters, str):
        indexes = parse_chapters(book, chapters)
    else:
        indexes = [int(i) for i in chapters or []]
    by_index = {c["index"]: c for c in book["chapters"]}
    result = SendResult()
    if channel is None:
        try:
            channel, chat_id = _phone_channel()
        except RuntimeError as exc:
            result.error = str(exc)
            return result
    for index in indexes:
        chapter = by_index.get(index)
        path = chapter_file(store, book_id, voice, index)
        if chapter is None or not path.is_file():
            result.not_made.append(index)
            continue
        if path.stat().st_size > TELEGRAM_MAX_BYTES:
            result.too_big.append(index)
            continue
        if on_progress is not None:
            on_progress(result, index)
        title = piece_rules.announcement(chapter).rstrip(".")
        ok = channel.send_audio(
            chat_id,
            str(path),
            caption=f"{book['title']} - {title}",
            title=title,
            performer=book["title"],
        )
        (result.sent if ok else result.failed).append(index)
    return result


# ------------------------------------------------- sending in the background

#: Upload speed seen on 10-13 (10.9 MB in 55 s), for "about N minutes".
UPLOAD_BYTES_PER_SECOND = 200_000
_send_lock = threading.Lock()
_status_lock = threading.Lock()
_send_thread: Optional[threading.Thread] = None


def _send_status_path(store: BookStore) -> Path:
    return store.root / "send-status.json"


def _write_send_status(store: BookStore, status: Dict[str, Any]) -> None:
    path = _send_status_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    status["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tmp = path.with_suffix(".tmp")
    with _status_lock:
        tmp.write_text(
            json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        # On Windows the swap fails while a reader (the page's poll) has the
        # file open; it is shut again within milliseconds.
        for attempt in range(20):
            try:
                tmp.replace(path)
                return
            except PermissionError:
                time.sleep(0.05)
        logger.warning("Could not update %s (kept busy by a reader)", path)


def send_status(store: Optional[BookStore] = None) -> Optional[Dict[str, Any]]:
    """The last background send: ``state`` sending / done / failed /
    stopped (the server restarted mid-send), chapters, sent, current,
    ``message`` (a sentence for the user)."""
    store = store or BookStore()
    try:
        status = json.loads(_send_status_path(store).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if status.get("state") == "sending" and not sending():
        status["state"] = "stopped"
        status["message"] = "Sending stopped when Sage restarted."
    return status


def sending() -> bool:
    return _send_thread is not None and _send_thread.is_alive()


def start_send(
    book_id: str,
    voice: Optional[str] = None,
    chapters: Any = None,
    *,
    store: Optional[BookStore] = None,
    channel: Any = None,
    chat_id: str = "",
    on_done: Optional[Callable[[SendResult], None]] = None,
) -> Dict[str, Any]:
    """Send chapters to the phone on a background thread (an upload takes
    ~1 min per chapter) and return the starting status at once. One send at
    a time: raises RuntimeError while another runs, ValueError when none of
    the chapters is narrated yet. ``on_done`` gets the result when the send
    finishes (not if it crashed)."""
    global _send_thread
    store = store or BookStore()
    book = store.load(book_id)
    voice = voice or book_voice(book)
    if isinstance(chapters, str):
        indexes = parse_chapters(book, chapters)
    else:
        indexes = [int(i) for i in chapters or []]
    made = [i for i in indexes if chapter_file(store, book_id, voice, i).is_file()]
    if not made:
        raise ValueError("None of those chapters is narrated yet")
    size = sum(chapter_file(store, book_id, voice, i).stat().st_size for i in made)
    with _send_lock:
        if sending():
            current = send_status(store) or {}
            done = len(current.get("sent", []))
            total = len(current.get("made", []))
            raise RuntimeError(
                f"Still sending {current.get('title', 'a book')} to the phone "
                f"({done} of {total} chapters done)"
            )
        status: Dict[str, Any] = {
            "state": "sending",
            "book": book_id,
            "title": book["title"],
            "voice": voice,
            "chapters": indexes,
            "made": made,
            "not_made": [i for i in indexes if i not in made],
            "sent": [],
            "current": None,
            "estimate_seconds": round(size / UPLOAD_BYTES_PER_SECOND),
            "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "message": "",
        }
        _write_send_status(store, status)

        def run() -> None:
            def progress(result: SendResult, index: int) -> None:
                status.update(sent=list(result.sent), current=index)
                _write_send_status(store, status)

            started = time.monotonic()
            try:
                result = send_chapters(
                    book_id,
                    voice,
                    made,
                    store=store,
                    channel=channel,
                    chat_id=chat_id,
                    on_progress=progress,
                )
                result.not_made = status["not_made"] + result.not_made
                status.update(
                    state="done" if result.ok else "failed",
                    sent=result.sent,
                    current=None,
                    message=result.describe(book),
                )
                if on_done is not None:
                    on_done(result)
            except Exception as exc:
                logger.warning("Sending %s to the phone failed", book_id, exc_info=True)
                status.update(
                    state="failed", current=None, message=f"Could not send: {exc}."
                )
            logger.info(
                "audiobook %s: sent %s to the phone in %.0f s",
                book_id,
                status["sent"],
                time.monotonic() - started,
            )
            _write_send_status(store, status)

        _send_thread = threading.Thread(target=run, name="audiobook-send", daemon=True)
        _send_thread.start()
    return dict(status)


def auto_send_pending(
    queue: Any,
    *,
    store: Optional[BookStore] = None,
    channel: Any = None,
    chat_id: str = "",
) -> Optional[Dict[str, Any]]:
    """Send the chapters waiting in the queue's ``send_pending`` (auto-send,
    user 2026-10-14: each chapter as soon as it is rendered, redone ones
    too). One book+voice per send; what is sent (or can never be: missing,
    over 50 MB) leaves the list, a failed one stays for the next chapter.
    Does nothing while another send runs: the next finished chapter starts
    it again. Returns the starting send status, or None."""
    store = store or BookStore()
    pending = queue.load()["send_pending"]
    if not pending or sending():
        return None
    book_id, voice = pending[0]["book"], pending[0]["voice"]
    group = [
        p["index"] for p in pending if p["book"] == book_id and p["voice"] == voice
    ]

    def done(result: SendResult) -> None:
        if result.error:  # no phone channel, or it could not be built: keep all
            logger.warning("Auto-send to the phone: %s", result.error)
            return
        if result.failed:
            logger.warning(
                "Auto-send to the phone failed for %s chapter(s) %s; "
                "they go with the next chapter",
                book_id,
                result.failed,
            )
        queue.drop_send_pending(
            book_id, voice, [i for i in group if i not in result.failed]
        )

    try:
        return start_send(
            book_id, voice, group, store=store, channel=channel, chat_id=chat_id,
            on_done=done,
        )
    except ValueError:  # none of them exists any more (book or audio removed)
        queue.drop_send_pending(book_id, voice, group)
    except RuntimeError:  # another send started first; picked up next time
        pass
    except Exception:
        logger.warning("Auto-send of %s to the phone failed", book_id, exc_info=True)
    return None


def delivery(
    book_id: str, voice: Optional[str] = None, *, store: Optional[BookStore] = None
) -> Dict[str, Any]:
    """What the Books page shows about getting this book off the PC: the
    folders, every part's state, and the last send if it was this book's."""
    store = store or BookStore()
    dest = destinations()
    sent = send_status(store)
    return {
        "folders": {
            "pc": str(dest.root),
            "onedrive": str(dest.onedrive) if dest.onedrive else None,
        },
        "part_size": PART_SIZE,
        "auto_export": auto_export_enabled(),
        "parts": part_status(book_id, voice, store=store),
        "sending": sent if sent and sent.get("book") == book_id else None,
    }
