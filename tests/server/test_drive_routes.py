import ast
import base64
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.core.types import ToolResult
from openjarvis.server import drive_routes as module

BODY = {"destination": "public test", "origin": {"latitude": 14.0, "longitude": 121.0}}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENJARVIS_HOME", str(tmp_path))
    monkeypatch.delenv("OPENJARVIS_CONFIG", raising=False)
    app = FastAPI()
    app.state.api_key = "test-only-auth"
    app.include_router(module.router)
    monkeypatch.setattr(
        module.NavigateTool, "execute", lambda *a, **k: pytest.fail("Unexpected call")
    )
    monkeypatch.setattr(
        module, "_audio", lambda *a: pytest.fail("Unexpected synthesis")
    )
    with TestClient(app) as c:
        yield c


HEADERS = {"Authorization": "Bearer test-only-auth"}


def test_audio_uses_sage_profile_and_spoken_text(monkeypatch):
    from openjarvis.speech import cartesia_tts
    from openjarvis.speech.tts import TTSResult

    class FakeBackend:
        def synthesize(self, text, **kwargs):
            assert "**" not in text
            assert kwargs["voice_id"] == module.JARVIS.voice_id
            assert kwargs["volume"] == module.JARVIS.volume
            return TTSResult(audio=b"test-mp3")

    monkeypatch.setattr(cartesia_tts, "CartesiaTTSBackend", FakeBackend)
    audio = module._audio("**Directions** to the destination.", "jarvis")
    assert base64.b64decode(audio["base64"]) == b"test-mp3"


def result(status="ready"):
    return ToolResult(
        tool_name="navigate",
        success=True,
        content="Directions.",
        metadata={
            "status": status,
            "briefing": "Directions to public test.",
            "maps_url": "https://waze.com/ul?ll=14,121&navigate=yes",
            "navigation_started": False,
        },
    )


def test_no_auth_never_calls_providers(client):
    assert client.post("/v1/drive", json=BODY).status_code == 401
    assert (
        client.post(
            "/v1/drive", json=BODY, headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )


def test_disabled_server_auth_is_not_permission(client):
    client.app.state.api_key = ""
    assert client.post("/v1/drive", json=BODY, headers=HEADERS).status_code == 503


@pytest.mark.parametrize(
    "change",
    [
        {"origin": None},
        {"origin": {"latitude": 91, "longitude": 0}},
        {"voice": "unapproved"},
        {"include_audio": "true"},
        {"extra": "value"},
    ],
)
def test_request_validation_before_providers(client, change):
    assert (
        client.post("/v1/drive", json={**BODY, **change}, headers=HEADERS).status_code
        == 422
    )


def test_origin_required(client):
    assert (
        client.post(
            "/v1/drive", json={"destination": "there"}, headers=HEADERS
        ).status_code
        == 422
    )


def test_shared_tool_and_audio_run_off_loop(client, monkeypatch):
    main_thread = client.portal.call(threading.get_ident)
    calls = []

    def execute(self, **params):
        assert threading.get_ident() != main_thread
        calls.append(params)
        return result()

    def audio(text, voice):
        assert threading.get_ident() != main_thread
        assert voice == "jarvis"
        assert text == "Directions to public test."
        return {"base64": base64.b64encode(b"mp3").decode(), "mime_type": "audio/mpeg"}

    monkeypatch.setattr(module.NavigateTool, "execute", execute)
    monkeypatch.setattr(module, "_audio", audio)
    r = client.post("/v1/drive", json=BODY, headers=HEADERS)
    assert r.status_code == 200
    assert calls == [BODY]
    assert r.headers["cache-control"] == "no-store"
    assert r.json()["audio_status"] == "ready"
    assert base64.b64decode(r.json()["audio"]["base64"]) == b"mp3"
    assert not r.json()["navigation_started"]


def test_selection_does_not_synthesize(client, monkeypatch):
    monkeypatch.setattr(
        module.NavigateTool, "execute", lambda *a, **k: result("needs_selection")
    )
    r = client.post("/v1/drive", json=BODY, headers=HEADERS).json()
    assert r["audio_status"] == "needs_selection" and r["audio"] is None


def test_audio_failure_preserves_link_without_leaking_error(client, monkeypatch):
    monkeypatch.setattr(module.NavigateTool, "execute", lambda *a, **k: result())

    def fail(*args):
        raise RuntimeError("private-provider-value")

    monkeypatch.setattr(module, "_audio", fail)
    r = client.post("/v1/drive", json=BODY, headers=HEADERS)
    assert r.json()["audio_status"] == "unavailable"
    assert r.json()["maps_url"].startswith("https://waze.com/")
    assert "private-provider-value" not in r.text


def test_text_only_does_not_synthesize(client, monkeypatch):
    monkeypatch.setattr(module.NavigateTool, "execute", lambda *a, **k: result())
    r = client.post("/v1/drive", json={**BODY, "include_audio": False}, headers=HEADERS)
    assert r.json()["audio_status"] == "not_requested"


def test_app_mounts_drive_router():
    tree = ast.parse((Path(module.__file__).parent / "app.py").read_text("utf-8"))
    assert any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "include_router"
        and n.args
        and isinstance(n.args[0], ast.Name)
        and n.args[0].id == "drive_router"
        for n in ast.walk(tree)
    )
