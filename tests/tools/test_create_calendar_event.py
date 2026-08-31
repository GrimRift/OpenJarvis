"""Tests for the calendar create-event tool.

The date guards here are not hypothetical. This model is documented to get
weekdays and years wrong with the correct date in its prompt — live, it wrote
"Friday, August 22, 2026" in the same response where it had just written
"Saturday, August 22, 2026". A wrong year on a calendar write is invisible: the
event lands somewhere the user never scrolls to.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from openjarvis.tools.create_calendar_event import CreateCalendarEventTool


@pytest.fixture()
def connector():
    stub = MagicMock()
    stub.is_connected.return_value = True
    stub.create_event.return_value = {
        "id": "evt_123",
        "htmlLink": "https://calendar.google.com/event?eid=evt_123",
    }
    return stub


@pytest.fixture()
def tool(connector):
    return CreateCalendarEventTool(connector=connector, timezone="Asia/Singapore")


def _soon(**kw) -> str:
    return (datetime.now() + timedelta(**kw)).replace(microsecond=0).isoformat(
        timespec="minutes"
    )


class TestHappyPath:
    def test_creates_the_event_and_reports_the_weekday(self, tool, connector):
        start = _soon(days=1)
        result = tool.execute(summary="Dentist appointment", start=start)

        assert result.success
        assert connector.create_event.call_count == 1
        kwargs = connector.create_event.call_args.kwargs
        assert kwargs["summary"] == "Dentist appointment"
        assert kwargs["timezone"] == "Asia/Singapore"
        # The weekday is echoed so a wrong date is visible in the reply.
        expected_day = datetime.fromisoformat(start).strftime("%A")
        assert expected_day in result.content
        assert result.metadata["event_id"] == "evt_123"

    def test_default_duration_is_an_hour(self, tool, connector):
        tool.execute(summary="Standup", start=_soon(days=1))
        kwargs = connector.create_event.call_args.kwargs
        start = datetime.fromisoformat(kwargs["start"])
        end = datetime.fromisoformat(kwargs["end"])
        assert end - start == timedelta(hours=1)

    def test_explicit_end_wins_over_duration(self, tool, connector):
        start = _soon(days=1)
        end = (datetime.fromisoformat(start) + timedelta(minutes=30)).isoformat(
            timespec="minutes"
        )
        tool.execute(summary="Call", start=start, end=end, duration_minutes=90)
        kwargs = connector.create_event.call_args.kwargs
        assert datetime.fromisoformat(kwargs["end"]) == datetime.fromisoformat(end)

    def test_datetimes_carry_seconds(self, tool, connector):
        """Google wants RFC3339 and rejects minute precision.

        Caught live: "2026-09-02T12:00" came back as a bare 400 Bad Request
        with no explanation, and the identical body with ":00" appended
        succeeded. This is the whole difference.
        """
        tool.execute(summary="Sync", start=_soon(days=1))
        kwargs = connector.create_event.call_args.kwargs
        for field in ("start", "end"):
            assert len(kwargs[field]) == len("2026-09-02T12:00:00"), kwargs[field]
            assert kwargs[field].count(":") == 2, kwargs[field]

    def test_local_time_is_sent_with_a_zone_not_a_utc_instant(self, tool, connector):
        """Google resolves wall-clock time from the zone; an offset would shift it."""
        tool.execute(summary="Lunch", start=_soon(days=1))
        kwargs = connector.create_event.call_args.kwargs
        assert "+" not in kwargs["start"] and not kwargs["start"].endswith("Z")
        assert kwargs["timezone"] == "Asia/Singapore"

    def test_an_offset_in_the_input_is_discarded(self, tool, connector):
        """A model that appends the wrong offset must not move the event."""
        start = datetime.now() + timedelta(days=1)
        tool.execute(
            summary="Sync",
            start=start.replace(microsecond=0).isoformat(timespec="minutes") + "+05:00",
        )
        kwargs = connector.create_event.call_args.kwargs
        assert kwargs["start"].startswith(start.strftime("%Y-%m-%dT%H:%M"))


class TestGuardsAgainstAWrongDate:
    def test_a_past_start_is_refused(self, tool, connector):
        """Almost always a mistyped year, and invisible once written."""
        result = tool.execute(summary="Dentist", start=_soon(days=-30))
        assert not result.success
        assert "past" in result.content.lower()
        connector.create_event.assert_not_called()

    def test_the_refusal_names_the_date_it_read(self, tool):
        result = tool.execute(summary="Dentist", start="2025-09-02T15:00")
        assert "2025" in result.content and "September" in result.content

    def test_a_moment_ago_is_still_allowed(self, tool, connector):
        """Refusing anything past would block 'book the meeting that just started'."""
        result = tool.execute(summary="Retro", start=_soon(minutes=-5))
        assert result.success
        connector.create_event.assert_called_once()

    def test_end_before_start_is_refused(self, tool, connector):
        start = _soon(days=1)
        end = (datetime.fromisoformat(start) - timedelta(hours=1)).isoformat(
            timespec="minutes"
        )
        result = tool.execute(summary="Call", start=start, end=end)
        assert not result.success
        connector.create_event.assert_not_called()


class TestBadInput:
    @pytest.mark.parametrize("start", ["", "tomorrow at 3pm", "2026-13-45T99:00"])
    def test_unparseable_start_is_refused(self, tool, connector, start):
        result = tool.execute(summary="Thing", start=start)
        assert not result.success
        connector.create_event.assert_not_called()

    def test_a_missing_title_is_refused(self, tool, connector):
        result = tool.execute(summary="   ", start=_soon(days=1))
        assert not result.success
        connector.create_event.assert_not_called()

    def test_an_absurd_duration_is_refused(self, tool, connector):
        result = tool.execute(
            summary="Thing", start=_soon(days=1), duration_minutes=999999
        )
        assert not result.success
        connector.create_event.assert_not_called()


class TestFailsClosed:
    def test_a_disconnected_calendar_says_how_to_connect(self, connector):
        connector.is_connected.return_value = False
        tool = CreateCalendarEventTool(connector=connector, timezone="UTC")
        result = tool.execute(summary="Thing", start=_soon(days=1))
        assert not result.success
        assert "jarvis connect gcalendar" in result.content
        connector.create_event.assert_not_called()

    def test_an_api_error_is_reported_not_raised(self, tool, connector):
        connector.create_event.side_effect = RuntimeError("403 insufficient scope")
        result = tool.execute(summary="Thing", start=_soon(days=1))
        assert not result.success
        assert "403" in result.content


class TestSpec:
    def test_it_does_not_ask_for_a_confirmation_nothing_can_answer(self, tool):
        """requires_confirmation=True would disable the tool, not guard it.

        Nothing in the chat path supplies a confirm_callback, so ToolExecutor
        fails such a call outright: "requires confirmation but no confirmation
        callback is available." Caught live — the first real attempt to create
        an event came back "confirmation isn't available in this session".
        Flip this to True only alongside an actual confirmation UI.
        """
        assert tool.spec.requires_confirmation is False

    def test_it_is_registered(self):
        """Reloaded on purpose, not imported.

        conftest's autouse ``_clean_registries`` empties the registry before
        every test, and a plain import is a no-op once the module is cached, so
        the decorator never re-runs. That is why
        ``test_check_class_schedule_registered`` is on the known-flaky list —
        it passes in a full run and fails alone. Reloading re-executes the
        decorator against the cleared registry, so this holds either way.
        """
        import importlib

        import openjarvis.tools.create_calendar_event as module
        from openjarvis.core.registry import ToolRegistry

        importlib.reload(module)
        assert ToolRegistry.contains("create_calendar_event")

    def test_the_package_imports_it(self):
        """Registration only helps if `openjarvis.tools` actually pulls it in."""
        from pathlib import Path

        init = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "openjarvis"
            / "tools"
            / "__init__.py"
        )
        assert "create_calendar_event" in init.read_text(encoding="utf-8")
