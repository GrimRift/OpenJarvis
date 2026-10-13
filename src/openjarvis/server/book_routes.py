"""HTTP side of M42: the Books page.

Under ``/v1`` so the API key guards it like everything else. Everything goes
through ``audiobooks.library`` -- the same layer Sage's chat tools use -- so
the page and the voice never disagree about a book. Chapter audio is served
as a file (Starlette answers Range requests, so the player can seek).
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from openjarvis.audiobooks import export
from openjarvis.audiobooks.library import Library

#: Largest book file the page accepts (the reference PDF is 12 MB).
MAX_UPLOAD_BYTES = 300 * 1024 * 1024


def create_book_router(
    library_factory: Callable[[], Library] = Library,
) -> APIRouter:
    router = APIRouter(prefix="/v1/books", tags=["books"])

    def lib() -> Library:
        return library_factory()

    def _book_or_404(book_id: str) -> None:
        try:
            lib().store.load(book_id)
        except (FileNotFoundError, ValueError):
            raise HTTPException(404, f"No book {book_id!r}") from None

    @router.get("")
    async def list_books() -> Dict[str, Any]:
        library = lib()
        return {"books": library.summaries(), "worker": library.worker_status()}

    async def _save_upload(library: Library, file: UploadFile) -> Path:
        """A PDF or EPUB from the page, written beside the store and kept
        (a book records its source path)."""
        name = Path(file.filename or "book").name
        suffix = Path(name).suffix.lower()
        if suffix not in (".pdf", ".epub"):
            raise HTTPException(400, "Only PDF and EPUB books can be imported")
        data = await file.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "That file is too large")
        folder = library.store.root / "_sources"
        folder.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w.\- ]+", "_", Path(name).stem)[:120] or "book"
        target = folder / f"{safe}{suffix}"
        with tempfile.NamedTemporaryFile(
            dir=folder, suffix=suffix, delete=False
        ) as tmp:
            tmp.write(data)
        Path(tmp.name).replace(target)
        return target

    @router.post("/import")
    async def import_upload(file: UploadFile = File(...)) -> Dict[str, Any]:
        """A PDF or EPUB from the page, imported as a book."""
        library = lib()
        target = await _save_upload(library, file)
        try:
            book = await asyncio.to_thread(library.import_file, target)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return library.detail(book["id"])

    @router.get("/queue")
    async def queue_state() -> Dict[str, Any]:
        library = lib()
        return {**library.queue.load(), "worker": library.worker_status()}

    @router.put("/queue")
    async def queue_settings(body: Dict[str, Any]) -> Dict[str, Any]:
        """``paused``, ``speed`` (0.1-1), ``break_minutes`` -- any of them."""
        library = lib()
        try:
            if "paused" in body:
                library.queue.set_paused(bool(body["paused"]))
            if "speed" in body:
                library.queue.set_speed(float(body["speed"]))
            if "break_minutes" in body:
                library.queue.set_break(float(body["break_minutes"]))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return library.queue.load()

    @router.delete("/queue/jobs/{job_id}")
    async def cancel_job(job_id: str) -> Dict[str, Any]:
        return {"cancelled": lib().queue.cancel(job_id)}

    @router.get("/{book_id}")
    async def book_detail(book_id: str) -> Dict[str, Any]:
        _book_or_404(book_id)
        library = lib()
        return {
            **library.detail(book_id),
            "delivery": export.delivery(book_id, store=library.store),
        }

    @router.post("/{book_id}/export")
    async def export_parts(book_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """M4B parts to the Audiobooks folder + OneDrive. ``parts`` (list of
        numbers, default all), ``partial`` (default true: what is made so
        far), ``force``."""
        _book_or_404(book_id)
        store = lib().store
        try:
            written = await asyncio.to_thread(
                export.export_parts,
                book_id,
                None,
                body.get("parts") or None,
                partial=bool(body.get("partial", True)),
                force=bool(body.get("force", False)),
                store=store,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "written": [str(p) for p in written],
            "delivery": export.delivery(book_id, store=store),
        }

    @router.post("/{book_id}/send")
    async def send_to_phone(book_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Chapters to the phone by Telegram, in the background: ``chapters``
        ("5-10", book numbering) or ``indexes`` (list)."""
        _book_or_404(book_id)
        store = lib().store
        chapters = body.get("indexes") or str(body.get("chapters") or "").strip()
        if not chapters:
            raise HTTPException(400, "Say which chapters to send")
        try:
            status = export.start_send(book_id, None, chapters, store=store)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"sending": status}

    @router.get("/{book_id}/cover")
    async def book_cover(book_id: str):
        _book_or_404(book_id)
        try:
            path = lib().cover(book_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        return FileResponse(path)

    @router.get("/{book_id}/chapters/{index}/text")
    async def chapter_text(book_id: str, index: int) -> Dict[str, Any]:
        _book_or_404(book_id)
        try:
            return lib().chapter_text(book_id, index)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.get("/{book_id}/chapters/{index}/audio")
    async def chapter_audio(book_id: str, index: int, voice: Optional[str] = None):
        _book_or_404(book_id)
        try:
            path = lib().chapter_audio(book_id, index, voice)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(404, str(exc)) from exc
        return FileResponse(path, media_type="audio/mp4")

    @router.put("/{book_id}/voice")
    async def set_voice(book_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        _book_or_404(book_id)
        library = lib()
        try:
            library.set_voice(book_id, str(body.get("voice") or ""))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return library.detail(book_id)

    @router.post("/{book_id}/render")
    async def render(book_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """``chapters`` ("40-60"), or ``ahead`` (keep N ready), or neither
        (the whole book); optional ``voice``."""
        _book_or_404(book_id)
        library = lib()
        try:
            job = library.render(
                book_id,
                chapters=str(body.get("chapters") or ""),
                ahead=int(body.get("ahead") or 0),
                voice=body.get("voice") or None,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"job": job, "book": library.detail(book_id)}

    @router.put("/{book_id}/position")
    async def position(book_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        _book_or_404(book_id)
        try:
            book = lib().set_position(
                book_id, int(body.get("index") or 0), float(body.get("seconds") or 0)
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "position": book.get("position"),
            "position_seconds": book.get("position_seconds"),
        }

    # ------------------------------------------- flags (user, 2026-10-14)

    @router.get("/{book_id}/chapters/{index}/flags")
    async def chapter_flags(book_id: str, index: int) -> Dict[str, Any]:
        _book_or_404(book_id)
        return {"flags": lib().chapter_flags(book_id, index)}

    @router.delete("/{book_id}/chapters/{index}/flags/{piece}")
    async def dismiss_flag(book_id: str, index: int, piece: int) -> Dict[str, Any]:
        """The user listened and the line is fine."""
        _book_or_404(book_id)
        try:
            left = lib().dismiss_flag(book_id, index, piece)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"flags": left}

    @router.post("/{book_id}/chapters/{index}/redo")
    async def redo_chapter(book_id: str, index: int) -> Dict[str, Any]:
        """Render the chapter again; the old file is kept in replaced/."""
        _book_or_404(book_id)
        library = lib()
        try:
            job = library.redo_chapter(book_id, index)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"job": job, "book": library.detail(book_id)}

    # ------------------------------------ adding chapters (user, 2026-10-14)

    @router.post("/{book_id}/chapters/from-file")
    async def add_from_file(
        book_id: str, file: UploadFile = File(...)
    ) -> Dict[str, Any]:
        """A newer PDF/EPUB of the book: only chapters it does not have."""
        _book_or_404(book_id)
        library = lib()
        target = await _save_upload(library, file)
        try:
            result = await asyncio.to_thread(
                library.add_chapters_from_file, book_id, target
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {**result, "book": library.detail(book_id)}

    @router.post("/{book_id}/chapters")
    async def add_chapter(book_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """One chapter from text: ``number``, ``title`` (optional), ``text``."""
        _book_or_404(book_id)
        library = lib()
        try:
            entry = library.add_chapter_text(
                book_id,
                str(body.get("number") or ""),
                str(body.get("text") or ""),
                str(body.get("title") or ""),
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"added": entry, "book": library.detail(book_id)}

    @router.post("/{book_id}/chapters/fetch")
    async def fetch_chapter(book_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """A chapter's text from a web page the user pasted, to check in the
        paste form before adding (nothing is saved here)."""
        _book_or_404(book_id)
        url = str(body.get("url") or "").strip()
        if not url:
            raise HTTPException(400, "Paste a link to the chapter")
        from openjarvis.audiobooks.additions import fetch_page

        try:
            return await asyncio.to_thread(fetch_page, url)
        except Exception as exc:  # network, bot wall, Opera missing
            raise HTTPException(400, f"Could not read that page: {exc}") from exc

    return router
