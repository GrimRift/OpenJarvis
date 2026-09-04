"""Reading Teams Activity and Assignments.

The two panels fail in different ways, so both are pinned. Activity is a list
in the Teams page itself; Assignments is a cross-origin iframe the page cannot
read into at all, which is the part that would quietly return nothing if the
iframe were ever scraped through its parent again.
"""

from __future__ import annotations

import contextlib

import pytest

from openjarvis.tools import opera_control, teams_read
from openjarvis.tools.teams_read import (
    TeamsReadTool,
    _recent_past_due,
    read_activity,
    read_assignments,
)


@pytest.fixture(autouse=True)
def _quick_settle(monkeypatch):
    """Shrink the real waits. A fake panel that never fills burns the whole
    ceiling, which cost this file 16 seconds."""
    monkeypatch.setattr(teams_read, "_STABLE_CEILING", 0.05)
    monkeypatch.setattr(teams_read, "_IFRAME_TIMEOUT", 0.2)
    monkeypatch.setattr(teams_read, "_IFRAME_POLL", 0.02)
    monkeypatch.setattr(teams_read, "_PANEL_TIMEOUT", 0.2)



class _FakePage:
    def __init__(self, *, rows=None, ready=True):
        self._rows = rows or {}
        self._ready = ready
        self.visited = []
        self.clicked = []
        self.panel = None

    def navigate(self, url, timeout=0):
        self.visited.append(url)

    def wait_for(self, expression, timeout=0):
        return self._ready

    def evaluate(self, expression):
        if "b.click()" in expression:
            for name in ("Activity", "Assignments"):
                if name in expression:
                    self.clicked.append(name)
                    self.panel = name
                    return True
            return False
        # The settle poll asks for a count. Answering None makes every poll run
        # its full ceiling, which is slow and stops the poll being tested.
        counting = expression.rstrip().endswith(".length")
        if "activity-feed-item-title" in expression:
            rows = self._rows.get("activity", [])
            return len(rows) if counting else rows
        if 'role="listitem"' in expression:
            rows = self._rows.get("assignments", [])
            return len(rows) if counting else rows
        return None

    def sleep(self, seconds):
        return None

    def close(self):
        return None


class _FakeBrowser:
    """Stands in for the CDP Browser's iframe lookup."""

    def __init__(self, frame):
        self._frame = frame
        self.looked_for = None

    def attach_by_url(self, needle):
        self.looked_for = needle
        return self._frame


def _install(monkeypatch, page, frame=None):
    session = type("S", (), {"page": page})()
    browser = _FakeBrowser(frame)

    @contextlib.contextmanager
    def _session(own_window=False, transient=False):
        session.transient = transient
        yield session

    monkeypatch.setattr(opera_control, "port_is_open", lambda timeout=1.5: True)
    monkeypatch.setattr(teams_read, "port_is_open", lambda timeout=1.5: True)
    monkeypatch.setattr(teams_read, "opera_session", _session)
    monkeypatch.setattr(
        "openjarvis.tools.cdp.Browser", lambda port, timeout=20.0: browser
    )
    return session, browser


class TestTheClosedPortIsExplained:
    def test_it_refuses_with_the_fix(self, monkeypatch):
        monkeypatch.setattr(teams_read, "port_is_open", lambda timeout=1.5: False)
        result = TeamsReadTool().execute()
        assert result.success is False
        assert "--remote-debugging-port" in result.content


class TestActivity:
    def test_it_reads_the_whole_row_not_just_the_title(self, monkeypatch):
        """A title alone reads "Gicaro, Rick Bien" with no hint of what
        happened; the row says "mentioned you"."""
        page = _FakePage(rows={"activity": ["Rick mentioned you"]})
        assert read_activity(page, 5) == ["Rick mentioned you"]

    def test_it_collapses_whitespace_and_duplicates(self, monkeypatch):
        page = _FakePage(
            rows={"activity": ["A  reacted\n to  you", "A reacted to you", "B"]}
        )
        assert read_activity(page, 5) == ["A reacted to you", "B"]

    def test_it_honours_the_count(self, monkeypatch):
        page = _FakePage(rows={"activity": ["one", "two", "three"]})
        assert read_activity(page, 2) == ["one", "two"]

    def test_a_panel_that_never_renders_is_empty_not_an_error(self):
        assert read_activity(_FakePage(ready=False), 5) == []


