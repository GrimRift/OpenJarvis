"""Tests for the Flux proxy — auth, fail-closed behaviour, and key containment."""

from __future__ import annotations

import ast
import inspect
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.core.config import JarvisConfig
from openjarvis.server import flux_routes


def _app(config: JarvisConfig, api_key: str = "") -> FastAPI:
    app = FastAPI()
    app.include_router(flux_routes.router)
    app.state.config = config
    app.state.api_key = api_key
    return app


def _enabled_config(**over) -> JarvisConfig:
    cfg = JarvisConfig()
    cfg.speech.flux_enabled = True
    for key, value in over.items():
        setattr(cfg.speech, key, value)
    return cfg


class TestKeyContainment:
    """The browser must never be able to obtain DEEPGRAM_API_KEY."""

    def test_the_key_is_never_sent_to_the_client(self):
        """No send_json call may reference the key helper."""
        tree = ast.parse(inspect.getsource(flux_routes))
        sends = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("send_json", "send_text", "send_bytes")
        ]
        for call in sends:
            rendered = ast.dump(call)
            assert "api_key" not in rendered
            assert "DEEPGRAM" not in rendered

    def test_unavailable_reason_does_not_leak_the_key(self):
        with patch.object(flux_routes.flux, "api_key", return_value="secret-key-value"):
            cfg = _enabled_config()
            reason = flux_routes._unavailable_reason(cfg.speech)
        assert "secret-key-value" not in reason


class TestAuthentication:
    def test_unauthorized_socket_is_closed(self):
        app = _app(_enabled_config(), api_key="expected")
        client = TestClient(app)

        with patch.object(flux_routes.flux, "is_available", return_value=True):
            try:
                with client.websocket_connect("/v1/speech/flux"):
                    pass
            except Exception:
                return  # closed during handshake, which is the point
        # If it connected without credentials the proxy is open; fail loudly.
        raise AssertionError("unauthenticated socket was accepted")


class TestFailsClosed:
    """A missing key or a disabled toggle must degrade, not hang."""

    def test_server_kill_switch_reports_unavailable(self):
        """[speech] flux_enabled forbids Flux outright, key or no key."""
        cfg = JarvisConfig()
        cfg.speech.flux_enabled = False
        client = TestClient(_app(cfg))

        with patch.object(flux_routes.flux, "is_available", return_value=True):
            with patch.object(flux_routes.flux, "api_key", return_value="k"):
                with client.websocket_connect("/v1/speech/flux") as ws:
                    msg = ws.receive_json()

        assert msg["type"] == "FluxUnavailable"
        assert "disabled on the server" in msg["reason"]

    def test_missing_key_reports_unavailable(self):
        client = TestClient(_app(_enabled_config()))

        with patch.object(flux_routes.flux, "is_available", return_value=False):
            with patch.object(flux_routes.flux, "api_key", return_value=""):
                with client.websocket_connect("/v1/speech/flux") as ws:
                    msg = ws.receive_json()

        assert msg["type"] == "FluxUnavailable"
        assert "DEEPGRAM_API_KEY" in msg["reason"]

    def test_invalid_thresholds_report_unavailable_instead_of_connecting(self):
        """Deepgram would reject these; the client is told rather than hung."""
        cfg = _enabled_config(
            flux_eager_enabled=True,
            flux_eot_threshold=0.6,
            flux_eager_eot_threshold=0.9,  # above eot -> invalid
        )
        client = TestClient(_app(cfg))

        with patch.object(flux_routes.flux, "is_available", return_value=True):
            with client.websocket_connect("/v1/speech/flux?eager=1") as ws:
                msg = ws.receive_json()

        assert msg["type"] == "FluxUnavailable"
        assert "eager_eot_threshold" in msg["reason"]


class TestEagerIsOptIn:
    """Standard mode must not enable speculation on the Deepgram side."""

    def _captured_session(self, url_query: str, cfg: JarvisConfig):
        captured = {}

        class _FakeSession:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            async def connect(self):
                raise RuntimeError("stop here — construction is what is under test")

        client = TestClient(_app(cfg))
        with patch.object(flux_routes.flux, "is_available", return_value=True):
            with patch.object(flux_routes.flux, "FluxSession", _FakeSession):
                with client.websocket_connect(f"/v1/speech/flux{url_query}") as ws:
                    ws.receive_json()  # FluxUnavailable from the raised connect
        return captured

    def test_standard_mode_passes_no_eager_threshold(self):
        captured = self._captured_session("", _enabled_config())
        assert captured["eager_eot_threshold"] is None

    def test_eager_query_alone_is_not_enough(self):
        """The server-side toggle governs; a crafted URL cannot enable it."""
        cfg = _enabled_config(flux_eager_enabled=False)
        captured = self._captured_session("?eager=1", cfg)
        assert captured["eager_eot_threshold"] is None

    def test_eager_enabled_and_requested_passes_the_threshold(self):
        cfg = _enabled_config(flux_eager_enabled=True, flux_eager_eot_threshold=0.6)
        captured = self._captured_session("?eager=1", cfg)
        assert captured["eager_eot_threshold"] == 0.6


