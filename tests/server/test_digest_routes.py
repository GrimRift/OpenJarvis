"""Tests for /api/digest endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi", reason="openjarvis[server] not installed")

from openjarvis.agents.digest_store import DigestArtifact, DigestStore


@pytest.fixture()
def store(tmp_path):
    db_path = str(tmp_path / "digest.db")
    s = DigestStore(db_path=db_path)
    s.save(
        DigestArtifact(
            text="Good morning sir.",
            audio_path=tmp_path / "digest.mp3",
            sections={"messages": "3 emails"},
            sources_used=["gmail"],
            generated_at=datetime.now(timezone.utc),
            model_used="test",
            voice_used="jarvis",
        )
    )
    # Write fake audio file
    (tmp_path / "digest.mp3").write_bytes(b"fake-mp3")
    yield s
    s.close()


def _make_app(db_path: str):
    """Create a FastAPI app with the digest router using get_latest as fallback."""
    from unittest.mock import patch

    from fastapi import FastAPI

    from openjarvis.agents.digest_store import DigestStore
    from openjarvis.server.digest_routes import create_digest_router

    # Patch get_today to fall back to get_latest — avoids timezone issues in CI
    original_get_today = DigestStore.get_today

    def _get_today_or_latest(self, timezone_name="UTC"):
        result = original_get_today(self, timezone_name=timezone_name)
        if result is None:
            return self.get_latest()
        return result

    app = FastAPI()
    with patch.object(DigestStore, "get_today", _get_today_or_latest):
        app.include_router(create_digest_router(db_path=db_path))
    return app


def test_get_digest_audio_available_false_when_no_audio_path(tmp_path):
    """A digest with audio_path=None (TTS failed/unconfigured) must report
    audio_available: false rather than crashing or false-positiving."""
    from fastapi.testclient import TestClient

    db_path = str(tmp_path / "digest.db")
    s = DigestStore(db_path=db_path)
    s.save(
        DigestArtifact(
            text="Text-only digest.",
            audio_path=None,
            sections={},
            sources_used=["gmail"],
            generated_at=datetime.now(timezone.utc),
            model_used="test",
            voice_used="",
        )
    )
    s.close()

    app = _make_app(db_path)
    client = TestClient(app)

    resp = client.get("/api/digest")
    assert resp.status_code == 200
    assert resp.json()["audio_available"] is False

    audio_resp = client.get("/api/digest/audio")
    assert audio_resp.status_code == 404


def test_get_digest(store, tmp_path):
    from fastapi.testclient import TestClient

    app = _make_app(str(tmp_path / "digest.db"))
    client = TestClient(app)
    resp = client.get("/api/digest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["text"] == "Good morning sir."
    assert data["sources_used"] == ["gmail"]


def test_get_digest_audio(store, tmp_path):
    from fastapi.testclient import TestClient

    app = _make_app(str(tmp_path / "digest.db"))
    client = TestClient(app)
    resp = client.get("/api/digest/audio")
    assert resp.status_code == 200
    assert resp.content == b"fake-mp3"


def test_get_digest_404(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openjarvis.server.digest_routes import create_digest_router

    app = FastAPI()
    app.include_router(create_digest_router(db_path=str(tmp_path / "empty.db")))

    client = TestClient(app)
    resp = client.get("/api/digest")
    assert resp.status_code == 404


def test_get_history(store, tmp_path):
    from fastapi.testclient import TestClient

    app = _make_app(str(tmp_path / "digest.db"))
    client = TestClient(app)
    resp = client.get("/api/digest/history")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["voice_used"] == "jarvis"


def test_generate_runs_where_no_event_loop_is_running(tmp_path):
    """Generating must not happen on a thread that already runs a loop.

    The briefing reads Outlook and Teams over CDP, and ``CDPSession`` drives
    its own loop with ``run_until_complete`` -- which raises when a loop is
    already running on that thread. ``_collect_browser_sources`` then drops
    the failed source instead of raising, so the endpoint answered 200 with a
    briefing that had silently lost Outlook, Teams and the assignment
    deadline. Nothing in the response distinguished that from a quiet news
    day, which is why this asserts the calling context rather than the text.

    Asserted as "no running loop" rather than "a different thread": thread
    identity is what TestClient happens to vary per request, while a running
    loop is the actual thing CDP cannot tolerate.
    """
    import asyncio
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    seen = {}

    class _FakeJarvis:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def ask(self, *args, **kwargs):
            try:
                asyncio.get_running_loop()
                seen["loop_running"] = True
            except RuntimeError:
                seen["loop_running"] = False
            return "briefing"

    db_path = str(tmp_path / "digest.db")
    DigestStore(db_path=db_path).close()
    app = _make_app(db_path)

    fake_sdk = type("_M", (), {"Jarvis": _FakeJarvis})
    with patch.dict("sys.modules", {"openjarvis.sdk": fake_sdk}):
        response = TestClient(app).post("/api/digest/generate")

    assert response.status_code == 200
    assert response.json()["text"] == "briefing"
    assert seen["loop_running"] is False
