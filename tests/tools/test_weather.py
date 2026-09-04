"""Tests for the weather tool and the one-line summary the briefing uses.

The line exists to answer one question -- do I need an umbrella -- so the
tests are about that, not about reciting a forecast table.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from openjarvis.connectors import weather as connector
from openjarvis.tools import weather as tool_module
from openjarvis.tools.weather import WeatherTool

CURRENT = {
    "main": {"temp": 28.4, "feels_like": 32.1, "humidity": 79},
    "weather": [{"description": "broken clouds"}],
    "wind": {"speed": 3.2},
}


def _forecast(*pops: float):
    return {
        "list": [
            {
                "dt_txt": f"2026-09-04 {12 + i * 3:02d}:00:00",
                "main": {"temp": 29},
                "pop": pop,
            }
            for i, pop in enumerate(pops)
        ]
    }


class TestTheBriefingLine:
    def test_it_names_the_hour_rain_becomes_likely(self):
        line = connector.summarize(CURRENT, _forecast(0.1, 0.62))
        assert "28°C" in line
        assert "broken clouds" in line
        assert "3 PM" in line and "62%" in line

    def test_a_dry_day_says_so_rather_than_going_quiet(self):
        """Silence would read as "the forecast failed"."""
        assert "no rain expected" in connector.summarize(CURRENT, _forecast(0.05, 0.1))

    def test_a_drizzle_is_not_worth_an_umbrella(self):
        """Reporting 20% every morning teaches the reader to ignore the line."""
        assert "no rain expected" in connector.summarize(CURRENT, _forecast(0.35))

    def test_losing_the_forecast_costs_the_rain_clause_not_the_line(self):
        line = connector.summarize(CURRENT, None)
        assert "28°C, broken clouds" == line

    def test_units_are_the_reader_s_not_the_provider_s(self):
        assert "°F" in connector.summarize(CURRENT, None, units="imperial")
        assert "°C" in connector.summarize(CURRENT, None, units="metric")


class TestTheTool:
    def _tool(self, tmp_path, **config):
        path = tmp_path / "weather.json"
        # Device location off by default here: these tests are about the tool,
        # and a real Windows fix would make them depend on where the machine is.
        payload = {"api_key": "k", "use_device_location": False, **config}
        path.write_text(json.dumps(payload), encoding="utf-8")
        return WeatherTool(token_path=str(path))

    def test_it_does_not_name_the_place_you_are_standing_in(self, tmp_path):
        """"Prinza: 25C" tells the user nothing they did not already know.

        The barangay the coordinates land in is more precise than the city
        and less useful than saying nothing, so a fix from the machine is
        reported bare.
        """
        path = tmp_path / "weather.json"
        path.write_text(
            json.dumps({"api_key": "k", "location": "Calamba,PH"}),
            encoding="utf-8",
        )
        tool = WeatherTool(token_path=str(path))
        located = dict(CURRENT, name="Prinza")
        with (
            patch(
                "openjarvis.core.device_location.current_coordinates",
                return_value=(14.166, 121.139),
            ),
            patch.object(tool_module, "fetch_current", return_value=located),
            patch.object(tool_module, "fetch_forecast", return_value=_forecast(0.1)),
        ):
            result = tool.execute()
        assert "Prinza" not in result.content
        assert result.content.startswith("28")
        # Still recorded, because the UI and the model may want to know.
        assert result.metadata["location"] == "Prinza"

    def test_a_named_place_is_echoed_back(self, tmp_path):
        """An answer about Tokyo must not be mistaken for one about here."""
        tool = self._tool(tmp_path, location="Calamba,PH")
        with (
            patch.object(
                tool_module, "fetch_current", return_value=dict(CURRENT, name="Tokyo")
            ),
            patch.object(tool_module, "fetch_forecast", return_value=_forecast(0.1)),
        ):
            result = tool.execute(location="Tokyo")
        assert result.content.startswith("Tokyo:")

    def test_the_configured_city_is_named_because_that_signals_no_fix(self, tmp_path):
        tool = self._tool(tmp_path, location="Calamba,PH")
        with (
            patch.object(
                tool_module,
                "fetch_current",
                return_value=dict(CURRENT, name="Calamba"),
            ),
            patch.object(tool_module, "fetch_forecast", return_value=_forecast(0.1)),
        ):
            result = tool.execute()
        assert result.content.startswith("Calamba:")

    def test_it_reports_the_configured_location(self, tmp_path):
        tool = self._tool(tmp_path, location="Cebu City,PH")
        with (
            patch.object(tool_module, "fetch_current", return_value=CURRENT) as cur,
            patch.object(tool_module, "fetch_forecast", return_value=_forecast(0.7)),
        ):
            result = tool.execute()
        assert result.success is True
        assert "Cebu City,PH" in result.content
        assert cur.call_args.args[1] == "Cebu City,PH"

    def test_an_explicit_place_overrides_the_configured_one(self, tmp_path):
        tool = self._tool(tmp_path, location="Cebu City,PH")
        with (
            patch.object(tool_module, "fetch_current", return_value=CURRENT) as cur,
            patch.object(tool_module, "fetch_forecast", return_value=_forecast(0.1)),
        ):
            tool.execute(location="Tokyo")
        assert cur.call_args.args[1] == "Tokyo"

    def test_a_dead_forecast_still_answers(self, tmp_path):
        tool = self._tool(tmp_path)
        with (
            patch.object(tool_module, "fetch_current", return_value=CURRENT),
            patch.object(tool_module, "fetch_forecast", side_effect=OSError("down")),
        ):
            result = tool.execute()
        assert result.success is True
        assert "28" in result.content

    def test_a_dead_provider_says_so_rather_than_guessing(self, tmp_path):
        tool = self._tool(tmp_path)
        with patch.object(tool_module, "fetch_current", side_effect=OSError("down")):
            result = tool.execute()
        assert result.success is False
        assert "weather service" in result.content

    def test_missing_configuration_names_the_file_to_fix(self, tmp_path):
        result = WeatherTool(token_path=str(tmp_path / "absent.json")).execute()
        assert result.success is False
        assert "absent.json" in result.content


@pytest.mark.parametrize("units,expected", [("metric", "m/s"), ("imperial", "mph")])
def test_wind_units_follow_the_temperature_units(units, expected):
    assert connector.unit_labels(units)["speed"] == expected


class TestWhichPlaceItReportsFor:
    """A named place wins; otherwise the machine's own fix; else the config."""

    CONFIG = {"location": "Manila,PH"}

    def test_a_named_place_beats_the_machine_s_own_location(self):
        with patch(
            "openjarvis.core.device_location.current_coordinates"
        ) as fix:
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

    def test_coordinates_are_sent_instead_of_a_city_name(self):
        params = connector._place_params("Manila,PH", (14.166, 121.139))
        assert params == {"lat": "14.166", "lon": "121.139"}
        assert connector._place_params("Manila,PH", None) == {"q": "Manila,PH"}