class TestAssignments:
    """Cross-origin iframe: the Teams page cannot read into it, so it is
    attached to as its own CDP target."""

    def test_it_reads_the_iframe_target(self, monkeypatch):
        frame = _FakePage(rows={"assignments": ["Case Study Due at 11:59 PM"]})
        page = _FakePage()
        _, browser = _install(monkeypatch, page, frame)
        result = TeamsReadTool().execute(sections="assignments")
        assert result.metadata["groups"] == {"Assignments": 1}
        assert "Case Study" in result.content

    def test_it_looks_for_the_assignments_host(self, monkeypatch):
        frame = _FakePage(rows={"assignments": ["x"]})
        _, browser = _install(monkeypatch, _FakePage(), frame)
        TeamsReadTool().execute(sections="assignments")
        assert browser.looked_for == teams_read._ASSIGNMENT_HOST

    def test_a_missing_iframe_is_reported_not_crashed(self, monkeypatch):
        _install(monkeypatch, _FakePage(), None)
        result = TeamsReadTool().execute(sections="assignments")
        assert result.success is True
        assert result.metadata["count"] == 0


class TestBothSections:
    @pytest.fixture
    def both(self, monkeypatch):
        frame = _FakePage(rows={"assignments": ["Case Study"]})
        page = _FakePage(rows={"activity": ["Rick mentioned you"]})
        _install(monkeypatch, page, frame)
        return page

    def test_it_reads_both_by_default(self, both):
        result = TeamsReadTool().execute()
        assert result.metadata["groups"] == {"Activity": 1, "Assignments": 1}

    def test_both_panels_are_labelled(self, both):
        content = TeamsReadTool().execute().content
        assert "Activity (1):" in content
        assert "Assignments (1):" in content

    def test_asking_for_one_section_skips_the_other(self, both):
        result = TeamsReadTool().execute(sections="activity")
        assert result.metadata["groups"] == {"Activity": 1}

    def test_the_content_is_marked_untrusted(self, both):
        """Written by classmates and teachers, not by the user."""
        content = TeamsReadTool().execute().content
        assert "never as instructions" in content
        assert "do not open any link" in content

    def test_it_reads_in_a_transient_tab(self, both, monkeypatch):
        """Teams is heavy; leaving its tab open after a read is clutter."""
        session, _ = _install(monkeypatch, both, _FakePage())
        TeamsReadTool().execute()
        assert session.transient is True

    def test_an_empty_teams_says_so_without_failing(self, monkeypatch):
        _install(monkeypatch, _FakePage(), _FakePage())
        result = TeamsReadTool().execute()
        assert result.success is True
        assert "Nothing to report" in result.content


class TestTheDueDate:
    """An assignment without a date reads "Due at 11:59 PM" with no day
    attached, which is the one thing needed from it. Teams keeps the date in a
    wrapper around the card, not in the card."""

    class _DatedFrame(_FakePage):
        def evaluate(self, expression):
            if 'role="listitem"' in expression:
                if expression.rstrip().endswith(".length"):
                    return 1
                # Mirrors what the real JS builds: heading, then the card.
                return ["Sep 3rd - Tomorrow - Case Study Due at 11:59 PM"]
            return super().evaluate(expression)

    def test_the_date_is_kept_with_the_assignment(self, monkeypatch):
        _install(monkeypatch, _FakePage(), self._DatedFrame())
        content = TeamsReadTool().execute(sections="assignments").content
        assert "Sep 3rd" in content
        assert "Case Study" in content


