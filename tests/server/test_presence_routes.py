"""Tests for the presence routes.

The settings route is the master switch for everything M36 does on its own,
so what matters is that it validates strictly and that a change is reflected
at once rather than at the next poll.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("fastapi", reason="openjarvis[server] not installed")

from openjarvis.core.presence import PresenceMonitor, PresenceSettings, save_settings


def _client(monitor=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openjarvis.server.api_routes import presence_router

    app = FastAPI()
    app.include_router(presence_router)
    if monitor is not None:
        app.state.presence_monitor = monitor
    return TestClient(app)


class TestState:
    def test_without_a_monitor_it_says_so(self) -> None:
        body = _client().get("/v1/presence").json()
        assert body["state"] == "disabled"
        assert "not running" in body["reason"]

    def test_with_a_monitor_it_returns_the_snapshot(self, tmp_path) -> None:
        save_settings(PresenceSettings(enabled=True), tmp_path)
        monitor = PresenceMonitor(
            config_dir=tmp_path,
            idle_sensor=lambda: 3.0,
            foreground_sensor=lambda: "Editor",
            clock=lambda: 1.0,
        )
        monitor.poll()
        body = _client(monitor).get("/v1/presence").json()
        assert body["state"] == "present"
        assert body["foreground"] == "Editor"


class TestSettings:
    def test_get_returns_defaults(self, tmp_path) -> None:
        with patch("openjarvis.core.presence.DEFAULT_CONFIG_DIR", tmp_path):
            body = _client().get("/v1/presence/settings").json()
        assert body["enabled"] is False

    def test_put_persists_and_polls_immediately(self, tmp_path) -> None:
        polled = []
        monitor = SimpleNamespace(poll=lambda: polled.append(1))
        with patch("openjarvis.core.presence.DEFAULT_CONFIG_DIR", tmp_path):
            res = _client(monitor).put("/v1/presence/settings", json={"enabled": True})
            assert res.status_code == 200
            assert res.json()["enabled"] is True
            assert _client().get("/v1/presence/settings").json()["enabled"] is True
        assert polled == [1]

    def test_a_non_boolean_switch_is_rejected(self, tmp_path) -> None:
        with patch("openjarvis.core.presence.DEFAULT_CONFIG_DIR", tmp_path):
            res = _client().put("/v1/presence/settings", json={"enabled": "yes"})
        assert res.status_code == 400

    def test_a_non_positive_threshold_is_rejected(self, tmp_path) -> None:
        with patch("openjarvis.core.presence.DEFAULT_CONFIG_DIR", tmp_path):
            res = _client().put(
                "/v1/presence/settings", json={"idle_threshold_seconds": 0}
            )
        assert res.status_code == 400

    def test_unknown_keys_are_ignored_not_stored(self, tmp_path) -> None:
        with patch("openjarvis.core.presence.DEFAULT_CONFIG_DIR", tmp_path):
            body = _client().put(
                "/v1/presence/settings", json={"camera": True}
            ).json()
        assert "camera" not in body


class TestRecentDaysMatchTheRoute:
    """The route's lookup, run against the real signatures.

    A renamed keyword between the episode store and the chat route raised a
    TypeError that the surrounding except swallowed, so every prompt quietly
    lost its recent days while every unit test passed. This reads the
    keywords the route actually passes and runs them, so a signature change
    fails here rather than silently there.
    """

    def _route_call(self):
        import ast
        from pathlib import Path as _P

        src = _P("src/openjarvis/server/routes.py").read_text(encoding="utf-8")
        calls = [
            node
            for node in ast.walk(ast.parse(src))
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", "") == "recent_episodes"
        ]
        assert calls, "the route no longer calls recent_episodes"
        return calls[0]

    def test_the_routes_lookup_works_against_real_signatures(self, tmp_path) -> None:
        import ast
        from datetime import date, timedelta

        from openjarvis.core.presence import PresenceSettings, save_settings
        from openjarvis.memory.episodes import (
            Episode,
            format_recent_days,
            recent_episodes,
            save_episode,
        )

        save_settings(PresenceSettings(enabled=True), tmp_path)
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        save_episode(Episode(yesterday, "we fixed the pack", 3, 1.0), tmp_path)

        call = self._route_call()
        names = {kw.arg for kw in call.keywords}
        # The route must scope the lookup to the server's own data dir.
        assert names == {"count", "config_dir"}, names
        count = next(
            kw.value.value for kw in call.keywords
            if kw.arg == "count" and isinstance(kw.value, ast.Constant)
        )
        recent = format_recent_days(recent_episodes(count=count, config_dir=tmp_path))
        assert "Yesterday: we fixed the pack" in recent

    def test_a_test_app_without_a_monitor_never_reads_the_real_diary(self) -> None:
        # The chat route only consults episodes through app.state's presence
        # monitor. A test app has none, so it cannot read the machine's own
        # diary into a test prompt -- which it did once, appending real
        # backfilled days to "Be terse."
        import ast
        from pathlib import Path as _P

        src = _P("src/openjarvis/server/routes.py").read_text(encoding="utf-8")
        assert 'getattr(request.app.state, "presence_monitor", None)' in src
        # And the guard sits on the same lines as the lookup, not elsewhere.
        tree = ast.parse(src)
        # The route is async, so both node kinds are checked.
        kinds = (ast.FunctionDef, ast.AsyncFunctionDef)
        funcs = [
            n
            for n in ast.walk(tree)
            if isinstance(n, kinds)
            and any(
                isinstance(c, ast.Call)
                and getattr(c.func, "id", "") == "recent_episodes"
                for c in ast.walk(n)
            )
        ]
        assert funcs, "no function calls recent_episodes"
        for fn in funcs:
            text = ast.get_source_segment(src, fn) or ""
            assert "presence_monitor" in text
