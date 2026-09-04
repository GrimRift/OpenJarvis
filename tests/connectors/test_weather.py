"""Tests for WeatherConnector — OpenWeatherMap API."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from openjarvis.connectors._stubs import Document
from openjarvis.core.registry import ConnectorRegistry


def test_weather_registered():
    """WeatherConnector is discoverable via ConnectorRegistry."""
    from openjarvis.connectors.weather import WeatherConnector

    ConnectorRegistry.register_value("weather", WeatherConnector)
    assert ConnectorRegistry.contains("weather")
    cls = ConnectorRegistry.get("weather")
    assert cls.connector_id == "weather"
    assert cls.display_name == "Weather"
    assert cls.auth_type == "token"


_CURRENT_RESPONSE = {
    "main": {"temp": 62.5, "humidity": 55},
    "weather": [{"description": "clear sky"}],
    "wind": {"speed": 8.2},
}

_FORECAST_RESPONSE = {
    "list": [
        {
            "dt_txt": "2026-04-02 12:00:00",
            "main": {"temp": 64.0},
            "weather": [{"description": "few clouds"}],
        },
        {
            "dt_txt": "2026-04-02 15:00:00",
            "main": {"temp": 66.0},
            "weather": [{"description": "scattered clouds"}],
        },
    ],
}


@pytest.fixture()
def connector(tmp_path):
    """WeatherConnector with fake config file."""
    from openjarvis.connectors.weather import WeatherConnector

    config_path = tmp_path / "weather.json"
    config_path.write_text(
        '{"api_key": "fake-key", "location": "San Francisco,CA"}',
        encoding="utf-8",
    )
    return WeatherConnector(token_path=str(config_path))


def test_is_connected(connector):
    assert connector.is_connected() is True


def test_is_connected_no_file(tmp_path):
    from openjarvis.connectors.weather import WeatherConnector

    c = WeatherConnector(token_path=str(tmp_path / "missing.json"))
    assert c.is_connected() is False


def test_sync_yields_one_decision_shaped_document(connector):
    """One document, not the old current-plus-forecast pair.

    The forecast document existed only to be truncated into a briefing line,
    and the briefing wants a single line: what it is doing now, and whether
    rain is coming. The forecast is still fetched -- it is what supplies the
    rain clause -- it is just no longer a document of its own.
    """
    with patch(
        "openjarvis.connectors.weather._weather_api_get",
        side_effect=[_CURRENT_RESPONSE, _FORECAST_RESPONSE],
    ):
        docs = list(connector.sync())

    assert len(docs) == 1
    doc = docs[0]
    assert isinstance(doc, Document)
    assert doc.source == "weather"
    assert doc.doc_type == "current"
    assert "clear sky" in doc.content
    # The summary is what the briefing formatter reads; it used to look for
    # fields that never existed and rendered "?" for every one of them.
    assert doc.metadata["summary"] == doc.content
    assert doc.metadata["humidity"] == 55


def test_the_rain_clause_comes_from_the_forecast(connector):
    wet = {
        "list": [
            {"dt_txt": "2026-04-02 12:00:00", "main": {"temp": 64.0}, "pop": 0.1},
            {"dt_txt": "2026-04-02 15:00:00", "main": {"temp": 66.0}, "pop": 0.8},
        ]
    }
    with patch(
        "openjarvis.connectors.weather._weather_api_get",
        side_effect=[_CURRENT_RESPONSE, wet],
    ):
        doc = next(iter(connector.sync()))
    assert "rain likely around 3 PM" in doc.content


def test_a_dead_forecast_still_yields_the_temperature(connector):
    """Losing the forecast costs the rain clause, not the whole line."""
    with patch(
        "openjarvis.connectors.weather._weather_api_get",
        side_effect=[_CURRENT_RESPONSE, OSError("forecast down")],
    ):
        doc = next(iter(connector.sync()))
    assert "clear sky" in doc.content


def test_disconnect(connector):
    connector.disconnect()
    assert connector.is_connected() is False
