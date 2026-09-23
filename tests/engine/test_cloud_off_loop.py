"""The cloud stream must not block the event loop (23 September).

Its providers are async generators over synchronous SDK clients: each wait
for a token froze the server. A one-line answer took 3.6 s to its first
word and a 50 ms status request up to 15 s.
"""

from __future__ import annotations

import asyncio
import time

from openjarvis.core.types import Message, Role
from openjarvis.engine._stubs import StreamChunk
from openjarvis.engine.cloud import CloudEngine


def _engine() -> CloudEngine:
    engine = CloudEngine.__new__(CloudEngine)
    return engine


def test_waiting_for_tokens_leaves_the_loop_free() -> None:
    engine = _engine()

    async def slow_provider(messages, **kw):
        for word in ("seventeen ", "times ", "twenty-three"):
            time.sleep(0.2)  # the SDK's blocking read
            yield StreamChunk(content=word)

    engine._stream_full_openai = slow_provider

    async def scenario():
        ticks = 0
        done = False

        async def ticker():
            nonlocal ticks
            while not done:
                ticks += 1
                await asyncio.sleep(0.02)

        tick_task = asyncio.create_task(ticker())
        words = []
        async for chunk in engine.stream_full(
            [Message(role=Role.USER, content="hi")], model="gpt-5.6-luna"
        ):
            words.append(chunk.content)
        done = True
        await tick_task
        return words, ticks

    words, ticks = asyncio.run(scenario())
    assert "".join(words) == "seventeen times twenty-three"
    # 0.6 s of waiting at a tick every 20 ms; blocked, it would be ~1.
    assert ticks > 15


def test_an_error_in_the_stream_reaches_the_caller() -> None:
    engine = _engine()

    async def broken(messages, **kw):
        yield StreamChunk(content="a")
        raise RuntimeError("provider went away")

    engine._stream_full_openai = broken

    async def scenario():
        got = []
        try:
            async for chunk in engine.stream_full(
                [Message(role=Role.USER, content="hi")], model="gpt-5.6-luna"
            ):
                got.append(chunk.content)
        except RuntimeError as exc:
            return got, str(exc)
        return got, None

    got, error = asyncio.run(scenario())
    assert got == ["a"] and error == "provider went away"
