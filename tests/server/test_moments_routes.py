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
        speaker=lambda text, before=None: True,
        chimer=lambda: True,
        initiative_composer=lambda ctx: "",
        busy_sensor=lambda a, s: [],
        scheduler_lookup=lambda: None,
        timezone_name="Asia/Singapore",
        reminder=lambda what: True,
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
        assert not tool.execute(what="x", in_minutes=5, at="2027-01-19T15:00").success
        assert not tool.execute(what="x", in_minutes=0).success
        assert not tool.execute(what="x", in_minutes="soon").success
        # A job watch cannot be a reminder: it is said at the desk.
        assert not tool.execute(what="x", task_id="abc", anywhere=True).success

    def test_a_relative_time_is_a_reminder_wherever_the_user_is(self, engine) -> None:
        """ "Tell me five minutes from now that I have to eat" was refused
        because only an absolute datetime was accepted."""
        import time

        from openjarvis.tools.moments import TellMeWhenTool

        tool = TellMeWhenTool()
        before = time.time()
        result = tool.execute(what="you have to eat", in_minutes=5)
        assert result.success, result.content
        assert result.content.startswith("I'll tell you in 5 minutes (")
        assert "wherever you are" in result.content
        [watch] = engine.list_watches()
        assert watch.anywhere is True
        assert abs(watch.due_at - (before + 300)) < 5
        assert "anywhere" in tool.execute(action="list").content

        hour = tool.execute(what="stretch", in_minutes=90)
        assert "in 1 hour 30 minutes" in hour.content

    def test_an_absolute_time_stays_at_the_desk_unless_asked(self, engine) -> None:
        from openjarvis.tools.moments import TellMeWhenTool

        tool = TellMeWhenTool()
        desk = tool.execute(what="a", at="2027-01-19T15:00")
        assert "at the desk" in desk.content
        anywhere = tool.execute(what="b", at="2027-01-19T16:00", anywhere=True)
        assert "wherever you are" in anywhere.content
        assert [w.anywhere for w in engine.list_watches()] == [False, True]


class TestAReminderIsNotSetTwice:
    """23 September: "go downstairs" was said aloud, the user asked "can you
    remind me again?" -- what was it? -- and the model set it for another
    minute, twice over: three voices, three phone notifications."""

    def test_one_just_said_is_not_set_again_for_right_now(self, engine) -> None:
        import time

        from openjarvis.core.moments import MOMENT_TOLD
        from openjarvis.tools.moments import TellMeWhenTool

        engine._record(
            MOMENT_TOLD, "(reminder) go downstairs", spoken=False,
            detail="reminder", now=time.time() - 20,
        )
        result = TellMeWhenTool().execute(what="Go downstairs.", in_minutes=1)
        assert "just said aloud" in result.content
        assert engine.list_watches() == []

    def test_later_is_still_a_new_reminder(self, engine) -> None:
        import time

        from openjarvis.core.moments import MOMENT_TOLD
        from openjarvis.tools.moments import TellMeWhenTool

        engine._record(
            MOMENT_TOLD, "(reminder) go downstairs", spoken=False,
            detail="reminder", now=time.time() - 20,
        )
        TellMeWhenTool().execute(what="go downstairs", in_minutes=10)
        assert len(engine.list_watches()) == 1

    def test_long_after_it_is_a_new_reminder(self, engine) -> None:
        import time

        from openjarvis.core.moments import MOMENT_TOLD
        from openjarvis.tools.moments import TellMeWhenTool

        engine._record(
            MOMENT_TOLD, "(reminder) go downstairs", spoken=False,
            detail="reminder", now=time.time() - 600,
        )
        TellMeWhenTool().execute(what="go downstairs", in_minutes=1)
        assert len(engine.list_watches()) == 1

    def test_the_same_one_twice_in_a_turn_is_one(self, engine) -> None:
        from openjarvis.tools.moments import TellMeWhenTool

        tool = TellMeWhenTool()
        tool.execute(what="go downstairs", in_minutes=1)
        again = tool.execute(what="Go downstairs", in_minutes=1)
        assert "Already set" in again.content
        assert len(engine.list_watches()) == 1
        tool.execute(what="drink water", in_minutes=1)
        assert len(engine.list_watches()) == 2


def test_a_moment_being_spoken_does_not_freeze_the_server(engine) -> None:
    """The engine holds its lock for a whole moment. The page's poll of
    /moments used to wait on it on the event loop, so for 15 s nothing else
    was served -- the voice stream included, which is how the orb missed
    every moment (23 September)."""
    import threading
    import time

    held = threading.Event()

    def speak_a_moment():
        with engine._lock:
            held.set()
            time.sleep(1.0)

    # As a context manager the client serves every request on one event
    # loop, as the real server does; otherwise each gets a loop of its own
    # and nothing can block anything else.
    with _client(engine) as client:
        threading.Thread(target=speak_a_moment).start()
        held.wait(1)
        feed = threading.Thread(target=lambda: client.get("/v1/presence/moments"))
        feed.start()
        time.sleep(0.1)
        t0 = time.monotonic()
        assert client.get("/v1/presence/voice").status_code == 200
        assert time.monotonic() - t0 < 0.5
        feed.join(3)
