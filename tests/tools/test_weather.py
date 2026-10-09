"""Tests for the weather tool and the one-line summary the briefings use.

The line exists to answer one question -- do I need an umbrella -- so the
tests are about that, not about reciting a forecast table. The tool's result
also carries the whole report for the weather panel (M41).
"""

from __future__ import annotations

import json
import threading
import time
from unittest.mock import patch

import httpx
import pytest

from openjarvis.connectors import weather as connector
from openjarvis.tools.weather import WeatherTool

CALAMBA = {
    "name": "Calamba",
    "admin1": "Calabarzon",
    "admin2": "Province of Laguna",
    "country": "Philippines",
    "country_code": "PH",
    "latitude": 14.21167,
    "longitude": 121.16528,
    "timezone": "Asia/Manila",
}


def _forecast(*pops: float, now: str = "2026-10-01T12:00", code: int = 3):
    """An Open-Meteo answer whose hours start at *now*, one per rain chance."""
    hours = [f"2026-10-01T{12 + i:02d}:00" for i in range(len(pops))]
    return {
        "timezone": "Asia/Manila",
        "current": {
            "time": now,
            "temperature_2m": 28.4,
            "apparent_temperature": 32.1,
            "relative_humidity_2m": 79,
            "weather_code": code,
            "wind_speed_10m": 11.5,
            "wind_direction_10m": 270,
            "pressure_msl": 1011.6,
            "uv_index": 6.2,
            "is_day": 1,
        },
        "hourly": {
            "time": hours,
            "temperature_2m": [29] * len(pops),
            "precipitation_probability": [round(p * 100) for p in pops],
            "weather_code": [code] * len(pops),
            "is_day": [1] * len(pops),
        },
        "daily": {
            "time": ["2026-10-01", "2026-10-02"],
            "weather_code": [3, 95],
            "temperature_2m_max": [31.9, 32.0],
            "temperature_2m_min": [24.9, 24.5],
            "precipitation_probability_max": [40, 98],
            "uv_index_max": [8.2, 8.1],
            "sunrise": ["2026-10-01T05:44", "2026-10-02T05:44"],
            "sunset": ["2026-10-01T17:45", "2026-10-02T17:44"],
        },
    }


def _report(*pops: float, **kw):
    return connector.build_report(_forecast(*pops, **kw), CALAMBA)


class TestTheBriefingLine:
    def test_it_names_the_hour_rain_becomes_likely(self):
        line = connector.summarize(_report(0.1, 0.62))
        assert line.startswith("28°C, overcast")
        assert "1 PM" in line and "62%" in line

    def test_rain_in_the_hour_under_way_is_now(self):
        """Said at 12:45, "around 12 PM" sounds like it already passed."""
        line = connector.summarize(_report(0.7, now="2026-10-01T12:45"))
        assert line.endswith("rain likely now (70%)")

    def test_a_dry_day_says_so_rather_than_going_quiet(self):
        """Silence would read as "the forecast failed"."""
        assert "no rain expected" in connector.summarize(_report(0.05, 0.1))

    def test_a_drizzle_is_not_worth_an_umbrella(self):
        """Reporting 20% every morning teaches the reader to ignore the line."""
        assert "no rain expected" in connector.summarize(_report(0.35))

    def test_tomorrow_s_rain_is_not_today_s(self):
        """Only the next 12 hours count for a line about today."""
        pops = [0.0] * connector.RAIN_LOOKAHEAD_HOURS + [0.9]
        assert "no rain expected" in connector.summarize(_report(*pops))

    def test_the_car_line_has_no_rain_clause(self):
        assert connector.summarize(_report(0.9), rain=False) == "28°C, overcast"

    def test_units_are_the_reader_s_not_the_provider_s(self):
        imperial = connector.build_report(_forecast(0.1), CALAMBA, units="imperial")
        assert "°F" in connector.summarize(imperial)
        assert "°C" in connector.summarize(_report(0.1))


