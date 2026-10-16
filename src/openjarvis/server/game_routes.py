"""The app's game panel: the game on now, and ping pong points.

Moves and clicks go through the chat (``routes._game_reply_stream``), so the
conversation and Sage's voice follow them. Two things do not: reopening the
panel from an old chat card asks here for the game as it stands now, and a
ping pong point is reported here many times a minute, too often for a turn.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class PongPoint(BaseModel):
    #: "user" or "sage".
    winner: str
    #: Hits in the rally, both paddles together.
    rally: int = 0


def create_game_router() -> APIRouter:
    router = APIRouter(prefix="/v1/games", tags=["games"])

    @router.get("/current")
    async def current() -> Dict[str, Any]:
        from openjarvis.tools.play_game import PlayGameTool

        panel = await asyncio.to_thread(PlayGameTool().current)
        return {"game_panel": panel}

    @router.post("/pong/point")
    async def pong_point(point: PongPoint) -> Dict[str, Any]:
        from openjarvis.games import MoveError
        from openjarvis.tools.play_game import PlayGameTool

        try:
            return await asyncio.to_thread(
                PlayGameTool().point, point.winner, point.rally
            )
        except MoveError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    return router


__all__ = ["create_game_router"]
