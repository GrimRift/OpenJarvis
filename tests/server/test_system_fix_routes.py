"""Tests for the fix describe/apply routes.

The apply route takes ``confirmed`` as a query parameter defaulting to false,
so a caller that forgets it gets a refusal rather than a side effect. That
default is the whole safety property, and it is what these tests pin.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("fastapi", reason="openjarvis[server] not installed")

from openjarvis.core.fixes import FixOutcome, FixPlan


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openjarvis.server.api_routes import system_router

    app = FastAPI()
    app.include_router(system_router)
    return TestClient(app)


_PLAN = FixPlan(
    fix_id="rerun-job:abc",
    title="Run this scheduled job now",
    description="Runs it once.",
    steps=["Execute the job once now."],
    automatic=True,
    reversible=False,
)


class TestDescribeRoute:
    def test_returns_the_plan(self) -> None:
        with patch("openjarvis.core.fixes.describe_fix", return_value=_PLAN):
            response = _client().get("/v1/system/fixes/rerun-job:abc")
        assert response.status_code == 200
        body = response.json()
        assert body["automatic"] is True
        assert body["reversible"] is False

    def test_unknown_fix_is_a_404(self) -> None:
        with patch("openjarvis.core.fixes.describe_fix", return_value=None):
            response = _client().get("/v1/system/fixes/nope:x")
        assert response.status_code == 404

    def test_describing_does_not_apply(self) -> None:
        with (
            patch("openjarvis.core.fixes.describe_fix", return_value=_PLAN),
            patch("openjarvis.core.fixes.apply_fix") as apply_mock,
        ):
            _client().get("/v1/system/fixes/rerun-job:abc")
        assert not apply_mock.called


class TestApplyRoute:
    def test_omitting_confirmed_refuses(self) -> None:
        with patch("openjarvis.core.fixes.apply_fix") as apply_mock:
            apply_mock.return_value = FixOutcome("rerun-job:abc", False, "no")
            _client().post("/v1/system/fixes/rerun-job:abc/apply")
        assert apply_mock.call_args.kwargs == {"confirmed": False}

    def test_confirmed_true_is_passed_through(self) -> None:
        with patch("openjarvis.core.fixes.apply_fix") as apply_mock:
            apply_mock.return_value = FixOutcome("rerun-job:abc", True, "ran")
            response = _client().post(
                "/v1/system/fixes/rerun-job:abc/apply?confirmed=true"
            )
        assert apply_mock.call_args.kwargs == {"confirmed": True}
        assert response.json()["applied"] is True

    def test_a_refusal_is_not_an_error_status(self) -> None:
        # The caller needs to read why it was refused, so this is a 200 with
        # applied=false rather than an error the UI would render as a crash.
        with patch(
            "openjarvis.core.fixes.apply_fix",
            return_value=FixOutcome("reauth:gmail", False, "cannot be applied"),
        ):
            response = _client().post(
                "/v1/system/fixes/reauth:gmail/apply?confirmed=true"
            )
        assert response.status_code == 200
        assert response.json()["applied"] is False