class TestTheReport:
    def test_hours_start_at_the_current_one(self):
        """Hours already past are not drawn on the panel's curve."""
        report = _report(0.1, 0.2, 0.3, now="2026-10-01T13:15")
        assert [h["time"] for h in report["hourly"]] == [
            "2026-10-01T13:00",
            "2026-10-01T14:00",
        ]

    def test_it_carries_what_the_panel_draws(self):
        report = _report(0.1)
        assert report["place"] == "Calamba, Laguna"
        assert report["source"] == "Open-Meteo"
        assert report["current"]["icon"] == "cloudy"
        assert report["current"]["humidity"] == 79
        assert report["daily"][1]["conditions"] == "thunderstorm"
        assert report["daily"][1]["rain_chance"] == pytest.approx(0.98)
        assert report["daily"][0]["sunrise"] == "2026-10-01T05:44"

    def test_an_unknown_code_is_not_a_crash(self):
        assert connector.describe_code(12345)[0] == "conditions unknown"
        assert connector.describe_code(None)[0] == "conditions unknown"

    @pytest.mark.parametrize(
        "units,wind,expected",
        [("metric", "kmh", "km/h"), ("metric", None, "m/s"), ("imperial", None, "mph")],
    )
    def test_wind_units(self, units, wind, expected):
        assert connector.unit_labels(units, wind)["speed"] == expected


