"""Cartesia WebSocket continuation payload and cancellation contract."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from openjarvis.speech.cartesia_tts import CartesiaTTSContext


class _Socket:
    def __init__(self, messages=()):
        self.sent: list[dict] = []
        self._messages = iter(messages)

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._messages)
        except StopIteration as exc:
            raise StopAsyncIteration from exc

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_one_context_receives_ordered_continuations_and_final_marker() -> None:
    socket = _Socket()
    with patch(
        "openjarvis.speech.cartesia_tts.websockets.connect",
        AsyncMock(return_value=socket),
    ):
        async with CartesiaTTSContext("secret", "voice", context_id="turn-1") as ctx:
            await ctx.send_text("First sentence. ")
            await ctx.send_text("Final tail")
            await ctx.finish()

    assert [item["transcript"] for item in socket.sent] == [
        "First sentence. ",
        "Final tail",
        "",
    ]
    assert [item["continue"] for item in socket.sent] == [True, True, False]
    assert {item["context_id"] for item in socket.sent} == {"turn-1"}


@pytest.mark.asyncio
async def test_cancel_targets_the_context_and_does_not_include_credentials() -> None:
    socket = _Socket()
    connect = AsyncMock(return_value=socket)
    with patch("openjarvis.speech.cartesia_tts.websockets.connect", connect):
        async with CartesiaTTSContext("secret", "voice", context_id="turn-2") as ctx:
            await ctx.cancel()

    assert socket.sent == [{"context_id": "turn-2", "cancel": True}]
    assert "secret" not in json.dumps(socket.sent)
    assert connect.await_args.kwargs["additional_headers"]["X-API-Key"] == "secret"
    assert connect.await_args.kwargs["max_queue"] <= 16
