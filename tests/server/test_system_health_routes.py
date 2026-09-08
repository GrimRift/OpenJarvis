"""Tests for ``GET /v1/system/health``.

The route exists so the Health page and the ``system_health`` tool read the
same run. The tests that matter here are the ones that would catch a second
path opening up: that the route delegates to the shared function, and that it
does not quietly go live.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("fastapi", reason="openjarvis[server] not installed")

from openjarvis.core.health import CheckResult, HealthReport


def _make_app():
    from fastapi import FastAPI

    from openjarvis.server.api_routes import system_router

    app = FastAPI()
    app.include_router(system_router)
    return app


def _client():
    from fastapi.testclient import TestClient

    return TestClient(_make_app())


_FAKE = HealthReport(
    checks=[
        CheckResult("Good", "ok", "fine", section="system"),
        CheckResult("Bad", "fail", "broken", section="voice", fix="reauth:x"),
    ]
)


class TestSystemHealthRoute:
    def test_returns_the_shared_report(self) -> None:
        with patch(
            "openjarvis.core.health.run_health_checks", return_value=_FAKE
        ) as run:
            response = _client().get("/v1/system/health")
        assert response.status_code == 200
        assert run.called
        body = response.json()
        assert body["status"] == "fail"
        sections = {s["id"]: s for s in body["sections"]}
        assert set(sections) == {"system", "voice"}
        assert sections["voice"]["checks"][0]["fix"] == "reauth:x"

    def test_defaults_to_a_local_run(self) -> None:
        # A live run is billable and counts against a 30/day cap, so the
        # default must never reach the network on its own.
        with patch(
            "openjarvis.core.health.run_health_checks", return_value=_FAKE
        ) as run:
            _client().get("/v1/system/health")
        assert run.call_args.kwargs["live"] is False
        # app_state is passed so the telemetry check can see the engines.
        assert "app_state" in run.call_args.kwargs

    def test_live_is_opt_in_through_the_query_string(self) -> None:
        with patch(
            "openjarvis.core.health.run_health_checks", return_value=_FAKE
        ) as run:
            _client().get("/v1/system/health?live=true")
        assert run.call_args.kwargs["live"] is True

    def test_a_failing_check_run_is_a_500_not_a_crash(self) -> None:
        with patch(
            "openjarvis.core.health.run_health_checks",
            side_effect=RuntimeError("boom"),
        ):
            response = _client().get("/v1/system/health")
        assert response.status_code == 500
