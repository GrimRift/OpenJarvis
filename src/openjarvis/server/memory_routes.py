"""The Memory page's API (M38): facts, episodes, documents, profile.

Everything Sage remembers about the user, addressable. Facts are the
`LocalFactStore` the extraction service writes; episodes the nightly diary;
documents the retrieval backend; the profile `USER.md`. Search on facts
uses the same ranking recall does, so what the page shows is what the
model can reach.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile

from openjarvis.core.config import DEFAULT_CONFIG_DIR

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/memory", tags=["memory"])

# Auto-extracted facts count as "new" -- shown with a one-click reject --
# for this long. They are used from the moment they are stored.
PENDING_SECONDS = 24 * 3600


def _facts(request: Request):
    service = getattr(request.app.state, "memory_service", None)
    store = getattr(service, "_store", None) if service is not None else None
    if store is None:
        from openjarvis.memory.store import LocalFactStore

        store = LocalFactStore()
    return store


def _fact_dict(fact: Any, score: Optional[float] = None) -> Dict[str, Any]:
    now = time.time()
    data = {
        "id": fact.id,
        "text": fact.text,
        "source": fact.source or "auto",
        "trust": fact.trust,
        "created_at": fact.created_at,
        "day": fact.day,
        "pinned": fact.pinned,
        "private": fact.private,
        "pending": (fact.source or "auto") == "auto"
        and now - fact.created_at < PENDING_SECONDS,
        "removed_at": fact.removed_at,
        "removed_reason": fact.removed_reason,
    }
    if score is not None:
        data["score"] = score
    return data


# -- facts -------------------------------------------------------------------


@router.get("/facts")
async def list_facts(request: Request, q: str = "", removed: bool = False):
    """Facts newest first, or ranked by *q* the way recall ranks them."""
    from openjarvis.memory.recall import score_facts

    store = _facts(request)
    if removed:
        return {"facts": [_fact_dict(f) for f in reversed(store.list_removed())]}
    facts = store.list()
    if q.strip():
        scores = score_facts(facts, q)
        ranked = sorted(
            zip(facts, scores), key=lambda p: (p[1], p[0].created_at), reverse=True
        )
        return {"facts": [_fact_dict(f, s) for f, s in ranked if s > 0]}
    return {"facts": [_fact_dict(f) for f in reversed(facts)]}


@router.post("/facts")
async def add_fact(request: Request):
    body = await request.json()
    text = str((body or {}).get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    store = _facts(request)
    from openjarvis.memory.store import TRUST_TRUSTED

    added = store.add(
        text,
        source="you",
        trust=TRUST_TRUSTED,
        pinned=bool(body.get("pinned", False)),
        private=bool(body.get("private", False)),
    )
    if not added:
        raise HTTPException(status_code=409, detail="Sage already remembers that")
    fact = next((f for f in reversed(store.list()) if f.text == text), None)
    return {"fact": _fact_dict(fact) if fact else None}


@router.put("/facts/{fact_id}")
async def update_fact(fact_id: str, request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object")
    store = _facts(request)
    text = body.get("text")
    if text is not None and not str(text).strip():
        raise HTTPException(status_code=400, detail="text cannot be empty")
    for key in ("pinned", "private"):
        if key in body and not isinstance(body[key], bool):
            raise HTTPException(status_code=400, detail=f"{key} must be a boolean")
    fact = store.update(
        fact_id,
        text=str(text).strip() if text is not None else None,
        pinned=body.get("pinned"),
        private=body.get("private"),
        source="you" if text is not None else None,
    )
    if fact is None:
        raise HTTPException(status_code=404, detail="No such fact")
    return {"fact": _fact_dict(fact)}


@router.delete("/facts/{fact_id}")
async def delete_fact(fact_id: str, request: Request):
    if not _facts(request).remove(fact_id, "removed on the Memory page"):
        raise HTTPException(status_code=404, detail="No such fact")
    return {"removed": fact_id}


@router.post("/facts/{fact_id}/restore")
async def restore_fact(fact_id: str, request: Request):
    if not _facts(request).restore(fact_id):
        raise HTTPException(status_code=404, detail="Nothing to restore")
    return {"restored": fact_id}


# -- episodes ----------------------------------------------------------------


@router.get("/episodes")
async def list_episodes():
    from openjarvis.memory.episodes import load_episodes

    episodes = sorted(load_episodes().values(), key=lambda e: e.day, reverse=True)
    return {"episodes": [e.to_dict() for e in episodes]}


@router.put("/episodes/{day}")
async def update_episode(day: str, request: Request):
    from openjarvis.memory.episodes import load_episodes, save_episode

    body = await request.json()
    summary = str((body or {}).get("summary") or "").strip()
    if not summary:
        raise HTTPException(status_code=400, detail="summary is required")
    episode = load_episodes().get(day)
    if episode is None:
        raise HTTPException(status_code=404, detail="No episode for that day")
    episode.summary = summary
    episode.model = "you"
    save_episode(episode)
    return {"episode": episode.to_dict()}


@router.delete("/episodes/{day}")
async def delete_episode(day: str):
    from openjarvis.memory.episodes import episodes_path, load_episodes

    episodes = load_episodes()
    if day not in episodes:
        raise HTTPException(status_code=404, detail="No episode for that day")
    del episodes[day]
    path = episodes_path()
    path.write_text(
        json.dumps({d: e.to_dict() for d, e in sorted(episodes.items())}, indent=2),
        encoding="utf-8",
    )
    return {"removed": day}


@router.post("/episodes/{day}/rewrite")
async def rewrite_episode(day: str, request: Request):
    """Write the day's episode again from its turns, through the same agent
    the nightly job uses."""
    system = getattr(request.app.state, "system", None) or getattr(
        request.app.state, "scheduler_system", None
    )
    if system is None:
        raise HTTPException(status_code=503, detail="No agent system to write with")
    try:
        result = await asyncio.to_thread(
            system.ask,
            f"Write Sage's diary entry for {day}",
            agent="episode_writer",
            tools=None,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    from openjarvis.memory.episodes import load_episodes

    episode = load_episodes().get(day)
    return {
        "episode": episode.to_dict() if episode else None,
        "result": str(getattr(result, "content", result))[:500],
    }


# -- profile -----------------------------------------------------------------


def _profile_path() -> Path:
    return DEFAULT_CONFIG_DIR / "USER.md"


@router.get("/profile")
async def get_profile():
    try:
        return {"text": _profile_path().read_text(encoding="utf-8")}
    except OSError:
        return {"text": ""}


@router.put("/profile")
async def put_profile(request: Request):
    body = await request.json()
    text = (body or {}).get("text")
    if not isinstance(text, str):
        raise HTTPException(status_code=400, detail="text must be a string")
    path = _profile_path()
    backup = path.with_suffix(".md.bak")
    try:
        if path.exists():
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"saved": True}


# -- settings and hygiene ----------------------------------------------------


@router.get("/settings")
async def get_memory_settings():
    from openjarvis.memory.settings import load_memory_settings

    return load_memory_settings().to_dict()


@router.put("/settings")
async def put_memory_settings(request: Request):
    from openjarvis.memory.settings import (
        EXTRACTION_MODES,
        load_memory_settings,
        save_memory_settings,
    )

    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object")
    settings = load_memory_settings()
    if "extraction_mode" in body:
        if body["extraction_mode"] not in EXTRACTION_MODES:
            raise HTTPException(
                status_code=400,
                detail=f"extraction_mode must be one of {EXTRACTION_MODES}",
            )
        settings.extraction_mode = body["extraction_mode"]
    if "hygiene_enabled" in body:
        if not isinstance(body["hygiene_enabled"], bool):
            raise HTTPException(
                status_code=400, detail="hygiene_enabled must be a boolean"
            )
        settings.hygiene_enabled = body["hygiene_enabled"]
    save_memory_settings(settings)
    return settings.to_dict()


@router.get("/hygiene")
async def hygiene_runs():
    from openjarvis.memory.hygiene import load_runs

    return {"runs": list(reversed(load_runs()))[:7]}


@router.post("/hygiene/run")
async def hygiene_run_now(request: Request):
    system = getattr(request.app.state, "system", None) or getattr(
        request.app.state, "scheduler_system", None
    )
    if system is None:
        raise HTTPException(status_code=503, detail="No agent system to run with")
    try:
        result = await asyncio.to_thread(
            system.ask, "Clean up Sage's memory.", agent="memory_hygiene", tools=None
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"result": str(getattr(result, "content", result))[:500]}


# -- documents ---------------------------------------------------------------


def _documents_db() -> Path:
    return DEFAULT_CONFIG_DIR / "memory.db"


@router.get("/documents")
async def list_documents():
    """Indexed documents grouped by source (file or paste), with chunk counts."""
    path = _documents_db()
    if not path.exists():
        return {"documents": []}
    try:
        db = sqlite3.connect(str(path))
        rows = db.execute(
            "select source, count(*), min(created_at), substr(min(content),1,160) "
            "from documents group by source order by min(created_at) desc"
        ).fetchall()
        db.close()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "documents": [
            {
                "source": source or "(pasted text)",
                "chunks": chunks,
                "created_julian": created,
                "preview": preview,
            }
            for source, chunks, created, preview in rows
        ]
    }


@router.delete("/documents")
async def delete_document(request: Request, source: str):
    backend = getattr(request.app.state, "memory_backend", None)
    if backend is None:
        raise HTTPException(status_code=503, detail="No memory backend configured")
    path = _documents_db()
    ids: List[str] = []
    try:
        db = sqlite3.connect(str(path))
        ids = [
            r[0]
            for r in db.execute("select id from documents where source = ?", (source,))
        ]
        db.close()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    removed = sum(1 for doc_id in ids if backend.delete(doc_id))
    return {"removed": removed, "source": source}


@router.post("/documents/upload")
async def upload_document(request: Request, file: UploadFile):
    """Index one uploaded file through the existing document readers."""
    backend = getattr(request.app.state, "memory_backend", None)
    if backend is None:
        raise HTTPException(status_code=503, detail="No memory backend configured")
    from openjarvis.server.upload_router import _chunk_text, read_document

    name = file.filename or "upload"
    ext = Path(name).suffix.lower()
    if ext not in (".txt", ".md", ".csv", ".pdf", ".docx"):
        raise HTTPException(status_code=400, detail=f"Unsupported file type {ext!r}")
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (20 MB limit)")
    text, note, _reread = read_document(data, ext)
    if not text.strip():
        raise HTTPException(status_code=400, detail="No readable text in that file")
    chunks = _chunk_text(text)
    for index, chunk in enumerate(chunks):
        backend.store(chunk, source=name, metadata={"index": index, "upload": True})
    return {"source": name, "chunks": len(chunks), "note": note}


__all__ = ["router"]