class TestTheKeyNeverAppearsInAFailure:
    """The API key travels in the URL as `appid`, and httpx quotes the URL.

    Raising that unaltered put the key into the tool result, and from there
    into the model's context, the chat transcript and the logs. Observed for
    real on the first live call.
    """

    def _response(self, status):
        request = httpx.Request(
            "GET",
            "https://api.openweathermap.org/data/2.5/weather",
            params={"lat": "14.1", "lon": "121.1", "appid": "SECRETKEY123"},
        )
        return httpx.Response(status, request=request)

    @pytest.mark.parametrize("status", [401, 404, 429, 500])
    def test_no_status_quotes_the_credential(self, status):
        response = self._response(status)
        error = httpx.HTTPStatusError(
            "boom", request=response.request, response=response
        )
        with patch.object(connector, "_weather_api_get_raw", side_effect=error):
            with pytest.raises(connector.WeatherAPIError) as raised:
                connector._weather_api_get("https://example.invalid", {})
        assert "SECRETKEY123" not in str(raised.value)
        assert str(status) in str(raised.value)

    def test_a_rejected_key_explains_the_activation_delay(self):
        response = self._response(401)
        error = httpx.HTTPStatusError(
            "boom", request=response.request, response=response
        )
        with patch.object(connector, "_weather_api_get_raw", side_effect=error):
            with pytest.raises(connector.WeatherAPIError) as raised:
                connector._weather_api_get("https://example.invalid", {})
        assert "activate" in str(raised.value)

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
