"""M42: Sage's audiobook tools -- what a voice request about a book does.

"Make an audiobook of D:\\Downloads\\x.epub", "render chapters 40-60", "how far
is the book?", "pause the audiobook rendering", "read me chapter 4",
"continue the book". Each goes through ``audiobooks.library``, the same layer
the Books page uses. Playing happens in the app: ``audiobook_play`` returns
``metadata.audiobook_play`` and the page's book player picks it up when the
tool call ends, the route the picture and weather panels already take.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.audiobooks.library import BookNotFound, Library
from openjarvis.audiobooks.queue import parse_chapters
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_BOOK_PARAM = {
    "type": "string",
    "description": "Which book: part of its title. Empty if only one book exists.",
}


def _fail(tool: str, text: str) -> ToolResult:
    return ToolResult(tool_name=tool, content=text, success=False)


class _BookTool(BaseTool):
    def __init__(self, library: Optional[Library] = None) -> None:
        self._library = library

    @property
    def library(self) -> Library:
        if self._library is None:
            self._library = Library()
        return self._library


@ToolRegistry.register("audiobook_status")
class AudiobookStatusTool(_BookTool):
    tool_id = "audiobook_status"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="audiobook_status",
            description=(
                "How far an audiobook is: chapters ready, the chapter being made, "
                "what is queued, where the user is listening. 'How far is the "
                "book?', 'is my audiobook done?', 'what books do I have?'"
            ),
            parameters={"type": "object", "properties": {"book": _BOOK_PARAM}},
            category="media",
        )

    def execute(self, **params: Any) -> ToolResult:
        ref = str(params.get("book") or "")
        lib = self.library
        books = lib.summaries()
        if not books:
            return ToolResult(
                tool_name="audiobook_status",
                content="No books are imported yet. A PDF or EPUB can be imported "
                "on the Books page or by giving its path.",
            )
        if not ref and len(books) > 1:
            lines = [
                f"{b['title']}: {b['rendered']} of {b['chapters']} chapters ready"
                for b in books
            ]
            return ToolResult(tool_name="audiobook_status", content="; ".join(lines))
        try:
            book = lib.find(ref)
        except BookNotFound as exc:
            return _fail("audiobook_status", str(exc))
        return ToolResult(
            tool_name="audiobook_status", content=lib.describe(book["id"])
        )


@ToolRegistry.register("audiobook_render")
class AudiobookRenderTool(_BookTool):
    tool_id = "audiobook_render"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="audiobook_render",
            description=(
                "Make (narrate) an imported book as audio in one of Sage's voices. "
                "'Render chapters 40-60', 'make the whole book', 'keep 5 chapters "
                "ready ahead of me', 'do it in the Jarvis voice'. It renders in the "
                "background, gently, whenever Sage is idle."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "book": _BOOK_PARAM,
                    "chapters": {
                        "type": "string",
                        "description": "Chapter numbers like '40-60' or '1-3,9'. "
                        "Empty for the whole book.",
                    },
                    "ahead": {
                        "type": "integer",
                        "description": "Instead of chapters: keep this many chapters "
                        "ready after where the user is listening.",
                    },
                    "voice": {
                        "type": "string",
                        "description": "A Sage voice name (jarvis, lotm-narrator). "
                        "Empty keeps the book's narrator.",
                    },
                },
            },
            category="media",
        )

    def execute(self, **params: Any) -> ToolResult:
        lib = self.library
        try:
            book = lib.find(str(params.get("book") or ""))
            voice = str(params.get("voice") or "").strip() or None
            if voice:
                voice = _resolve_voice(voice)
                lib.set_voice(book["id"], voice)
            job = lib.render(
                book["id"],
                chapters=str(params.get("chapters") or ""),
                ahead=int(params.get("ahead") or 0),
                voice=voice,
            )
        except (BookNotFound, ValueError) as exc:
            return _fail("audiobook_render", str(exc))
        if job["mode"] == "rolling":
            what = f"keeping {job['ahead']} chapters ready ahead of the listener"
        elif job["mode"] == "range":
            what = f"{len(job['chapters'])} chapters"
        else:
            what = f"the whole book ({len(job['chapters'])} chapters)"
        return ToolResult(
            tool_name="audiobook_render",
            content=f"Queued {what} of {book['title']} in the {job['voice']} voice. "
            "It renders in the background when Sage is idle.",
        )


@ToolRegistry.register("audiobook_pause")
class AudiobookPauseTool(_BookTool):
    tool_id = "audiobook_pause"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="audiobook_pause",
            description=(
                "Pause or resume MAKING audiobooks (the background rendering), or "
                "cancel what is queued. Not for listening -- use audiobook_play."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["pause", "resume", "cancel"],
                        "description": "pause, resume, or cancel every queued job",
                    }
                },
                "required": ["action"],
            },
            category="media",
        )

    def execute(self, **params: Any) -> ToolResult:
        action = str(params.get("action") or "").lower()
        queue = self.library.queue
        if action == "pause":
            queue.set_paused(True)
            text = "Audiobook rendering is paused."
        elif action == "resume":
            queue.set_paused(False)
            text = "Audiobook rendering will continue when Sage is idle."
        elif action == "cancel":
            text = f"Cancelled {queue.cancel('all')} queued audiobook job(s)."
        else:
            return _fail("audiobook_pause", "action must be pause, resume or cancel")
        return ToolResult(tool_name="audiobook_pause", content=text)


#: Folders a spoken import may read from besides OPENJARVIS_FILE_READ_DIRS:
#: where the user's books actually are (the reference book is in Downloads).
#: Only .pdf/.epub, and the tool reads nothing back to the model but counts.
def _import_roots() -> List[Path]:
    roots = [
        Path(p).resolve()
        for p in os.environ.get("OPENJARVIS_FILE_READ_DIRS", "").split(os.pathsep)
        if p.strip()
    ]
    home = Path.home()
    for name in ("Downloads", "Documents", "Desktop", "OneDrive"):
        roots.append((home / name).resolve())
    roots.append(Path("D:/Downloads"))
    return roots


@ToolRegistry.register("audiobook_import")
class AudiobookImportTool(_BookTool):
    tool_id = "audiobook_import"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="audiobook_import",
            description=(
                "Import a book file (PDF or EPUB) the user owns so it can become an "
                "audiobook: 'make an audiobook of D:\\Downloads\\book.epub'. After "
                "importing, call audiobook_render to start narrating."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Full path to the .pdf or .epub",
                    }
                },
                "required": ["path"],
            },
            category="media",
            timeout_seconds=180.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        raw = str(params.get("path") or "").strip().strip('"')
        if not raw:
            return _fail("audiobook_import", "Give the full path of the PDF or EPUB.")
        path = Path(raw).expanduser()
        if path.suffix.lower() not in (".pdf", ".epub"):
            return _fail("audiobook_import", "Only PDF and EPUB books can be imported.")
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            return _fail("audiobook_import", f"No such file: {raw}")
        if not any(resolved.is_relative_to(root) for root in _import_roots()):
            return _fail(
                "audiobook_import",
                "That folder is not one Sage may read books from (Downloads, "
                "Documents, Desktop, OneDrive). Import it on the Books page instead.",
            )
        try:
            book = self.library.import_file(resolved)
        except ValueError as exc:
            return _fail("audiobook_import", str(exc))
        return ToolResult(
            tool_name="audiobook_import",
            content=(
                f"Imported {book['title']}: {book['narrated_chapters']} chapters to "
                f"narrate, {book['words']:,} words (about "
                f"{book['words'] / 153 / 60:.0f} hours of audio). Nothing is "
                "rendered yet."
            ),
        )


@ToolRegistry.register("audiobook_play")
class AudiobookPlayTool(_BookTool):
    tool_id = "audiobook_play"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="audiobook_play",
            description=(
                "LISTEN to an audiobook on this PC, in the Sage app: 'read me "
                "chapter 4', 'continue the book', 'play the next chapter', 'pause "
                "the audiobook', 'resume it'. Plays chapters that are already made."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "book": _BOOK_PARAM,
                    "chapter": {
                        "type": "string",
                        "description": "A chapter number ('4', '32.1'), 'continue' "
                        "(where the user left off), or 'next'.",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["play", "pause", "resume"],
                        "description": "play (default), pause or resume the player",
                    },
                },
            },
            category="media",
        )

    def execute(self, **params: Any) -> ToolResult:
        action = str(params.get("action") or "play").lower()
        if action in ("pause", "resume"):
            return ToolResult(
                tool_name="audiobook_play",
                content=f"Audiobook {'paused' if action == 'pause' else 'resumed'}.",
                metadata={"audiobook_play": {"action": action}},
            )
        lib = self.library
        try:
            book = lib.find(str(params.get("book") or ""))
            detail = lib.detail(book["id"])
            index, seconds = _pick_chapter(
                book, detail, str(params.get("chapter") or "")
            )
        except (BookNotFound, ValueError) as exc:
            return _fail("audiobook_play", str(exc))
        chapter = next(c for c in detail["chapters"] if c["index"] == index)
        if chapter["state"] != "ready":
            return _fail(
                "audiobook_play",
                f"{chapter['label']} isn't made yet ({chapter['state']}). Offer to "
                "render it with audiobook_render.",
            )
        return ToolResult(
            tool_name="audiobook_play",
            content=f"Playing {chapter['label']} of {book['title']} in the app.",
            metadata={
                "audiobook_play": {
                    "action": "play",
                    "book": book["id"],
                    "title": book["title"],
                    "index": index,
                    "label": chapter["label"],
                    "voice": detail["voice"],
                    "seconds": seconds,
                }
            },
        )


def _pick_chapter(book: Dict[str, Any], detail: Dict[str, Any], which: str) -> tuple:
    which = which.strip().lower()
    position = detail.get("position")
    if which in ("", "continue", "resume"):
        if position:
            return position, float(detail.get("position_seconds") or 0)
        ready = [c for c in detail["chapters"] if c["state"] == "ready"]
        if not ready:
            raise ValueError("No chapter of this book is made yet.")
        return ready[0]["index"], 0.0
    if which == "next":
        order = [c["index"] for c in detail["chapters"] if not c["skip"]]
        if position in order and order.index(position) + 1 < len(order):
            return order[order.index(position) + 1], 0.0
        return order[0], 0.0
    return parse_chapters(book, which.replace("chapter", "").strip())[0], 0.0


def _resolve_voice(name: str) -> str:
    """'Jarvis Turbo' / 'lotm narrator' -> the voice folder name."""
    from openjarvis.core.paths import get_data_dir

    voices = Path(get_data_dir()) / "voices"
    wanted = name.strip().lower().replace(" ", "-")
    folders = (
        [p.name for p in voices.iterdir() if p.is_dir()] if voices.is_dir() else []
    )
    if wanted in folders:
        return wanted
    for folder in folders:
        meta = voices / folder / "meta.json"
        try:
            import json

            label = json.loads(meta.read_text(encoding="utf-8")).get("label", "")
        except (OSError, ValueError):
            label = ""
        if label and label.strip().lower().replace(" ", "-") == wanted:
            return folder
    raise ValueError(
        f"No Sage voice called {name!r} (have: {', '.join(sorted(folders))})"
    )
