"""Settings > Tools > Search engine: which engine web search uses.

Kept on the server, like Settings > Images: a voice or Telegram turn never
passes through the browser, so the choice cannot ride the chat request.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from openjarvis.tools import search_engines


def create_search_router(*, settings_path: Optional[Path] = None) -> APIRouter:
    router = APIRouter(prefix="/v1/search", tags=["search"])

    @router.get("/settings")
    async def get_settings():
        return search_engines.status(settings_path)

    @router.put("/settings")
    async def put_settings(body: Dict[str, Any]):
        try:
            search_engines.save_engine(str(body.get("engine") or ""), settings_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # A cached answer would still be the old engine's.
        from openjarvis.tools.web_search import clear_cache

        clear_cache()
        return search_engines.status(settings_path)

    return router
