"""Tests for the world_time tool — resolution, arithmetic, and phrasing."""

from __future__ import annotations

import json
from datetime import timedelta, timezone
from unittest.mock import patch

from openjarvis.tools.world_time import (
    WorldTimeTool,
    _format_offset,
    _resolve_zone,
)


def _run(**params):
    result = WorldTimeTool().execute(**params)
    return result, (json.loads(result.content) if result.success else None)


class TestZoneResolution:
    def test_resolves_legacy_alias_country(self):
        zone, name = _resolve_zone("Japan")
        assert zone is not None and name == "Japan"

    def test_resolves_country_without_a_zone_of_that_name(self):
        """ZoneInfo('Philippines') raises; only Asia/Manila exists."""
        zone, name = _resolve_zone("Philippines")
        assert zone is not None and name == "Asia/Manila"

    def test_resolves_a_bare_city(self):
        zone, name = _resolve_zone("Tokyo")
        assert zone is not None and name == "Asia/Tokyo"

    def test_resolution_is_case_insensitive(self):
        assert _resolve_zone("new york")[1] == "America/New_York"

    def test_unknown_place_resolves_to_nothing(self):
        assert _resolve_zone("nonsenseville") == (None, "")

    def test_empty_input_resolves_to_nothing(self):
        assert _resolve_zone("")[0] is None


class TestOffsetFormatting:
    def test_whole_hour_offsets(self):
        assert _format_offset(8) == "UTC+8"
        assert _format_offset(-4) == "UTC-4"
        assert _format_offset(0) == "UTC+0"

    def test_fractional_offsets_are_not_rounded_away(self):
        assert _format_offset(5.5) == "UTC+5:30"
        assert _format_offset(5.75) == "UTC+5:45"


class TestArithmetic:
    """The failure this tool exists for: UTC+9 called '3 hours ahead' of UTC+8."""

    def test_japan_is_one_hour_ahead_of_philippine_time(self):
        # compare_to is explicit because the difference is otherwise measured
        # against whatever zone the machine is set to: this passed only on a
        # box in Asia/Manila, and read "9 hours ahead" on a UTC CI runner.
        # The sibling tests below already pass compare_to for the same reason.
        with patch("openjarvis.tools.world_time.datetime") as fake:
            from datetime import datetime as real_datetime

            fake.now.return_value = real_datetime(
                2026, 8, 26, 20, 56, tzinfo=timezone(timedelta(hours=8))
            )
            result, payload = _run(location="Japan", compare_to="Asia/Manila")

        assert result.success
        assert payload["difference"] == "1 hour ahead"
        assert payload["time"] == "9:56 PM"

    def test_half_hour_zone_reports_minutes(self):
        _, payload = _run(location="India")
        assert "30 minutes" in payload["difference"]

    def test_same_zone_reports_no_difference(self):
        _, payload = _run(location="Asia/Manila", compare_to="Asia/Manila")
        assert payload["difference"] == "the same time"
        assert "the same time as" in payload["summary"]

    def test_compare_to_uses_that_zone_not_local(self):
        _, payload = _run(location="Japan", compare_to="London")
        assert payload["compared_to"] == "Europe/London"
        assert "Europe/London" in payload["summary"]


class TestPhrasing:
    def test_ahead_takes_a_preposition_and_behind_does_not(self):
        _, ahead = _run(location="Japan", compare_to="Asia/Manila")
        _, behind = _run(location="Asia/Manila", compare_to="Japan")
        assert "ahead of" in ahead["summary"]
        assert "behind of" not in behind["summary"]
        assert "behind" in behind["summary"]


class TestFailures:
    def test_missing_location_is_rejected(self):
        result, _ = _run()
        assert not result.success
        assert "Missing required parameter" in result.content

    def test_unknown_location_fails_with_guidance(self):
        result, _ = _run(location="nonsenseville")
        assert not result.success
        assert "Asia/Tokyo" in result.content

    def test_unknown_comparison_target_fails(self):
        result, _ = _run(location="Japan", compare_to="nonsenseville")
        assert not result.success