class TestTheTool:
    def _tool(self, tmp_path, **config):
        path = tmp_path / "weather.json"
        # Device location off by default here: these tests are about the tool,
        # and a real Windows fix would make them depend on where the machine is.
        payload = {"place": CALAMBA, "use_device_location": False, **config}
        path.write_text(json.dumps(payload), encoding="utf-8")
        return WeatherTool(token_path=str(path))

    def _run(self, tool, forecast=None, **params):
        with patch.object(
            connector, "fetch_forecast", return_value=forecast or _forecast(0.1)
        ) as f:
            return tool.execute(**params), f

    def test_a_fix_near_home_is_reported_bare(self, tmp_path):
        """The user knows they are home; naming it adds words, not news."""
        tool = self._tool(tmp_path, use_device_location=True)
        with patch(
            "openjarvis.core.device_location.current_coordinates",
            return_value=(14.166, 121.139),
        ):
            result, fetch = self._run(tool)
        assert result.content.startswith("28°C")
        # The fix itself is what the forecast is for; the panel still names home.
        assert fetch.call_args.args[:2] == (14.166, 121.139)
        assert result.metadata["weather"]["place"] == "Calamba, Laguna"

    def test_a_fix_far_from_home_is_not_labelled_home(self, tmp_path):
        tool = self._tool(tmp_path, use_device_location=True)
        with (
            patch(
                "openjarvis.core.device_location.current_coordinates",
                return_value=(10.3157, 123.8854),  # Cebu
            ),
            patch.object(connector, "town_at", return_value={"name": ""}),
        ):
            result, _ = self._run(tool)
        assert result.metadata["weather"]["place"] == ""

    def test_away_from_home_the_town_is_named(self, tmp_path):
        """9 October: the user was in Lucban; the answer says so."""
        tool = self._tool(tmp_path, use_device_location=True)
        with (
            patch(
                "openjarvis.core.device_location.current_coordinates",
                return_value=(14.1177, 121.5494),
            ),
            patch.object(
                connector,
                "town_at",
                return_value={"name": "Lucban", "admin2": "Quezon"},
            ),
        ):
            result, fetch = self._run(tool)
        assert result.content.startswith("Lucban, Quezon: 28°C")
        assert fetch.call_args.args[:2] == (14.1177, 121.5494)

    def test_a_place_the_user_never_said_is_ignored(self, tmp_path):
        """The model wrote "Calamba, Laguna, Philippines" from the profile while
        the user, in Lucban, only asked "will it rain later?"."""
        from openjarvis.security import page_access

        tool = self._tool(tmp_path, use_device_location=True)
        with (
            page_access.scope("whats up, will it rain later?"),
            patch(
                "openjarvis.core.device_location.current_coordinates",
                return_value=(14.1177, 121.5494),
            ),
            patch.object(
                connector,
                "town_at",
                return_value={"name": "Lucban", "admin2": "Quezon"},
            ),
        ):
            result, fetch = self._run(tool, location="Calamba, Laguna, Philippines")
        assert fetch.call_args.args[:2] == (14.1177, 121.5494)
        assert result.content.startswith("Lucban, Quezon:")

    def test_a_follow_up_keeps_the_place_from_the_message_before(self, tmp_path):
        """"weather in Tokyo?" then "and tomorrow?" stays in Tokyo."""
        from openjarvis.security import page_access

        tool = self._tool(tmp_path, use_device_location=True)
        tokyo = {
            **CALAMBA,
            "name": "Tokyo",
            "admin2": "",
            "admin1": "Tokyo",
            "latitude": 35.68,
            "longitude": 139.69,
        }
        with (
            page_access.scope("and tomorrow?", "what's the weather in Tokyo?"),
            patch("openjarvis.core.device_location.current_coordinates") as fix,
            patch.object(connector, "geocode", return_value=tokyo),
        ):
            _, fetch = self._run(tool, location="Tokyo, Japan", day="tomorrow")
        assert fetch.call_args.args[:2] == (35.68, 139.69)
        assert not fix.called

    def test_a_place_the_user_names_still_wins(self, tmp_path):
        from openjarvis.security import page_access

        tool = self._tool(tmp_path, use_device_location=True)
        tokyo = {
            **CALAMBA,
            "name": "Tokyo",
            "admin2": "",
            "admin1": "Tokyo",
            "latitude": 35.68,
            "longitude": 139.69,
        }
        with (
            page_access.scope("what's the weather in Tokyo tomorrow?"),
            patch("openjarvis.core.device_location.current_coordinates") as fix,
            patch.object(connector, "geocode", return_value=tokyo),
        ):
            _, fetch = self._run(tool, location="Tokyo, Japan")
        assert fetch.call_args.args[:2] == (35.68, 139.69)
        assert not fix.called

    def test_a_named_place_is_echoed_back(self, tmp_path):
        """An answer about Tokyo must not be mistaken for one about here."""
        tool = self._tool(tmp_path)
        tokyo = {
            "name": "Tokyo",
            "admin1": "Tokyo",
            "country": "Japan",
            "latitude": 35.69,
            "longitude": 139.69,
        }
        with patch.object(connector, "search_places", return_value=[tokyo]):
            result, fetch = self._run(tool, location="Tokyo")
        assert result.content.startswith("Tokyo")
        assert fetch.call_args.args[:2] == (35.69, 139.69)

    def test_the_configured_place_is_named_because_that_signals_no_fix(self, tmp_path):
        result, fetch = self._run(self._tool(tmp_path))
        assert result.content.startswith("Calamba, Laguna:")
        assert fetch.call_args.args[:2] == (14.21167, 121.16528)

    def test_the_pinned_place_needs_no_lookup(self, tmp_path):
        """Four Calambas in PH: a pinned one is never re-guessed."""
        with patch.object(connector, "search_places") as search:
            self._run(self._tool(tmp_path))
        assert not search.called

    def test_an_old_city_only_file_is_looked_up_by_country(self, tmp_path):
        path = tmp_path / "weather.json"
        path.write_text(
            json.dumps({"location": "Calamba,PH", "use_device_location": False})
        )
        connector._geocode_cache.clear()
        connector._forecast_cache.clear()
        with patch.object(connector, "_weather_api_get") as api:
            api.side_effect = [{"results": [CALAMBA]}, _forecast(0.1)]
            result = WeatherTool(token_path=str(path)).execute()
        assert result.success is True
        geocode_params = api.call_args_list[0].args[1]
        assert geocode_params["name"] == "Calamba"
        assert geocode_params["countryCode"] == "PH"

    def test_no_settings_file_still_answers(self, tmp_path):
        """Open-Meteo needs no key, so a missing file is not 'not configured'."""
        connector._geocode_cache.clear()
        with (
            patch.object(connector, "search_places", return_value=[CALAMBA]),
            patch(
                "openjarvis.core.device_location.current_coordinates", return_value=None
            ),
        ):
            result, _ = self._run(WeatherTool(token_path=str(tmp_path / "absent.json")))
        assert result.success is True

    def test_the_result_carries_the_panel_and_the_week(self, tmp_path):
        result, _ = self._run(self._tool(tmp_path))
        panel = result.metadata["weather"]
        assert panel["summary"] == result.metadata["summary"]
        assert len(panel["daily"]) == 2
        assert "Next days: Thu 25-32°C overcast, rain 40%; Fri" in result.content

    def test_an_hour_s_chance_and_the_rain_around_it_are_in_the_result(self, tmp_path):
        """Reported 7 October: 'rain at 7 AM?' got 'likely' from the summary's
        'rain likely now', while the panel showed 4% at 7 AM."""
        forecast = _forecast(0.72, 0.5, 0.1, 0.04, 0.6, 0.6, now="2026-10-01T12:15")
        result, _ = self._run(self._tool(tmp_path), forecast=forecast)
        assert "Rain chance by hour: 12 PM 72%, 1 PM 50%, 2 PM 10%, 3 PM 4%," in (
            result.content
        )
        assert (
            "Rain likely: now until about 2 PM (peak 72%); from 4 PM on (peak 60%)"
            in result.content
        )

    def test_a_later_spell_names_its_start_and_end(self, tmp_path):
        forecast = _forecast(0.1, 0.45, 0.8, 0.2)
        result, _ = self._run(self._tool(tmp_path), forecast=forecast)
        assert "Rain likely: 1 PM to 3 PM (peak 80%)" in result.content

    def test_a_dry_day_says_no_rain_in_the_hours_shown(self, tmp_path):
        result, _ = self._run(self._tool(tmp_path), forecast=_forecast(0.1, 0.2))
        assert "Rain likely: not in the next 2 hours" in result.content

    def test_a_dead_provider_says_so_rather_than_guessing(self, tmp_path):
        tool = self._tool(tmp_path)
        with patch.object(connector, "fetch_forecast", side_effect=OSError("down")):
            result = tool.execute()
        assert result.success is False
        assert "weather service" in result.content

    def test_an_unknown_place_fails_plainly(self, tmp_path):
        with patch.object(connector, "search_places", return_value=[]):
            result, _ = self._run(self._tool(tmp_path), location="Nowhereville")
        assert result.success is False
        assert "Nowhereville" in result.content


