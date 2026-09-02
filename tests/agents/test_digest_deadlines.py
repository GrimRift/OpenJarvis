"""Deadlines must survive the briefing.

Every case here is a way the assignment due the next day went missing. It was
collected correctly each time; what failed was everything after collection.
"""

from __future__ import annotations

import pytest

from openjarvis.agents import morning_digest
from openjarvis.agents.morning_digest import (
    MorningDigestAgent,
    _deadline_lines,
    _deadline_preamble,
)

ASSIGNMENT = (
    "Sep 3rd - Tomorrow - Case Study (By group) Due at 11:59 PM "
    "T1 AY2627 ENEMAN20 BSCE231P2 100 points"
)


class _Result:
    def __init__(self, content="ok", success=True, metadata=None):
        self.content = content
        self.success = success
        self.metadata = metadata or {}


class TestPullingDeadlinesOutOfTheTools:
    """Read from tool metadata, not by re-parsing the tool's own prose."""

    def test_it_finds_assignments(self):
        result = _Result(metadata={"items": {"Assignments": [ASSIGNMENT]}})
        assert "Case Study" in _deadline_lines([("TEAMS", result)])

    def test_activity_is_not_a_deadline(self):
        result = _Result(
            metadata={"items": {"Activity": ["Rick mentioned you"]}}
        )
        assert _deadline_lines([("TEAMS", result)]) == ""

    def test_it_keeps_assignments_and_drops_the_rest(self):
        result = _Result(
            metadata={
                "items": {
                    "Activity": ["Rick mentioned you"],
                    "Assignments": [ASSIGNMENT],
                }
            }
        )
        lines = _deadline_lines([("TEAMS", result)])
        assert "Case Study" in lines
        assert "mentioned you" not in lines

    def test_a_tool_with_no_items_is_not_an_error(self):
        assert _deadline_lines([("MAIL", _Result(metadata={"count": 3}))]) == ""

    def test_nothing_collected_is_empty(self):
        assert _deadline_lines([]) == ""


class TestTheGuaranteeWhenTheModelDropsIt:
    """The model was told three ways to lead with the deadline — in the
    section prompt, in the absolute rules, and as a REQUIRED line outside the
    evidence block. It dropped the assignment every time, and once opened with
    "no urgent deadlines", which is worse than silence because it is
    confidently wrong. So the guarantee is made in code."""

    LINE = f"- {ASSIGNMENT}"

    def test_a_dropped_deadline_is_prepended(self):
        preamble = _deadline_preamble(self.LINE, "Sir, you have mail.")
        assert preamble.startswith("First, a deadline:")
        assert "Case Study" in preamble

    def test_a_deadline_the_briefing_covered_is_not_repeated(self):
        narrative = (
            "Sir, the Case Study by group for ENEMAN20 BSCE231P2 is due "
            "tomorrow at 11:59 PM."
        )
        assert _deadline_preamble(self.LINE, narrative) == ""

    def test_no_deadlines_means_no_preamble(self):
        assert _deadline_preamble("", "Sir, you have mail.") == ""

    def test_several_missing_deadlines_are_numbered(self):
        two = f"- {ASSIGNMENT}\n- Sep 9th - Lab Report Due at 5:00 PM PHYS101"
        preamble = _deadline_preamble(two, "Sir, you have mail.")
        assert preamble.startswith("First, your deadlines:")
        assert "1." in preamble and "2." in preamble

    def test_it_only_re_states_the_ones_that_are_missing(self):
        two = f"- {ASSIGNMENT}\n- Sep 9th - Lab Report Due at 5:00 PM PHYS101"
        narrative = "Sir, your Lab Report for PHYS101 is due on the 9th."
        preamble = _deadline_preamble(two, narrative)
        assert "Case Study" in preamble
        assert "Lab Report" not in preamble


class TestTheAgentFetchesItsOwnSources:
    """Outlook and Teams have no working connector, so the agent calls the
    browser tools. It builds them itself: there are two agent construction
    sites, and wiring only ``system/orchestrator.py`` produced a briefing that
    collected Gmail fine and answered "Unknown tool: teams_read" — losing the
    assignment without failing."""

    def _agent(self, sections):
        return MorningDigestAgent.__new__(MorningDigestAgent), sections

    def _collect(self, monkeypatch, sections, calls):
        def _run(tool_name, count=8):
            calls.append(tool_name)
            return _Result(
                content=f"{tool_name} data",
                metadata={"items": {"Assignments": [ASSIGNMENT]}},
            )

        monkeypatch.setattr(morning_digest, "_run_browser_tool", _run)
        agent = MorningDigestAgent.__new__(MorningDigestAgent)
        agent._sections = sections
        return agent._collect_browser_sources()

    def test_both_sources_are_read_when_configured(self, monkeypatch):
        calls = []
        got = self._collect(monkeypatch, ["messages", "teams"], calls)
        assert calls == ["outlook_read", "teams_read"]
        assert [label for label, _ in got] == ["OUTLOOK MAIL", "MICROSOFT TEAMS"]

    def test_turning_off_teams_stops_paying_for_the_slow_one(self, monkeypatch):
        calls = []
        self._collect(monkeypatch, ["messages"], calls)
        assert calls == ["outlook_read"]

    def test_no_configured_sections_reads_nothing(self, monkeypatch):
        calls = []
        assert self._collect(monkeypatch, ["calendar"], calls) == []
        assert calls == []

    def test_a_failing_source_costs_only_that_source(self, monkeypatch):
        def _run(tool_name, count=8):
            if tool_name == "teams_read":
                raise RuntimeError("browser is closed")
            return _Result(content="mail")

        monkeypatch.setattr(morning_digest, "_run_browser_tool", _run)
        agent = MorningDigestAgent.__new__(MorningDigestAgent)
        agent._sections = ["messages", "teams"]
        got = agent._collect_browser_sources()
        assert [label for label, _ in got] == ["OUTLOOK MAIL"]

    def test_an_unsuccessful_result_is_not_treated_as_data(self, monkeypatch):
        monkeypatch.setattr(
            morning_digest,
            "_run_browser_tool",
            lambda name, count=8: _Result(content="Unknown tool", success=False),
        )
        agent = MorningDigestAgent.__new__(MorningDigestAgent)
        agent._sections = ["messages", "teams"]
        assert agent._collect_browser_sources() == []


class TestTheTeamsSectionIsDescribed:
    def test_teams_has_its_own_section_prompt(self):
        """Folded into Messages, a due date reads as one more notification."""
        assert "teams" in morning_digest._SECTION_PROMPTS
        assert "due date" in morning_digest._SECTION_PROMPTS["teams"]

    @pytest.mark.parametrize(
        "section,tool", [("messages", "outlook_read"), ("teams", "teams_read")]
    )
    def test_each_browser_source_is_tied_to_a_section(self, section, tool):
        pairs = {(s, t) for s, t, _ in morning_digest._BROWSER_SOURCES}
        assert (section, tool) in pairs
