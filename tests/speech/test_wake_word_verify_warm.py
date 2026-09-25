"""The idle warm-up of the wake-word verifier: once, in a quiet room,
after four unused minutes, and never in anyone's way."""

from __future__ import annotations

import asyncio

import pytest

from openjarvis.core import activity
from openjarvis.speech import wake_word_verify as wwv


@pytest.fixture(autouse=True)
def _no_real_model(monkeypatch):
    # The verifier under test is handed a fake; nothing may reach the real
    # small model (tests/architecture: local_verifier_backend stubbed).
    monkeypatch.setattr(wwv, "local_verifier_backend", lambda config: None)
    monkeypatch.setattr(wwv, "keep_clip", lambda pcm, verdict: kept.append(pcm))
    kept.clear()
    activity.reset()
    yield
    activity.reset()


kept: list = []


class Backend:
    def __init__(self):
        self.heard = []

    def transcribe(self, audio, **kwargs):
        self.heard.append(audio)

        class R:
            text = ""
            segments = []

        return R()


def _idle(monkeypatch, seconds):
    monkeypatch.setitem(wwv._verifier_state, "running", 0)
    monkeypatch.setitem(
        wwv._verifier_state, "last_used", wwv.time.monotonic() - seconds
    )


def test_it_runs_once_on_silence_after_the_idle_time(monkeypatch):
    _idle(monkeypatch, wwv.IDLE_WARM_SECONDS + 1)
    backend = Backend()
    verifier = wwv.WakeWordVerifier(backend)
    ms = asyncio.run(verifier.warm_if_idle())
    assert ms is not None and len(backend.heard) == 1
    # Nothing kept of it.
    assert kept == []
    # Not again until the model has been idle that long once more.
    assert asyncio.run(verifier.warm_if_idle()) is None
    assert len(backend.heard) == 1


def test_it_waits_while_the_model_is_in_recent_use(monkeypatch):
    _idle(monkeypatch, 30)
    backend = Backend()
    assert asyncio.run(wwv.WakeWordVerifier(backend).warm_if_idle()) is None
    assert backend.heard == []


def test_it_never_runs_beside_a_real_check(monkeypatch):
    _idle(monkeypatch, wwv.IDLE_WARM_SECONDS + 1)
    monkeypatch.setitem(wwv._verifier_state, "running", 1)
    backend = Backend()
    assert asyncio.run(wwv.WakeWordVerifier(backend).warm_if_idle()) is None
    assert backend.heard == []


def test_it_stays_out_of_a_live_exchange_and_sages_voice(monkeypatch):
    _idle(monkeypatch, wwv.IDLE_WARM_SECONDS + 1)
    backend = Backend()
    verifier = wwv.WakeWordVerifier(backend)
    activity.user_talking(True)
    assert asyncio.run(verifier.warm_if_idle()) is None
    activity.reset()
    monkeypatch.setattr("openjarvis.speech.player.is_speaking", lambda: True)
    assert asyncio.run(verifier.warm_if_idle()) is None
    assert backend.heard == []


def test_a_real_check_counts_as_use(monkeypatch):
    _idle(monkeypatch, wwv.IDLE_WARM_SECONDS + 1)
    backend = Backend()
    verifier = wwv.WakeWordVerifier(backend)
    asyncio.run(verifier.verify(b"\x01\x00" * 16000))
    # Just used for real: the warm-up is not needed.
    assert asyncio.run(verifier.warm_if_idle()) is None
    assert wwv._verifier_state["running"] == 0


def test_the_warm_up_is_visible_in_the_status(monkeypatch):
    _idle(monkeypatch, wwv.IDLE_WARM_SECONDS + 1)
    monkeypatch.setitem(wwv._verifier_state, "warm_runs", 0)
    asyncio.run(wwv.WakeWordVerifier(Backend()).warm_if_idle())
    status = wwv.verifier_status()
    assert status["warm_runs"] == 1 and status["last_warm_ms"] is not None
    assert status["idle_s"] < 5 and status["checks_running"] == 0
