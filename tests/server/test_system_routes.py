"""The system panel's feed and End button (M41)."""

from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.core import machine as m
from openjarvis.server import system_routes


def _client():
    app = FastAPI()
    with patch.object(system_routes.threading, "Thread"):
        app.include_router(system_routes.create_system_router())
    return TestClient(app)


def test_stats_carries_the_summary():
    fake = {
        "cpu": {"percent": 5},
        "ram": {"percent": 40, "used_gb": 6, "total_gb": 15.6},
        "gpu": None,
        "disks": [],
        "net": {},
        "top_by_memory": [],
        "sage_total": {},
    }
    with patch.object(m.Machine, "snapshot", return_value=fake):
        got = _client().get("/v1/system/stats").json()
    assert got["summary"].startswith("Running well")


def test_end_maps_refusals_to_status_codes():
    client = _client()
    with patch.object(m.Machine, "end", side_effect=PermissionError("protected")):
        assert (
            client.post("/v1/system/end", json={"app": "Sage app"}).status_code == 403
        )
    with patch.object(m.Machine, "end", side_effect=LookupError("gone")):
        assert client.post("/v1/system/end", json={"app": "Steam"}).status_code == 404
    assert client.post("/v1/system/end", json={}).status_code == 400
    ended = {"app": "Opera GX", "ended": 3, "still_running": 0}
    with patch.object(m.Machine, "end", return_value=ended):
        assert (
            client.post("/v1/system/end", json={"app": "Opera GX"}).json()["ended"] == 3
        )