class TestWhichPlaceItReportsFor:
    """A named place wins; otherwise the machine's own fix; else the config."""

    CONFIG = {"location": "Manila,PH"}

    def test_a_named_place_beats_the_machine_s_own_location(self):
        with patch("openjarvis.core.device_location.current_coordinates") as fix:
            place, coords = connector.resolve_place(self.CONFIG, "Tokyo")
        assert (place, coords) == ("Tokyo", None)
        # Asking for Tokyo must not cost a location fix, let alone use one.
        assert not fix.called

    def test_the_machine_s_location_is_used_when_no_place_is_named(self):
        with patch(
            "openjarvis.core.device_location.current_coordinates",
            return_value=(14.166, 121.139),
        ):
            place, coords = connector.resolve_place(self.CONFIG)
        assert coords == (14.166, 121.139)
        assert place == "Manila,PH"

    def test_the_configured_city_is_the_backstop(self):
        with patch(
            "openjarvis.core.device_location.current_coordinates",
            return_value=None,
        ):
            place, coords = connector.resolve_place(self.CONFIG)
        assert (place, coords) == ("Manila,PH", None)

    def test_device_location_can_be_turned_off(self):
        config = {**self.CONFIG, "use_device_location": False}
        with patch("openjarvis.core.device_location.current_coordinates") as fix:
            place, coords = connector.resolve_place(config)
        assert (place, coords) == ("Manila,PH", None)
        assert not fix.called

    def test_a_pinned_place_is_named_with_its_province(self):
        config = {"place": CALAMBA, "use_device_location": False}
        assert connector.resolve_place(config) == ("Calamba, Laguna", None)


