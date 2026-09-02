"""FastAPI routes for the morning digest."""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from openjarvis.agents.digest_store import DigestStore
from openjarvis.cli.digest_cmd import (
    _cancel_scheduler_tasks,
    _create_scheduler_task,
    _save_digest_schedule,
)
from openjarvis.core.config import load_config


class ScheduleUpdate(BaseModel):
    """Request body for updating the digest schedule."""

    enabled: bool
    cron: Optional[str] = None


def create_digest_router(*, db_path: str = "") -> APIRouter:
    """Create a digest API router with the given store path."""
    router = APIRouter(prefix="/api/digest", tags=["digest"])
    store = DigestStore(db_path=db_path) if db_path else DigestStore()

    @router.get("")
    async def get_digest():
        """Return the latest digest artifact."""
        artifact = store.get_today()
        if artifact is None:
            raise HTTPException(status_code=404, detail="No digest for today")
        return {
            "text": artifact.text,
            "sections": artifact.sections,
            "sources_used": artifact.sources_used,
            "generated_at": artifact.generated_at.isoformat(),
            "model_used": artifact.model_used,
            "voice_used": artifact.voice_used,
            "audio_available": (
                artifact.audio_path is not None and artifact.audio_path.exists()
            ),
        }

    @router.get("/audio")
    async def get_digest_audio():
        """Stream the digest audio file."""
        artifact = store.get_today()
        if artifact is None:
            raise HTTPException(status_code=404, detail="No digest for today")
        if artifact.audio_path is None or not artifact.audio_path.exists():
            raise HTTPException(status_code=404, detail="Audio not available")
        return FileResponse(
            str(artifact.audio_path),
            media_type="audio/mpeg",
            filename="digest.mp3",
        )

    @router.post("/generate")
    async def generate_digest():
        """Force re-generation of the digest.

        Offloaded to a thread because the briefing reads Outlook and Teams
        over CDP, and ``CDPSession`` drives its own event loop with
        ``run_until_complete``. Called on the loop thread that raises, the
        pending ``wait_for`` is discarded ("coroutine was never awaited"), and
        ``_collect_browser_sources`` drops a failed source rather than raising
        -- so the endpoint returned a perfectly healthy-looking briefing with
        Outlook, Teams and the assignment deadline silently missing. The chat
        route never had this because it already runs the agent on a worker
        thread; only this endpoint called straight through.
        """

        def _generate() -> str:
            from openjarvis.sdk import Jarvis

            with Jarvis() as j:
                return j.ask("Generate my morning digest", agent="morning_digest")

        try:
            result = await asyncio.to_thread(_generate)
            return {"status": "ok", "text": result}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @router.get("/history")
    async def get_digest_history():
        """Return past digests."""
        history = store.history(limit=10)
        return [
            {
                "text": a.text[:200],
                "generated_at": a.generated_at.isoformat(),
                "model_used": a.model_used,
                "voice_used": a.voice_used,
            }
            for a in history
        ]

    @router.get("/schedule")
    async def get_schedule():
        """Return the current digest schedule configuration."""
        cfg = load_config()
        return {
            "enabled": cfg.digest.enabled,
            "cron": cfg.digest.schedule,
        }

    @router.post("/schedule")
    async def update_schedule(body: ScheduleUpdate):
        """Update the digest schedule configuration."""
        cfg = load_config()
        cron = body.cron if body.cron is not None else cfg.digest.schedule

        try:
            _save_digest_schedule(enabled=body.enabled, cron=cron)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to save config: {exc}",
            )

        # Sync with the TaskScheduler
        if body.enabled:
            _create_scheduler_task(cron)
        else:
            _cancel_scheduler_tasks()

        return {
            "enabled": body.enabled,
            "cron": cron,
        }

    return router
