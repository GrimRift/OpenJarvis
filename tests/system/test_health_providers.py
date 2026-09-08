"""Tests for the provider checks.

No test here makes a real call. The property that matters most is the
negative one: a local run must not touch the network, because these calls
cost money and count against daily caps.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from openjarvis.core.health import (
    SECTION_PROVIDERS,
    _check_cartesia,
    _check_deepgram,
    _check_google_apis,
    _check_tavily,
)


def _response(status: int):
    return SimpleNamespace(status_code=status)


class TestGoogleApis:
    def _run(self, tmp_path, live, statuses, token="fresh-token", present=("gmail",)):
        connectors = tmp_path / "connectors"
        connectors.mkdir(exist_ok=True)
        for name in present:
            (connectors / f"{name}.json").write_text("{}", encoding="utf-8")

        calls = []

        def _get(url, **kwargs):
            calls.append(url)
            name = url.rsplit("/", 1)[-1]
            return _response(statuses.get(name, 200))

        with (
            patch("openjarvis.core.health._config_dir", return_value=tmp_path),
            patch(
                "openjarvis.core.health._GOOGLE_PROBES",
                {n: f"https://example.test/{n}" for n in present},
            ),
            patch(
                "openjarvis.connectors.oauth.refresh_google_token",
                return_value=token,
            ),
            patch("httpx.get", side_effect=_get),
        ):
            return _check_google_apis(live), calls

    def test_a_local_run_makes_no_calls(self, tmp_path) -> None:
        results, calls = self._run(tmp_path, live=False, statuses={})
        assert calls == []
        assert results[0].status == "ok"
        assert "not called" in results[0].message

    def test_a_403_is_reported_as_a_disabled_api(self, tmp_path) -> None:
        # Drive, Contacts and Tasks returned 403 for months and were
        # diagnosed as a scope problem for just as long.
        results, _ = self._run(tmp_path, live=True, statuses={"gmail": 403})
        assert results[0].status == "fail"
        assert "403" in results[0].message
        assert "disabled" in (results[0].details or "")
        assert results[0].live is True

    def test_a_200_is_ok(self, tmp_path) -> None:
        results, calls = self._run(tmp_path, live=True, statuses={"gmail": 200})
        assert results[0].status == "ok"
        assert len(calls) == 1

    def test_a_dead_refresh_token_fails_with_a_fix(self, tmp_path) -> None:
        results, calls = self._run(tmp_path, live=True, statuses={}, token=None)
        assert results[0].status == "fail"
        assert results[0].fix == "reauth:gmail"
        assert calls == []

    def test_no_google_connectors_means_no_checks(self, tmp_path) -> None:
        (tmp_path / "connectors").mkdir(exist_ok=True)
        with patch("openjarvis.core.health._config_dir", return_value=tmp_path):
            assert _check_google_apis(live=True) == []


class TestSimpleProviders:
    def test_deepgram_local_run_makes_no_call(self) -> None:
        with (
            patch("openjarvis.core.health._provider_key", return_value="k"),
            patch("openjarvis.speech.flux.api_key", return_value="k"),
            patch("httpx.get") as get,
        ):
            results = _check_deepgram(live=False)
        assert not get.called
        assert results[0].status == "ok"

    def test_deepgram_rejects_a_bad_key(self) -> None:
        with (
            patch("openjarvis.speech.flux.api_key", return_value="k"),
            patch("httpx.get", return_value=_response(401)),
        ):
            results = _check_deepgram(live=True)
        assert results[0].status == "fail"
        assert results[0].live is True

    def test_deepgram_unreachable_points_at_dns(self) -> None:
        with (
            patch("openjarvis.speech.flux.api_key", return_value="k"),
            patch("httpx.get", side_effect=OSError("no route")),
        ):
            results = _check_deepgram(live=True)
        assert results[0].status == "fail"
        assert "DNS" in (results[0].details or "")

    def test_cartesia_local_run_makes_no_call(self) -> None:
        with (
            patch("openjarvis.core.health._provider_key", return_value="k"),
            patch("httpx.get") as get,
        ):
            results = _check_cartesia(live=False)
        assert not get.called
        assert results[0].status == "ok"

    def test_cartesia_key_rejected_is_a_failure(self) -> None:
        with (
            patch("openjarvis.core.health._provider_key", return_value="k"),
            patch("httpx.get", return_value=_response(401)),
        ):
            results = _check_cartesia(live=True)
        assert results[0].status == "fail"
        assert results[0].section == SECTION_PROVIDERS

    def test_an_absent_key_produces_no_check(self) -> None:
        with patch("openjarvis.core.health._provider_key", return_value=""):
            assert _check_cartesia(live=True) == []
            assert _check_tavily(live=True) == []

    def test_tavily_local_run_makes_no_call(self) -> None:
        with (
            patch("openjarvis.core.health._provider_key", return_value="k"),
            patch("httpx.post") as post,
        ):
            results = _check_tavily(live=False)
        assert not post.called
        assert results[0].status == "ok"

    def test_tavily_failure_says_there_is_no_fallback(self) -> None:
        with (
            patch("openjarvis.core.health._provider_key", return_value="k"),
            patch("httpx.post", return_value=_response(432)),
        ):
            results = _check_tavily(live=True)
        assert results[0].status == "fail"
        assert "fallback" in (results[0].details or "")
