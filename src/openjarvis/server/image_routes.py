"""HTTP side of M40: serve a generated image, read/write Settings > Images,
and the spend figures the Dashboard shows.

Under ``/v1`` so the API key guards it like everything else. The app fetches
the picture with its key and shows a blob URL; it never opens a ``data:`` URL
(the Tauri shell sends only http/https to the browser).
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from openjarvis.images import settings as image_settings
from openjarvis.images.store import ImageStore


def spend(telemetry_db: str, *, since: float = 0.0) -> Dict[str, Any]:
    """Cloud spend from telemetry.db, with images broken out.

    Rows whose price was unknown were written with cost 0 and
    ``cost_known: false``; they are counted separately so an unknown is never
    read as free.
    """
    empty = {
        "total_usd": 0.0,
        "images_usd": 0.0,
        "image_count": 0,
        "images_cost_unknown": 0,
        "since": since,
    }
    path = Path(telemetry_db)
    if not path.exists():
        return empty
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)
    try:
        total = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM telemetry WHERE timestamp >= ?",
            (since,),
        ).fetchone()[0]
        images = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(cost_usd), 0),"
            " SUM(CASE WHEN metadata LIKE '%\"cost_known\": false%' THEN 1 ELSE 0 END)"
            " FROM telemetry WHERE engine = 'openai-images' AND timestamp >= ?",
            (since,),
        ).fetchone()
    except sqlite3.Error:
        return empty
    finally:
        conn.close()
    return {
        "total_usd": float(total or 0.0),
        "images_usd": float(images[1] or 0.0),
        "image_count": int(images[0] or 0),
        "images_cost_unknown": int(images[2] or 0),
        "since": since,
    }


def create_image_router(
    *,
    store: Optional[ImageStore] = None,
    settings_path: Optional[Path] = None,
    telemetry_db: Optional[str] = None,
) -> APIRouter:
    router = APIRouter(prefix="/v1/images", tags=["images"])
    state: Dict[str, Any] = {"store": store}

    def _store() -> ImageStore:
        if state["store"] is None:
            state["store"] = ImageStore()
        return state["store"]

    @router.get("/settings")
    async def get_settings():
        cfg = image_settings.load(settings_path)
        return {
            **cfg.to_dict(),
            "models": list(image_settings.MODELS),
            "qualities": list(image_settings.QUALITIES),
            "sizes": list(image_settings.SIZES),
        }

    @router.put("/settings")
    async def put_settings(body: Dict[str, Any]):
        try:
            cfg = image_settings.update(body, settings_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return cfg.to_dict()

    @router.get("/spend")
    async def get_spend(days: int = 30):
        db = telemetry_db
        if db is None:
            from openjarvis.core.config import load_config

            db = load_config().telemetry.db_path
        since = time.time() - max(1, days) * 86400
        return await asyncio.to_thread(spend, db, since=since)

    @router.get("/{image_id}")
    async def get_image(image_id: str):
        record = await asyncio.to_thread(_store().get, image_id)
        if record is None:
            raise HTTPException(status_code=404, detail="No such image")
        path = Path(record.path)
        if not path.is_file():
            raise HTTPException(status_code=410, detail="The image file is gone")
        return FileResponse(path, media_type="image/png")

    return router
