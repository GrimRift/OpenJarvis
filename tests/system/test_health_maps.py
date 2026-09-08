"""Tests for the Google Maps probes and their rate limit.

Routes and Places are capped at 30 requests a day each, and a live health
check is reachable from chat, so the property that matters most is that a
second run does not spend a second request. The car briefing needs that quota
more than this page does.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import patch

from openjarvis.core.health import _check_google_maps


def _nav(routes=True, places=True):
    return SimpleNamespace(
        navigation=SimpleNamespace(routes_enabled=routes, places_enabled=places)
    )


class _Spy:
    def __init__(self, fail=False):
        self.routes = 0
        self.places = 0
        self.fail = fail

    def compute_route(self, origin, destination, key):
        self.routes += 1
        if self.fail:
            raise RuntimeError("quota exceeded for key AIzaSyLEAK")
        return {"ok": True}

    def search_places(self, query, key, origin):
        self.places += 1
        return [{"place_id": "x"}]


def _run(tmp_path, live, spy, config=None, key="a-key"):
    fake_module = SimpleNamespace(
        compute_route=spy.compute_route, search_places=spy.search_places
    )
    with (
        patch("openjarvis.core.health._config_dir", return_value=tmp_path),
        patch("openjarvis.core.health._get_config", return_value=config or _nav()),
        patch("openjarvis.core.health._provider_key", return_value=key),
        patch.dict("sys.modules", {"openjarvis.tools.navigate": fake_module}),
    ):
        return _check_google_maps(live)


class TestRateLimit:
    def test_a_local_run_spends_nothing(self, tmp_path) -> None:
        spy = _Spy()
        results = _run(tmp_path, False, spy)
        assert spy.routes == 0 and spy.places == 0
        assert results[0].status == "ok"
        assert "not called" in results[0].message

    def test_the_first_live_run_calls_both(self, tmp_path) -> None:
        spy = _Spy()
        results = _run(tmp_path, True, spy)
        assert spy.routes == 1 and spy.places == 1
        assert [r.status for r in results] == ["ok", "ok"]
        assert all(r.live for r in results)

    def test_a_second_live_run_the_same_day_spends_nothing(self, tmp_path) -> None:
        spy = _Spy()
        _run(tmp_path, True, spy)
        results = _run(tmp_path, True, spy)
        assert spy.routes == 1 and spy.places == 1
        assert all("checked" in r.message for r in results)
        # A remembered result is not evidence of a call made now.
        assert not any(r.live for r in results)

    def test_the_remembered_result_keeps_the_status(self, tmp_path) -> None:
        spy = _Spy(fail=True)
        first = _run(tmp_path, True, spy)
        assert first[0].status == "fail"
        second = _run(tmp_path, True, spy)
        assert second[0].status == "fail"

    def test_a_new_day_allows_another_probe(self, tmp_path) -> None:
        spy = _Spy()
        _run(tmp_path, True, spy)
        state = json.loads((tmp_path / "health_probes.json").read_text())
        for entry in state.values():
            entry["day"] = "2000-01-01"
        (tmp_path / "health_probes.json").write_text(json.dumps(state))
        _run(tmp_path, True, spy)
        assert spy.routes == 2 and spy.places == 2

    def test_an_unwritable_state_file_does_not_break_the_check(self, tmp_path) -> None:
        # Patch the write itself, not _save_probe_state: the protection lives
        # inside that function, so patching it would test the mock instead.
        spy = _Spy()
        with patch("pathlib.Path.write_text", side_effect=OSError("nope")):
            results = _run(tmp_path, True, spy)
        assert results[0].status == "ok"


class TestGating:
    def test_disabled_apis_produce_no_checks(self, tmp_path) -> None:
        spy = _Spy()
        results = _run(tmp_path, True, spy, config=_nav(routes=False, places=False))
        assert results == []
        assert spy.routes == 0

    def test_only_the_enabled_api_is_probed(self, tmp_path) -> None:
        spy = _Spy()
        results = _run(tmp_path, True, spy, config=_nav(routes=True, places=False))
        assert spy.routes == 1 and spy.places == 0
        assert len(results) == 1

    def test_enabled_without_a_key_fails(self, tmp_path) -> None:
        spy = _Spy()
        results = _run(tmp_path, True, spy, key="")
        assert results[0].status == "fail"
        assert spy.routes == 0


class TestFailuresDoNotLeak:
    def test_the_provider_body_is_not_repeated(self, tmp_path) -> None:
        # Google error bodies can echo the key back; only the exception type
        # is reported.
        spy = _Spy(fail=True)
        results = _run(tmp_path, True, spy)
        assert results[0].status == "fail"
        assert "AIzaSyLEAK" not in results[0].message
        assert "RuntimeError" in results[0].message

    def test_a_failure_is_recorded_with_a_timestamp(self, tmp_path) -> None:
        spy = _Spy(fail=True)
        _run(tmp_path, True, spy)
        state = json.loads((tmp_path / "health_probes.json").read_text())
        assert state["routes"]["status"] == "fail"
        assert state["routes"]["at"] <= time.time()