class TestConfigDefaults:
    def test_server_allows_by_default_so_the_ui_toggle_can_work(self):
        """These are kill switches, not the user's choice.

        Gating availability on them made the Settings toggle impossible to
        turn on: it reported "disabled" until it was already enabled. The
        user's opt-in lives in the client, which defaults off; the real
        gate on capability is the presence of DEEPGRAM_API_KEY.
        """
        cfg = JarvisConfig()
        assert cfg.speech.flux_enabled is True
        assert cfg.speech.flux_eager_enabled is True

    def test_without_a_key_flux_is_still_unavailable(self):
        """Allowing it must not mean claiming it works."""
        from openjarvis.speech.flux import is_available

        with patch.dict("os.environ", {"DEEPGRAM_API_KEY": ""}, clear=False):
            assert is_available() is False

    def test_default_thresholds_are_valid_for_deepgram(self):
        from openjarvis.speech.flux import validate_thresholds

        cfg = JarvisConfig().speech
        validate_thresholds(
            cfg.flux_eot_threshold,
            cfg.flux_eager_eot_threshold,
            cfg.flux_eot_timeout_ms,
        )

    def test_default_eager_threshold_is_below_eot(self):
        cfg = JarvisConfig().speech
        assert cfg.flux_eager_eot_threshold <= cfg.flux_eot_threshold


class TestUnavailableReason:
    def test_names_the_missing_key_before_the_kill_switch(self):
        """The missing key is the common case and the one the user can fix."""
        cfg = JarvisConfig()
        cfg.speech.flux_enabled = False
        with patch.object(flux_routes.flux, "api_key", return_value=""):
            assert "DEEPGRAM_API_KEY" in flux_routes._unavailable_reason(cfg.speech)

    def test_names_the_kill_switch_when_a_key_is_present(self):
        cfg = JarvisConfig()
        cfg.speech.flux_enabled = False
        with patch.object(flux_routes.flux, "api_key", return_value="k"):
            assert "disabled on the server" in flux_routes._unavailable_reason(
                cfg.speech
            )

    def test_names_the_missing_key_when_enabled(self):
        cfg = _enabled_config()
        with patch.object(flux_routes.flux, "api_key", return_value=""):
            assert "DEEPGRAM_API_KEY" in flux_routes._unavailable_reason(cfg.speech)

    def test_handles_a_missing_config_object(self):
        assert flux_routes._unavailable_reason(None)


class TestRouteRegistration:
    def test_flux_route_is_registered_without_disturbing_others(self):
        from openjarvis.server.app import create_app

        app = create_app(MagicMock(), "m", config=JarvisConfig())
        paths = {getattr(r, "path", "") for r in app.routes}

        assert "/v1/speech/flux" in paths
        assert "/v1/speech/wake-word" in paths
        assert "/v1/chat/stream" in paths


class TestSpeculationIsServerSide:
    """Speculative text must never cross the wire before confirmation."""

    def test_speculative_answer_is_only_ever_attached_to_a_final_event(self):
        """Only the EndOfTurn branch may add the key."""
        tree = ast.parse(inspect.getsource(flux_routes.flux_stream))
        assigns = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == "speculative_answer"
        ]
        assert assigns, "expected the release path to set speculative_answer"

        # Every such assignment must sit under `if event.is_final`.
        finals = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.If)
            and isinstance(n.test, ast.Attribute)
            and n.test.attr == "is_final"
        ]
        guarded = {
            id(sub)
            for branch in finals
            for node in ast.walk(branch)
            for sub in ast.walk(node)
            if isinstance(sub, ast.Subscript)
            and isinstance(sub.slice, ast.Constant)
            and sub.slice.value == "speculative_answer"
        }
        assert all(id(a) in guarded for a in assigns)

    def test_release_is_called_with_the_confirmed_turn_and_transcript(self):
        source = inspect.getsource(flux_routes.flux_stream)
        assert "speculator.release(event.turn_index, event.transcript)" in source

    def test_turn_resumed_cancels_before_anything_else(self):
        source = inspect.getsource(flux_routes.flux_stream)
        assert "if event.cancels_speculation:" in source
        assert "cancel_speculation()" in source

    def test_speculation_requires_the_eager_threshold_to_be_active(self):
        """Standard mode must never start a speculative generation."""
        source = inspect.getsource(flux_routes.flux_stream)
        assert "event.is_speculative and eager_threshold is not None" in source

    def test_generation_uses_the_tool_disabled_helper(self):
        """Not the agent, and not system.ask().

        The helper is handed to ``asyncio.to_thread`` rather than called
        directly, so it appears as a referenced name, not a call target.
        """
        tree = ast.parse(inspect.getsource(flux_routes.flux_stream))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        assert "generate_speculative" in names

        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        } | {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        # No agent or tool path may be reachable from the speculative branch.
        for forbidden in ("ask", "run_agent", "ToolExecutor", "execute"):
            assert forbidden not in called
