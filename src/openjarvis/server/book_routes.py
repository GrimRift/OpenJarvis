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

    @router.post("/import")
    async def import_upload(file: UploadFile = File(...)) -> Dict[str, Any]:
        """A PDF or EPUB from the page: written beside the store, imported,
        kept (the book records its source path)."""
        name = Path(file.filename or "book").name
        suffix = Path(name).suffix.lower()
        if suffix not in (".pdf", ".epub"):
            raise HTTPException(400, "Only PDF and EPUB books can be imported")
        data = await file.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "That file is too large")
        library = lib()
        folder = library.store.root / "_sources"
        folder.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w.\- ]+", "_", Path(name).stem)[:120] or "book"
        target = folder / f"{safe}{suffix}"
        with tempfile.NamedTemporaryFile(
            dir=folder, suffix=suffix, delete=False
        ) as tmp:
            tmp.write(data)
        Path(tmp.name).replace(target)
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
        return lib().detail(book_id)

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

    return router