class TestFailuresDoNotQuoteTheRequest:
    """httpx quotes the URL in its messages; a failure only needs the kind."""

    def _response(self, status):
        request = httpx.Request("GET", "https://api.open-meteo.com/v1/forecast")
        return httpx.Response(status, request=request)

    @pytest.mark.parametrize("status", [400, 429, 500])
    def test_status_failures_name_the_status(self, status):
        response = self._response(status)
        error = httpx.HTTPStatusError(
            "boom", request=response.request, response=response
        )
        with patch.object(connector, "_weather_api_get_raw", side_effect=error):
            with pytest.raises(connector.WeatherAPIError) as raised:
                connector._weather_api_get("https://example.invalid", {})
        assert str(status) in str(raised.value)

    def test_a_transport_failure_names_the_kind_not_the_url(self):
        with patch.object(
            connector,
            "_weather_api_get_raw",
            side_effect=httpx.ConnectTimeout("timed out"),
        ):
            with pytest.raises(connector.WeatherAPIError) as raised:
                connector._weather_api_get("https://example.invalid", {})
        assert "ConnectTimeout" in str(raised.value)
        assert "example.invalid" not in str(raised.value)


class TestASlowNetwork:
    """2026-10-01: about 1 in 3 connections to the forecast server stalled."""

    def test_a_stalled_request_is_tried_once_more(self):
        ok = httpx.Response(200, json={"ok": True}, request=httpx.Request("GET", "x"))
        with (
            patch.object(connector, "_STARTS", (0.0, 0.01)),
            patch.object(
                connector.httpx, "get", side_effect=[httpx.ReadTimeout("stall"), ok]
            ) as get,
        ):
            assert connector._weather_api_get_raw("https://x.invalid", {}) == {
                "ok": True
            }
        assert get.call_count == 2

    def test_every_attempt_stalling_gives_up_with_the_kind_of_failure(self):
        with (
            patch.object(connector, "_STARTS", (0.0, 0.01, 0.02)),
            patch.object(
                connector.httpx, "get", side_effect=httpx.ConnectTimeout("stall")
            ) as get,
        ):
            with pytest.raises(connector.WeatherAPIError, match="ConnectTimeout"):
                connector._weather_api_get("https://x.invalid", {})
        assert get.call_count == 3

    def test_the_same_forecast_is_not_fetched_twice_in_ten_minutes(self):
        connector._forecast_cache.clear()
        with patch.object(
            connector, "_weather_api_get", return_value=_forecast(0.1)
        ) as api:
            connector.fetch_forecast(14.1661, 121.1392, "metric", "kmh")
            # A fix a few metres away is the same place.
            connector.fetch_forecast(14.1659, 121.1394, "metric", "kmh")
            connector.fetch_forecast(14.1661, 121.1392, "imperial", "kmh")
        assert api.call_count == 2
        connector._forecast_cache.clear()

    def test_a_stalled_attempt_does_not_hold_up_the_answer(self):
        """The second attempt starts beside the stalled first and wins."""
        release = threading.Event()
        calls = []

        def get(url, params=None, timeout=None):
            calls.append(url)
            if len(calls) == 1:
                release.wait(5)
                return httpx.Response(
                    200, json={"who": "first"}, request=httpx.Request("GET", url)
                )
            return httpx.Response(
                200, json={"who": "second"}, request=httpx.Request("GET", url)
            )

        with (
            patch.object(connector, "_STARTS", (0.0, 0.1, 0.2)),
            patch.object(connector.httpx, "get", side_effect=get),
        ):
            started = time.monotonic()
            result = connector._weather_api_get_raw("https://x.invalid", {})
            elapsed = time.monotonic() - started
        release.set()
        assert result == {"who": "second"}
        assert elapsed < 2

    def test_an_answered_error_is_not_asked_again(self):
        bad = httpx.Response(400, request=httpx.Request("GET", "https://x.invalid"))
        with patch.object(connector.httpx, "get", return_value=bad) as get:
            with pytest.raises(connector.WeatherAPIError, match="400"):
                connector._weather_api_get("https://x.invalid", {})
        assert get.call_count == 1


