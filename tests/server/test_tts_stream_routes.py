"""Tests for the streaming TTS relay — auth, key containment, and the
fallback contract the browser depends on."""

from __future__ import annotations

import ast
import inspect
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.core.config import JarvisConfig
from openjarvis.server import tts_stream_routes


def _app(api_key: str = "") -> FastAPI:
    app = FastAPI()
    app.include_router(tts_stream_routes.router)
    app.state.config = JarvisConfig()
    app.state.api_key = api_key
    return app


async def _chunks(*payloads: bytes):
    for payload in payloads:
        yield payload


class TestKeyContainment:
    """CARTESIA_API_KEY must never reach the browser — the whole reason this
    relay exists rather than the page calling Cartesia directly."""

    def test_no_send_call_passes_the_key(self):
        """Asserts on the parsed calls, not on a substring of the file: an
        earlier version of this test matched the parameter name in a function
        signature and would have passed on a real leak."""
        tree = ast.parse(inspect.getsource(tts_stream_routes))
        sends = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"send_json", "send_bytes", "send_text"}
        ]
        assert sends, "expected the relay to send something"
        for call in sends:
            names = {sub.id for sub in ast.walk(call) if isinstance(sub, ast.Name)}
            assert "api_key" not in names, ast.dump(call)[:200]

    def test_missing_key_reports_an_error_the_client_can_recover_from(self):
        client = TestClient(_app())
        with patch.dict("os.environ", {"CARTESIA_API_KEY": ""}, clear=False):
            with client.websocket_connect("/v1/speech/tts-stream") as ws:
                msg = ws.receive_json()
        assert msg["type"] == "error"
        # The reason names the variable, never its value.
        assert "CARTESIA_API_KEY" in msg["reason"]


class TestAuth:
    def test_unauthorized_socket_is_closed(self):
        client = TestClient(_app(api_key="secret"))
        try:
            with client.websocket_connect("/v1/speech/tts-stream"):
                pass
        except Exception:
            return  # closed before accept, which is the point
        raise AssertionError("expected the socket to be refused")


class TestRequestGuards:
    def _client(self):
        return TestClient(_app())

    def test_empty_text_is_refused_without_calling_cartesia(self):
        with patch.dict("os.environ", {"CARTESIA_API_KEY": "k"}, clear=False):
            with patch.object(tts_stream_routes, "_speak") as speak:
                with self._client().websocket_connect("/v1/speech/tts-stream") as ws:
                    ws.send_json({"text": "   "})
                    msg = ws.receive_json()
        assert msg["type"] == "error"
        speak.assert_not_called()

    def test_absurdly_long_text_is_refused_rather_than_truncated(self):
        """A half-spoken answer is worse than a clear failure."""
        with patch.dict("os.environ", {"CARTESIA_API_KEY": "k"}, clear=False):
            with patch.object(tts_stream_routes, "_speak") as speak:
                with self._client().websocket_connect("/v1/speech/tts-stream") as ws:
                    ws.send_json({"text": "a" * (tts_stream_routes.MAX_TEXT_CHARS + 1)})
                    msg = ws.receive_json()
        assert msg["type"] == "error"
        speak.assert_not_called()


class TestStreaming:
    def _client(self):
        return TestClient(_app())

    def test_audio_is_relayed_as_binary_after_a_start_frame(self):
        with (
            patch.dict("os.environ", {"CARTESIA_API_KEY": "k"}, clear=False),
            patch.object(
                tts_stream_routes,
                "_speak",
                new=tts_stream_routes._speak,
            ),
            patch(
                "openjarvis.speech.cartesia_tts.astream_pcm",
                lambda *a, **k: _chunks(b"\x00" * 8, b"\x01" * 8),
            ),
        ):
            with self._client().websocket_connect("/v1/speech/tts-stream") as ws:
                ws.send_json({"text": "hello"})
                start = ws.receive_json()
                first = ws.receive_bytes()
                second = ws.receive_bytes()
                done = ws.receive_json()

        assert start["type"] == "start"
        assert start["sample_rate"] == 24000
        assert start["encoding"] == "pcm_f32le"
        assert first == b"\x00" * 8
        assert second == b"\x01" * 8
        assert done["type"] == "done"

    def test_selected_voice_tuning_is_forwarded_to_cartesia(self):
        captured = {}

        def stream(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return _chunks(b"\x00" * 8)

        with (
            patch.dict("os.environ", {"CARTESIA_API_KEY": "k"}, clear=False),
            patch("openjarvis.speech.cartesia_tts.astream_pcm", stream),
        ):
            with self._client().websocket_connect("/v1/speech/tts-stream") as ws:
                ws.send_json(
                    {
                        "text": "hello",
                        "voice_id": "frieren-id",
                        "speed": 0.9,
                        "volume": 1.9,
                    }
                )
                ws.receive_json()
                ws.receive_bytes()
                ws.receive_json()

        assert captured["args"][2] == "frieren-id"
        assert captured["kwargs"] == {"speed": 0.9, "volume": 1.9}

    def test_a_failure_before_any_audio_is_flagged_recoverable(self):
        """started=False tells the browser it may fall back to the batch
        endpoint; nothing was heard, so nothing would be repeated."""

        async def _boom(*a, **k):
            raise RuntimeError("cartesia down")
            yield b""  # pragma: no cover

        with (
            patch.dict("os.environ", {"CARTESIA_API_KEY": "k"}, clear=False),
            patch("openjarvis.speech.cartesia_tts.astream_pcm", _boom),
        ):
            with self._client().websocket_connect("/v1/speech/tts-stream") as ws:
                ws.send_json({"text": "hello"})
                msg = ws.receive_json()

        assert msg["type"] == "error"
        assert msg["started"] is False

    def test_a_failure_after_audio_is_flagged_not_recoverable(self):
        """Falling back here would speak the opening of the reply twice."""

        async def _half(*a, **k):
            yield b"\x00" * 8
            raise RuntimeError("dropped mid-stream")

        with (
            patch.dict("os.environ", {"CARTESIA_API_KEY": "k"}, clear=False),
            patch("openjarvis.speech.cartesia_tts.astream_pcm", _half),
        ):
            with self._client().websocket_connect("/v1/speech/tts-stream") as ws:
                ws.send_json({"text": "hello"})
                ws.receive_json()  # start
                ws.receive_bytes()
                msg = ws.receive_json()

        assert msg["type"] == "error"
        assert msg["started"] is True


class TestDefaults:
    def test_voice_falls_back_to_the_configured_then_default_voice(self):
        cfg = JarvisConfig()
        assert tts_stream_routes._resolve_voice(cfg, "explicit") == "explicit"
        resolved = tts_stream_routes._resolve_voice(cfg, "")
        assert resolved  # never empty, or Cartesia rejects the request

    def test_speed_ignores_nonsense_values(self):
        cfg = JarvisConfig()
        assert tts_stream_routes._resolve_speed(cfg, 1.5) == 1.5
        assert tts_stream_routes._resolve_speed(cfg, 0) > 0
        assert tts_stream_routes._resolve_speed(cfg, None) > 0
        assert tts_stream_routes._resolve_speed(cfg, "fast") > 0

    def test_volume_ignores_nonsense_values(self):
        cfg = JarvisConfig()
        assert tts_stream_routes._resolve_volume(cfg, 1.9) == 1.9
        assert tts_stream_routes._resolve_volume(cfg, 0) > 0
        assert tts_stream_routes._resolve_volume(cfg, None) > 0
        assert tts_stream_routes._resolve_volume(cfg, "loud") > 0
