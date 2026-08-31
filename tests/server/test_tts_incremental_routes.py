"""End-to-end protocol tests for incremental browser-to-Cartesia speech."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.core.config import JarvisConfig
from openjarvis.server import tts_stream_routes


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(tts_stream_routes.router)
    app.state.config = JarvisConfig()
    app.state.api_key = ""
    return app


class _FakeCartesiaContext:
    instances: list["_FakeCartesiaContext"] = []

    def __init__(self, *args, **kwargs) -> None:
        self.segments: list[str] = []
        self.cancelled = False
        self.closed = False
        self._audio: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.__class__.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args) -> None:
        self.closed = True

    async def send_text(self, text: str) -> None:
        self.segments.append(text)
        await self._audio.put(text.encode())

    async def finish(self) -> None:
        await self._audio.put(None)

    async def cancel(self) -> None:
        self.cancelled = True
        await self._audio.put(None)

    async def receive_audio(self):
        while True:
            chunk = await self._audio.get()
            if chunk is None:
                return
            yield chunk


class _FailingCartesiaContext(_FakeCartesiaContext):
    fail_after_segments = 0

    async def send_text(self, text: str) -> None:
        if len(self.segments) >= self.fail_after_segments:
            raise RuntimeError("synthetic Cartesia failure")
        await super().send_text(text)


class _SlowFinishCartesiaContext(_FakeCartesiaContext):
    async def finish(self) -> None:
        # Keep audio generation open until the browser explicitly stops it.
        return None


def _connect(client: TestClient):
    return client.websocket_connect("/v1/speech/tts-stream")


def test_audio_starts_after_a_sentence_before_model_text_finishes() -> None:
    _FakeCartesiaContext.instances.clear()
    with (
        patch.dict("os.environ", {"CARTESIA_API_KEY": "k"}, clear=False),
        patch.object(tts_stream_routes, "CartesiaTTSContext", _FakeCartesiaContext),
        TestClient(_app()) as client,
        _connect(client) as ws,
    ):
        ws.send_json({"type": "begin", "voice_id": "voice"})
        assert ws.receive_json()["type"] == "ready"

        ws.send_json({"type": "text", "delta": "First sentence. Later"})
        assert ws.receive_json()["type"] == "start"
        assert ws.receive_bytes() == b"First sentence. "
        assert _FakeCartesiaContext.instances[0].segments == ["First sentence. "]

        ws.send_json({"type": "text", "delta": " tail"})
        ws.send_json({"type": "finish"})
        assert ws.receive_bytes() == b"Later tail"
        assert ws.receive_json()["type"] == "done"


def test_stop_cancels_context_and_disconnect_cleans_up() -> None:
    _FakeCartesiaContext.instances.clear()
    with (
        patch.dict("os.environ", {"CARTESIA_API_KEY": "k"}, clear=False),
        patch.object(tts_stream_routes, "CartesiaTTSContext", _FakeCartesiaContext),
        TestClient(_app()) as client,
    ):
        with _connect(client) as ws:
            ws.send_json({"type": "begin", "voice_id": "voice"})
            assert ws.receive_json()["type"] == "ready"
            ws.send_json({"type": "cancel"})
            assert ws.receive_json()["type"] == "cancelled"
        assert _FakeCartesiaContext.instances[-1].cancelled is True
        assert _FakeCartesiaContext.instances[-1].closed is True

        with _connect(client) as ws:
            ws.send_json({"type": "begin", "voice_id": "voice"})
            assert ws.receive_json()["type"] == "ready"
        assert _FakeCartesiaContext.instances[-1].cancelled is True
        assert _FakeCartesiaContext.instances[-1].closed is True


def test_stop_still_cancels_after_model_text_has_finished() -> None:
    _SlowFinishCartesiaContext.instances.clear()
    with (
        patch.dict("os.environ", {"CARTESIA_API_KEY": "k"}, clear=False),
        patch.object(
            tts_stream_routes,
            "CartesiaTTSContext",
            _SlowFinishCartesiaContext,
        ),
        TestClient(_app()) as client,
        _connect(client) as ws,
    ):
        ws.send_json({"type": "begin", "voice_id": "voice"})
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "text", "delta": "Audio starts. "})
        assert ws.receive_json()["type"] == "start"
        ws.receive_bytes()
        ws.send_json({"type": "finish"})
        ws.send_json({"type": "cancel"})
        assert ws.receive_json()["type"] == "cancelled"

    assert _SlowFinishCartesiaContext.instances[0].cancelled is True
    assert _SlowFinishCartesiaContext.instances[0].closed is True

def test_turn_limit_fails_closed_before_unbounded_buffering() -> None:
    _FakeCartesiaContext.instances.clear()
    with (
        patch.dict("os.environ", {"CARTESIA_API_KEY": "k"}, clear=False),
        patch.object(tts_stream_routes, "CartesiaTTSContext", _FakeCartesiaContext),
        TestClient(_app()) as client,
        _connect(client) as ws,
    ):
        ws.send_json({"type": "begin", "voice_id": "voice"})
        assert ws.receive_json()["type"] == "ready"
        ws.send_json(
            {"type": "text", "delta": "x" * (tts_stream_routes.MAX_TURN_CHARS + 1)}
        )
        error = ws.receive_json()

    assert error["type"] == "error"
    assert error["started"] is False
    assert _FakeCartesiaContext.instances[0].cancelled is True
    assert tts_stream_routes.MAX_PENDING_SEGMENTS <= 16


def test_pre_audio_failure_is_recoverable_but_post_audio_failure_is_not() -> None:
    _FailingCartesiaContext.instances.clear()
    _FailingCartesiaContext.fail_after_segments = 0
    with (
        patch.dict("os.environ", {"CARTESIA_API_KEY": "k"}, clear=False),
        patch.object(tts_stream_routes, "CartesiaTTSContext", _FailingCartesiaContext),
        TestClient(_app()) as client,
        _connect(client) as ws,
    ):
        ws.send_json({"type": "begin", "voice_id": "voice"})
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "text", "delta": "Fails before audio. "})
        error = ws.receive_json()
    assert error["type"] == "error"
    assert error["started"] is False

    _FailingCartesiaContext.instances.clear()
    _FailingCartesiaContext.fail_after_segments = 1
    with (
        patch.dict("os.environ", {"CARTESIA_API_KEY": "k"}, clear=False),
        patch.object(tts_stream_routes, "CartesiaTTSContext", _FailingCartesiaContext),
        TestClient(_app()) as client,
        _connect(client) as ws,
    ):
        ws.send_json({"type": "begin", "voice_id": "voice"})
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "text", "delta": "Audio starts. "})
        assert ws.receive_json()["type"] == "start"
        ws.receive_bytes()
        ws.send_json({"type": "text", "delta": "Then it fails. "})
        error = ws.receive_json()
    assert error["type"] == "error"
    assert error["started"] is True