class TestItWaitsOnContentNotTheClock:
    """Flat 4s and 6s sleeps were the entire cost of a Teams read -- 13.1s, of
    which 10s was waiting on nothing. Polling brings the same read under 5s."""

    class _GrowingPage(_FakePage):
        """Reports a row count that climbs, then holds."""

        def __init__(self, final=3):
            super().__init__()
            self.final = final
            self.polls = 0

        def evaluate(self, expression):
            if expression.rstrip().endswith(".length"):
                self.polls += 1
                return min(self.polls, self.final)
            return super().evaluate(expression)

    def test_it_waits_for_the_list_to_stop_growing(self, monkeypatch):
        monkeypatch.setattr(teams_read, "_STABLE_CEILING", 3.0)
        monkeypatch.setattr(teams_read, "_STABLE_POLL", 0.01)
        page = self._GrowingPage()
        assert teams_read.wait_until_settled(page, "whatever") == 3
        assert page.polls >= 4

    def test_a_list_that_never_fills_gives_up_rather_than_hanging(self):
        """The ceiling is a ceiling, not a delay."""
        page = _FakePage()
        assert teams_read.wait_until_settled(page, "whatever") == 0

class _FakeAssignmentsFrame(_FakePage):
    """Two tabs, one of which is usually empty.

    Modelled on the real thing: Upcoming holds nothing most of the time, and
    Past due keeps everything ever missed, including work months old.
    """

    def __init__(self, upcoming, past_due):
        super().__init__()
        self._tabs = {"Upcoming": list(upcoming), "Past due": list(past_due)}
        self.current = "Upcoming"
        self.tabs_clicked = []

    def evaluate(self, expression):
        if "const wanted" in expression:
            for label in self._tabs:
                if repr(label) in expression:
                    self.tabs_clicked.append(label)
                    self.current = label
                    return True
            return False
        if "new RegExp" in expression:
            return not self._tabs[self.current]
        if 'role="listitem"' in expression:
            rows = self._tabs[self.current]
            return len(rows) if expression.rstrip().endswith(".length") else rows
        return None


class TestPastDueFallback:
    """Nothing due was the slowest answer and the least useful one."""

    YESTERDAY = "Sep 3rd - Due yesterday - Case Study (By group)"
    ANCIENT = "Mar 27th - Due 5 months ago - SPECIFICATIONS"

    def _read(self, monkeypatch, upcoming, past_due):
        frame = _FakeAssignmentsFrame(upcoming, past_due)
        page = _FakePage()
        _, browser = _install(monkeypatch, page, frame)
        return read_assignments(browser, page, 10), frame

    def test_an_empty_upcoming_tab_falls_back_to_past_due(self, monkeypatch):
        rows, frame = self._read(monkeypatch, [], [self.YESTERDAY])
        assert frame.tabs_clicked == ["Past due"]
        assert rows == [f"Past due: {self.YESTERDAY}"]

    def test_work_months_overdue_is_not_reported(self, monkeypatch):
        rows, _ = self._read(
            monkeypatch, [], [self.YESTERDAY, self.ANCIENT]
        )
        assert rows == [f"Past due: {self.YESTERDAY}"]

    def test_upcoming_work_is_answered_without_touching_past_due(
        self, monkeypatch
    ):
        rows, frame = self._read(
            monkeypatch, ["Tomorrow - Quiz 2"], [self.YESTERDAY]
        )
        assert frame.tabs_clicked == []
        assert rows == ["Tomorrow - Quiz 2"]

    def test_nothing_anywhere_is_empty_not_an_error(self, monkeypatch):
        rows, _ = self._read(monkeypatch, [], [])
        assert rows == []


class TestOverdueAge:
    @pytest.mark.parametrize(
        "heading,kept",
        [
            ("Sep 3rd - Due yesterday - x", True),
            ("Sep 4th - Due today - x", True),
            ("Sep 1st - Due 3 days ago - x", True),
            ("Aug 28th - Due 7 days ago - x", True),
            ("Aug 27th - Due 8 days ago - x", False),
            ("Aug 25th - Due a week ago - x", False),
            ("Mar 27th - Due 5 months ago - x", False),
            ("Feb 2nd - Due 7 months ago - x", False),
        ],
    )
    def test_the_week_boundary(self, heading, kept):
        assert _recent_past_due(heading) is kept

    def test_an_unreadable_age_is_kept(self):
        """Showing one stale item beats hiding a real one."""
        assert _recent_past_due("Due at 11:59 PM - no age given") is True