class TestTheDayAskedAbout:
    """'Friday's weather' opens the panel on Friday (user report, 2026-10-02)."""

    WEEK = {"daily": [{"date": f"2026-10-0{d}"} for d in range(1, 8)]}  # Thu..Wed

    @pytest.mark.parametrize(
        "asked,index",
        [
            ("Friday", 1),
            ("fri", 1),
            ("Saturday", 2),
            ("Thursday", 0),
            ("tomorrow", 1),
            ("today", 0),
            ("2026-10-05", 4),
            ("someday", None),
            ("2026-12-25", None),
        ],
    )
    def test_matching(self, asked, index):
        from openjarvis.tools.weather import match_day

        assert match_day(self.WEEK, asked) == index

    def test_the_tool_marks_the_day_and_names_it(self, tmp_path):
        path = tmp_path / "weather.json"
        path.write_text(json.dumps({"place": CALAMBA, "use_device_location": False}))
        with patch.object(connector, "fetch_forecast", return_value=_forecast(0.1)):
            result = WeatherTool(token_path=str(path)).execute(day="Friday")
        assert result.metadata["weather"]["focus_day"] == 1
        assert "Asked about: 2026-10-02 (thunderstorm)" in result.content

    def test_a_general_question_has_no_day(self, tmp_path):
        path = tmp_path / "weather.json"
        path.write_text(json.dumps({"place": CALAMBA, "use_device_location": False}))
        with patch.object(connector, "fetch_forecast", return_value=_forecast(0.1)):
            result = WeatherTool(token_path=str(path)).execute()
        assert result.metadata["weather"]["focus_day"] is None
        assert "Asked about" not in result.content


class TestADaysOwnHoursAndAdvice:
    def _report(self):
        data = _forecast(0.1, 0.2, now="2026-10-01T12:00")
        # Friday's hours: dry morning, storm peaking at 5 PM.
        fri = [f"2026-10-02T{h:02d}:00" for h in range(24)]
        pops = [10] * 15 + [60, 80, 98, 70] + [30] * 5
        data["hourly"]["time"] += fri
        data["hourly"]["temperature_2m"] += [25 + (h > 9) * 5 for h in range(24)]
        data["hourly"]["precipitation_probability"] += pops
        data["hourly"]["weather_code"] += [95] * 24
        data["hourly"]["is_day"] += [1] * 24
        return connector.build_report(data, CALAMBA)

    def test_each_day_carries_its_own_24_hours(self):
        report = self._report()
        fri = report["daily"][1]["hours"]
        assert len(fri) == 24
        assert fri[0]["time"] == "2026-10-02T00:00"
        # The next-24-hours strip is unchanged: it starts at the current hour.
        assert report["hourly"][0]["time"] == "2026-10-01T12:00"

    def test_a_stormy_day_says_when(self):
        advice = self._report()["daily"][1]["advice"]
        assert advice.startswith("Thunderstorms likely, heaviest around 5 PM (98%)")
        assert "take an umbrella" in advice

    def test_a_dry_day_says_so(self):
        day = {
            "conditions": "clear sky",
            "low": 24,
            "high": 33,
            "rain_chance": 0.1,
            "uv_max": 9,
            "hours": [],
        }
        assert connector.day_advice(day) == (
            "Clear sky, no rain expected. UV is very high around midday. 24–33°C"
        )


def test_the_town_is_read_from_openstreetmap_once_per_area():
    from unittest.mock import MagicMock

    reply = MagicMock()
    reply.json.return_value = {
        "address": {"village": "Palola", "town": "Lucban", "province": "Quezon"}
    }
    connector._towns.clear()
    with patch.object(connector.httpx, "get", return_value=reply) as get:
        first = connector.town_at((14.11766, 121.54938))
        again = connector.town_at((14.11801, 121.54990))  # same ~1 km square
    assert first == again == {"name": "Lucban", "admin2": "Quezon"}
    assert get.call_count == 1
    assert "Sage" in get.call_args.kwargs["headers"]["User-Agent"]
    connector._towns.clear()


def test_a_failed_town_lookup_leaves_the_place_unnamed():
    connector._towns.clear()
    with patch.object(connector.httpx, "get", side_effect=OSError("offline")):
        assert connector.town_at((14.1, 121.5)) == {"name": ""}
    connector._towns.clear()
