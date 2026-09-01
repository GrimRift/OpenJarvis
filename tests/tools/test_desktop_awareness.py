"""Desktop awareness: monitors, window listing, and what gets withheld.

The monitor half matters more than it looks. On this user's setup the laptop
panel sits at x=-1920, left of the primary, so a window's raw coordinates say
nothing until you know which screen they land on.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from openjarvis.security.screen_redaction import REDACTED
from openjarvis.tools.desktop_awareness import ListWindowsTool
from openjarvis.tools.desktop_monitors import (
    Monitor,
    describe_monitors,
    monitor_for,
)

#: The real layout on the user's machine: external primary, laptop to its left.
EXTERNAL = Monitor(
    index=2,
    device=r"\\.\DISPLAY2",
    name="Generic PnP Monitor",
    width=1920,
    height=1080,
    x=0,
    y=0,
    is_primary=True,
)
LAPTOP = Monitor(
    index=1,
    device=r"\\.\DISPLAY1",
    name="Generic PnP Monitor",
    width=1536,
    height=960,
    x=-1920,
    y=0,
    is_primary=False,
)
MONITORS = [EXTERNAL, LAPTOP]


def _window(title: str, x: int = 100, y: int = 100, foreground: bool = False):
    return {
        "title": title,
        "handle": 1,
        "x": x,
        "y": y,
        "width": 800,
        "height": 600,
        "foreground": foreground,
    }


class TestMonitorGeometry:
    def test_a_window_on_the_primary_is_found(self):
        assert monitor_for(MONITORS, 500, 400) is EXTERNAL

    def test_a_negative_x_lands_on_the_laptop(self):
        """The whole reason monitors are reported at all."""
        assert monitor_for(MONITORS, -1400, 400) is LAPTOP

    def test_a_point_on_no_screen_returns_nothing(self):
        assert monitor_for(MONITORS, 99999, 99999) is None

    def test_the_main_and_second_screens_are_named_that_way(self):
        """"main"/"second" are the words the user says; "primary" is Windows'."""
        text = describe_monitors(MONITORS)
        assert "(main" in text
        assert "(second" in text

    def test_the_largest_is_called_out_when_it_is_not_primary(self):
        """The user picks a main screen by size; Windows picks by setting."""
        flipped = [
            Monitor(**{**EXTERNAL.__dict__, "is_primary": False}),
            Monitor(**{**LAPTOP.__dict__, "is_primary": True}),
        ]
        assert "largest" in describe_monitors(flipped)

    def test_no_displays_does_not_crash(self):
        assert "No displays" in describe_monitors([])


class TestListWindows:
    def _run(self, windows):
        with patch(
            "openjarvis.tools.desktop_awareness._visible_windows", return_value=windows
        ), patch(
            "openjarvis.tools.desktop_awareness.list_monitors", return_value=MONITORS
        ):
            return ListWindowsTool().execute()

    def test_it_reports_which_monitor_each_window_is_on(self):
        result = self._run([_window("Editor", x=200), _window("Chat", x=-1500)])
        assert "monitor 2" in result.content
        assert "monitor 1" in result.content

    def test_the_foreground_window_is_marked(self):
        result = self._run([_window("Editor", foreground=True), _window("Chat")])
        assert "[FOREGROUND]" in result.content
        assert result.content.count("[FOREGROUND]") == 1

    def test_a_sensitive_title_is_withheld(self):
        result = self._run([_window("1Password"), _window("Editor")])
        assert REDACTED in result.content
        assert "1Password" not in result.content
        assert result.metadata["redacted_count"] == 1

    def test_ordinary_titles_are_reported_verbatim(self):
        result = self._run([_window("HANDOFF.md - Visual Studio Code")])
        assert "HANDOFF.md - Visual Studio Code" in result.content
        assert result.metadata["redacted_count"] == 0

    def test_an_empty_desktop_is_reported_not_failed(self):
        result = self._run([])
        assert result.success is True
        assert "No visible windows" in result.content

    def test_a_windows_api_failure_is_reported_not_raised(self):
        with patch(
            "openjarvis.tools.desktop_awareness.list_monitors",
            side_effect=OSError("no display"),
        ):
            result = ListWindowsTool().execute()
        assert result.success is False
        assert "no display" in result.content


class TestItIsReadOnly:
    """M32 ships awareness before actions. Nothing here may click or type."""

    @pytest.mark.parametrize("forbidden", ["Click", "SendKeys", "SetFocus", "Invoke"])
    def test_no_interaction_calls_exist(self, forbidden):
        import ast
        import inspect

        from openjarvis.tools import desktop_awareness

        tree = ast.parse(inspect.getsource(desktop_awareness))
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert forbidden not in called


class TestMonitorNumbering:
    """The main screen must be Monitor 1, whatever Windows calls it.

    Windows enumerates by device order. On this user's machine the laptop is
    DISPLAY1 and the external they call "main" is the primary, so device
    numbering made "look at my second monitor" capture the main one — and
    describe it confidently. A wrong answer that looks like a working feature
    is worse than an error.
    """

    def test_the_primary_becomes_monitor_one(self):
        from openjarvis.tools.desktop_monitors import number_from_primary

        # Device order: laptop first, external (primary) second.
        renumbered = number_from_primary([LAPTOP, EXTERNAL])
        assert renumbered[0].is_primary is True
        assert renumbered[0].index == 1
        assert renumbered[0].device == EXTERNAL.device

    def test_the_other_screen_becomes_monitor_two(self):
        from openjarvis.tools.desktop_monitors import number_from_primary

        renumbered = number_from_primary([LAPTOP, EXTERNAL])
        assert renumbered[1].index == 2
        assert renumbered[1].device == LAPTOP.device

    def test_indices_are_contiguous_from_one(self):
        from openjarvis.tools.desktop_monitors import number_from_primary

        renumbered = number_from_primary([LAPTOP, EXTERNAL])
        assert [m.index for m in renumbered] == [1, 2]

    def test_a_single_screen_is_monitor_one(self):
        from openjarvis.tools.desktop_monitors import number_from_primary

        assert number_from_primary([EXTERNAL])[0].index == 1

    def test_the_main_screen_is_described_as_main(self):
        from openjarvis.tools.desktop_monitors import number_from_primary

        text = describe_monitors(number_from_primary([LAPTOP, EXTERNAL]))
        assert "Monitor 1 (main" in text
        assert "Monitor 2 (second" in text
