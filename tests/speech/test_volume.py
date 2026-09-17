"""Sage's volumes: master times channel, shared through volume.json."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="openjarvis[server] not installed")

from openjarvis.speech.volume import Volumes, level, load_volumes, save_volumes


def test_effective_is_master_times_channel_clamped(tmp_path):
    v = Volumes(master=0.5, chat=0.8, chime=1.0)
    assert v.effective("chat") == pytest.approx(0.4)
    assert v.effective("chime") == 0.5
    assert v.effective("unknown") == 0.5
    save_volumes(v, tmp_path)
    assert level("chat", tmp_path) == pytest.approx(0.4)


def test_missing_or_broken_file_means_full_volume(tmp_path):
    assert load_volumes(tmp_path).to_dict() == {
        "master": 1.0,
        "chat": 1.0,
        "ack": 1.0,
        "moments": 1.0,
        "reminders": 1.0,
        "chime": 1.0,
    }
    (tmp_path / "volume.json").write_text("not json", encoding="utf-8")
    assert load_volumes(tmp_path).master == 1.0
    (tmp_path / "volume.json").write_text(
        '{"master": 7, "chat": "x"}', encoding="utf-8"
    )
    loaded = load_volumes(tmp_path)
    assert loaded.master == 1.0 and loaded.chat == 1.0


def test_api_round_trip_and_validation(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openjarvis.server.api_routes import speech_router

    monkeypatch.setattr("openjarvis.speech.volume.DEFAULT_CONFIG_DIR", tmp_path)
    app = FastAPI()
    app.include_router(speech_router)
    client = TestClient(app)
    assert client.get("/v1/speech/volume").json()["master"] == 1.0
    assert client.put(
        "/v1/speech/volume", json={"master": 0.6, "chime": 0.2}
    ).json() == {
        "master": 0.6,
        "chat": 1.0,
        "ack": 1.0,
        "moments": 1.0,
        "reminders": 1.0,
        "chime": 0.2,
    }
    assert client.put("/v1/speech/volume", json={"tv": 0.5}).status_code == 400
    assert client.put("/v1/speech/volume", json={"chat": 1.5}).status_code == 400
    assert client.put("/v1/speech/volume", json={"chat": "loud"}).status_code == 400
    assert level("chime", tmp_path) == pytest.approx(0.12)


def test_ffplay_gets_the_level(monkeypatch, tmp_path):
    from openjarvis.speech import player

    calls = []

    def _run(cmd, **kwargs):
        calls.append(cmd)

    monkeypatch.setattr(player.subprocess, "run", _run)
    monkeypatch.setattr("openjarvis.speech.volume.DEFAULT_CONFIG_DIR", tmp_path)
    save_volumes(Volumes(master=0.5, chime=0.5), tmp_path)
    player.play_file("x.wav", duck=False, channel="chime")
    assert calls[0][:1] == ["ffplay"] and "-af" in calls[0]
    # 0.25 with the boost on top, through a limiter (ffplay's own -volume
    # stops at 100, so the boost needs the filter).
    filters = calls[0][calls[0].index("-af") + 1]
    assert filters == "volume=0.300,alimiter=limit=0.97:level=false"
    assert calls[0][-1] == "x.wav"


def test_full_volume_is_the_boost_and_zero_stays_zero(tmp_path):
    from openjarvis.speech.volume import BOOST, gain

    monkeypatch_dir = tmp_path
    save_volumes(Volumes(), monkeypatch_dir)
    assert BOOST == pytest.approx(1.2)
    assert gain("chat", monkeypatch_dir) == pytest.approx(BOOST)
    assert level("chat", monkeypatch_dir) == pytest.approx(1.0)
    save_volumes(Volumes(master=0.0), monkeypatch_dir)
    assert gain("chat", monkeypatch_dir) == 0.0
