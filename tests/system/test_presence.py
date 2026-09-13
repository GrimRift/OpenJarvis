"""Tests for presence sensing.

The state machine is what every later M36 phase trusts. If it says "present"
to an empty desk a moment fires into a room with nobody in it; if it says
"away" to someone who is sitting there, "welcome back" is said to a person
who never left. Both are worse than staying silent, so the transitions are
tested with an injected clock and injected sensors rather than a real desk.
"""

from __future__ import annotations

import json
from pathlib import Path

from openjarvis.core.presence import (
    STATE_AWAY,
    STATE_DISABLED,
    STATE_PRESENT,
    STATE_UNKNOWN,
    PresenceMonitor,
    PresenceSettings,
    decide_state,
    load_settings,
    save_settings,
)


class TestDecideState:
    def test_recent_input_is_present(self) -> None:
        assert decide_state(10, 300) == STATE_PRESENT

    def test_long_idle_is_away(self) -> None:
        assert decide_state(301, 300) == STATE_AWAY

    def test_the_threshold_itself_is_away(self) -> None:
        assert decide_state(300, 300) == STATE_AWAY

    def test_an_unreadable_sensor_is_unknown_not_away(self) -> None:
        # A sensor failure must never read as an empty desk.
        assert decide_state(None, 300) == STATE_UNKNOWN


class TestSettings:
    def test_defaults_are_off(self, tmp_path: Path) -> None:
        settings = load_settings(tmp_path)
        assert settings.enabled is False
        assert settings.idle_threshold_seconds == 300

    def test_round_trip(self, tmp_path: Path) -> None:
        save_settings(
            PresenceSettings(enabled=True, idle_threshold_seconds=120), tmp_path
        )
        loaded = load_settings(tmp_path)
        assert loaded.enabled is True
        assert loaded.idle_threshold_seconds == 120

    def test_a_damaged_file_falls_back_to_defaults(self, tmp_path: Path) -> None:
        (tmp_path / "presence.json").write_text("{not json", encoding="utf-8")
        assert load_settings(tmp_path).enabled is False

    def test_bad_values_are_ignored_not_trusted(self, tmp_path: Path) -> None:
        (tmp_path / "presence.json").write_text(
            json.dumps({"enabled": "yes", "idle_threshold_seconds": -5}),
            encoding="utf-8",
        )
        settings = load_settings(tmp_path)
        assert settings.enabled is False
        assert settings.idle_threshold_seconds == 300


class _Desk:
    """A fake desk: set idle seconds and the clock by hand."""

    def __init__(self) -> None:
        self.idle: float | None = 0.0
        self.now = 1_000_000.0
        self.title: str | None = "Editor"

    def monitor(self, tmp_path: Path) -> PresenceMonitor:
        return PresenceMonitor(
            config_dir=tmp_path,
            idle_sensor=lambda: self.idle,
            foreground_sensor=lambda: self.title,
            clock=lambda: self.now,
        )


class TestMonitor:
    def test_disabled_reads_no_sensors(self, tmp_path: Path) -> None:
        desk = _Desk()
        reads = []
        monitor = PresenceMonitor(
            config_dir=tmp_path,
            idle_sensor=lambda: reads.append(1) or 0.0,
            foreground_sensor=lambda: "x",
            clock=lambda: desk.now,
        )
        snap = monitor.poll()
        assert snap.state == STATE_DISABLED
        assert reads == []

    def test_enabled_present_then_away_then_back(self, tmp_path: Path) -> None:
        save_settings(
            PresenceSettings(enabled=True, idle_threshold_seconds=300), tmp_path
        )
        desk = _Desk()
        monitor = desk.monitor(tmp_path)

        first = monitor.poll()
        assert first.state == STATE_PRESENT
        assert first.since == desk.now
        assert first.foreground == "Editor"

        desk.now += 600
        desk.idle = 600
        away = monitor.poll()
        assert away.state == STATE_AWAY
        assert away.since == desk.now
        assert away.last_present_at == first.since

        desk.now += 60
        desk.idle = 2
        back = monitor.poll()
        assert back.state == STATE_PRESENT
        assert back.since == desk.now
        assert back.last_away_at == away.since

    def test_since_holds_while_the_state_holds(self, tmp_path: Path) -> None:
        # "Welcome back" needs to know how long the absence was; a since that
        # reset on every poll would make every absence fifteen seconds long.
        save_settings(PresenceSettings(enabled=True), tmp_path)
        desk = _Desk()
        monitor = desk.monitor(tmp_path)
        started = monitor.poll().since
        for _ in range(5):
            desk.now += 15
            desk.idle = 1
            assert monitor.poll().since == started

    def test_the_switch_is_live_without_a_restart(self, tmp_path: Path) -> None:
        desk = _Desk()
        monitor = desk.monitor(tmp_path)
        assert monitor.poll().state == STATE_DISABLED
        save_settings(PresenceSettings(enabled=True), tmp_path)
        assert monitor.poll().state == STATE_PRESENT
        save_settings(PresenceSettings(enabled=False), tmp_path)
        assert monitor.poll().state == STATE_DISABLED

    def test_a_sensor_failure_is_unknown(self, tmp_path: Path) -> None:
        save_settings(PresenceSettings(enabled=True), tmp_path)
        desk = _Desk()
        desk.idle = None
        snap = desk.monitor(tmp_path).poll()
        assert snap.state == STATE_UNKNOWN
        assert "could not be read" in snap.reason

    def test_the_reason_is_human(self, tmp_path: Path) -> None:
        save_settings(
            PresenceSettings(enabled=True, idle_threshold_seconds=300), tmp_path
        )
        desk = _Desk()
        desk.idle = 42
        assert "42s ago" in desk.monitor(tmp_path).poll().reason
        desk.idle = 900
        assert "no input for 900s" in desk.monitor(tmp_path).poll().reason

    def test_snapshot_serializes(self, tmp_path: Path) -> None:
        save_settings(PresenceSettings(enabled=True), tmp_path)
        d = _Desk().monitor(tmp_path).poll().to_dict()
        assert d["state"] == STATE_PRESENT
        assert {"state", "idle_seconds", "since", "reason"} <= set(d)
