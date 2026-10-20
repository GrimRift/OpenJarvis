"""Routes for pairing a phone or iPad (M29); see ``pairing.py``."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from openjarvis.server import pairing

logger = logging.getLogger(__name__)


def create_pairing_router() -> APIRouter:
    router = APIRouter(prefix="/v1/pairing", tags=["pairing"])

    @router.post("/code")
    async def new_code() -> Dict[str, Any]:
        # Behind the API key (AuthMiddleware): only an app that already has
        # the key can make a code that hands it out.
        code, ttl = pairing.codes.issue()
        address = await asyncio.to_thread(pairing.tailnet_address)
        logger.info("Pairing code issued (%ds)", int(ttl))
        return {"code": code, "expires_in": int(ttl), "address": address}

    @router.delete("/code")
    async def cancel_code() -> Dict[str, Any]:
        pairing.codes.cancel()
        return {"ok": True}

    @router.post("/redeem")
    async def redeem(request: Request, body: Dict[str, Any]) -> Dict[str, Any]:
        # Exempt from the API key (auth_middleware): the device asking has no
        # key yet. The code is the credential.
        result = pairing.codes.redeem(str(body.get("code", "")))
        client = request.client.host if request.client else "?"
        if result == "ok":
            logger.info("Pairing code redeemed by %s", client)
            return {"api_key": getattr(request.app.state, "api_key", "") or ""}
        logger.warning("Pairing code refused (%s) from %s", result, client)
        if result == "wrong":
            raise HTTPException(status_code=403, detail="That code is not right.")
        raise HTTPException(
            status_code=410,
            detail=(
                "No pairing code is waiting. "
                "Make a new one in Sage's Settings on the PC."
            ),
        )

    return router
