"""The system panel's live feed and its End button (M41).

``GET /v1/system/stats`` is polled about once a second while the panel or
the Dashboard tile is on screen. ``POST /v1/system/end`` ends one program,
and only the panel's Confirm button calls it; Sage's own programs and
Windows' are refused here whatever the request says.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from openjarvis.core.machine import machine, summarize


def create_system_router() -> APIRouter:
    router = APIRouter(prefix="/v1/system", tags=["system"])
    # The first reading names every process (~2-3 s); take it at start-up so
    # the first "system status" does not pay for it.
    threading.Thread(
        target=lambda: machine().snapshot(), name="machine-warm", daemon=True
    ).start()

    @router.get("/stats")
    async def stats(part: str = ""):
        snap = await asyncio.to_thread(machine().snapshot)
        return {**snap, "summary": summarize(snap, part or None)}

    @router.post("/end")
    async def end(body: Dict[str, Any]):
        app = str((body or {}).get("app") or "").strip()
        if not app:
            raise HTTPException(status_code=400, detail="Which program?")
        try:
            return await asyncio.to_thread(machine().end, app)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return router
