"""Tests for the moments routes and the two moment tools.

The routes are the Settings page's view of what Sage said first and its
"not now" switch; the tools are the same switch and the watch register as
the model reaches them. Both must go through the one running engine, so the
server's in-memory state is the only writer.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="openjarvis[server] not installed")

from openjarvis.core.moments import MomentEngine, set_current_engine
from openjarvis.core.presence import PresenceMonitor, PresenceSettings, save_settings


@pytest.fixture
def engine(tmp_path):
    save_settings(PresenceSettings(enabled=True), tmp_path)
    monitor = PresenceMonitor(
        config_dir=tmp_path,
        idle_sensor=lambda: 1.0,
        foreground_sensor=lambda: None,
        clock=lambda: 1_800_000_000.0,
    )
    engine = MomentEngine(
        monitor,
        config_dir=tmp_path,
        clock=lambda: 1_800_000_000.0,
        composer=lambda kind, ctx: kind,
        speaker=lambda text: True,
        chimer=lambda: True,
        initiative_composer=lambda ctx: "",
        busy_sensor=lambda a, s: [],
        scheduler_lookup=lambda: None,
        timezone_name="Asia/Singapore",
    )
    set_current_engine(engine)
    yield engine
    set_current_engine(None)


def _client(engine=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openjarvis.server.api_routes import presence_router

    app = FastAPI()
    app.include_router(presence_router)
    if engine is not None:
        app.state.moment_engine = engine
    return TestClient(app)


class TestRoutes:
    def test_without_an_engine_it_reports_not_running(self) -> None:
        body = _client().get("/v1/presence/moments").json()
        assert body["running"] is False
        assert body["watches"] == []

    def test_snooze_round_trips(self, engine) -> None:
        client = _client(engine)
        assert client.put(
            "/v1/presence/moments/snooze", json={"snoozed": True}
        ).json() == {"snoozed_today": True}
        assert client.get("/v1/presence/moments").json()["snoozed_today"] is True
        assert (
            client.put(
                "/v1/presence/moments/snooze", json={"snoozed": "yes"}
            ).status_code
            == 400
        )

    def test_watches_can_be_cancelled(self, engine) -> None:
        watch = engine.add_watch("it is three", due_at=1_800_003_600.0)
        client = _client(engine)
        assert [
            w["id"] for w in client.get("/v1/presence/moments").json()["watches"]
        ] == [watch.id]
        assert (
            client.delete(f"/v1/presence/moments/watches/{watch.id}").status_code == 200
        )
        assert (
            client.delete(f"/v1/presence/moments/watches/{watch.id}").status_code == 404
        )

    def test_since_narrows_the_history(self, engine) -> None:
        engine._record("told", "said", spoken=True, detail="", now=10.0)
        engine._record("told", "later", spoken=True, detail="", now=20.0)
        client = _client(engine)
        assert [
            h["text"]
            for h in client.get("/v1/presence/moments?since=15").json()["history"]
        ] == ["later"]

    def test_settings_route_accepts_the_moment_switches(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "openjarvis.core.presence.DEFAULT_CONFIG_DIR", tmp_path, raising=False
        )
        client = _client()
        body = client.put(
            "/v1/presence/settings",
            json={"greeting_enabled": False, "quiet_hours_end_local": 8},
        ).json()
        assert body["greeting_enabled"] is False
        assert body["quiet_hours_end_local"] == 8
        assert (
            client.put(
                "/v1/presence/settings", json={"quiet_hours_end_local": 24}
            ).status_code
            == 400
        )


class TestTools:
    def test_not_now_goes_through_the_engine(self, engine) -> None:
        from openjarvis.tools.moments import NotNowTool

        result = NotNowTool().execute()
        assert result.success and "rest of today" in result.content
        assert engine.snoozed_today() is True
        assert NotNowTool().execute(resume=True).success
        assert engine.snoozed_today() is False

    def test_timed_quiet_and_initiative_commands(self, engine, tmp_path, monkeypatch):
        from openjarvis.tools.moments import NotNowTool

        monkeypatch.setattr(
            "openjarvis.core.presence.DEFAULT_CONFIG_DIR", tmp_path, raising=False
        )
        result = NotNowTool().execute(minutes=30)
        assert result.success and "30 minutes" in result.content
        assert engine.snapshot()["snoozed_until"] == 1_800_000_000.0 + 1800
        assert NotNowTool().execute(resume=True).success
        assert engine.snapshot()["snoozed_until"] is None

        from openjarvis.core.presence import load_settings

        result = NotNowTool().execute(initiative="off")
        assert result.success and "when you call" in result.content
        assert load_settings(tmp_path).initiative_mode == "off"
        assert NotNowTool().execute(initiative="gentle").success
        assert load_settings(tmp_path).initiative_mode == "gentle"
        assert not NotNowTool().execute(initiative="loud").success

    def test_tell_me_when_registers_a_time_watch(self, engine) -> None:
        from openjarvis.tools.moments import TellMeWhenTool

        tool = TellMeWhenTool()
        result = tool.execute(what="your class starts", at="2027-01-19T15:00")
        assert result.success, result.content
        assert "15:00" in result.content
        [watch] = engine.list_watches()
        assert watch.what == "your class starts"
        assert watch.due_at is not None and watch.task_id is None
        listed = tool.execute(action="list")
        assert watch.id in listed.content
        assert tool.execute(action="cancel", watch_id=watch.id).success
        assert engine.list_watches() == []

    def test_tell_me_when_needs_exactly_one_trigger(self, engine) -> None:
        from openjarvis.tools.moments import TellMeWhenTool

        tool = TellMeWhenTool()
        assert not tool.execute(what="x").success
        assert not tool.execute(what="x", at="2027-01-19T15:00", task_id="abc").success
        assert not tool.execute(what="x", at="three o'clock").success
        assert tool.execute(what="x", task_id="abc").success
