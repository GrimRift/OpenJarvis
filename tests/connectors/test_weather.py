"""Tests for WeatherConnector -- Open-Meteo, read by the morning briefing."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from openjarvis.connectors._stubs import Document
from openjarvis.core.registry import ConnectorRegistry
from tests.tools.test_weather import CALAMBA, _forecast


def test_weather_registered():
    """WeatherConnector is discoverable via ConnectorRegistry."""
    from openjarvis.connectors.weather import WeatherConnector

    ConnectorRegistry.register_value("weather", WeatherConnector)
    assert ConnectorRegistry.contains("weather")
    cls = ConnectorRegistry.get("weather")
    assert cls.connector_id == "weather"
    assert cls.display_name == "Weather"


@pytest.fixture()
def connector(tmp_path):
    from openjarvis.connectors.weather import WeatherConnector

    config_path = tmp_path / "weather.json"
    config_path.write_text(
        json.dumps({"place": CALAMBA, "use_device_location": False}),
        encoding="utf-8",
    )
    return WeatherConnector(token_path=str(config_path))


def test_is_connected_without_a_key(connector):
    """Open-Meteo has no key: a settings file is what 'set up' means."""
    assert connector.is_connected() is True


def test_is_connected_no_file(tmp_path):
    from openjarvis.connectors.weather import WeatherConnector

    c = WeatherConnector(token_path=str(tmp_path / "missing.json"))
    assert c.is_connected() is False


def test_sync_yields_one_decision_shaped_document(connector):
    with patch(
        "openjarvis.connectors.weather.fetch_forecast",
        return_value=_forecast(0.1, 0.8),
    ):
        docs = list(connector.sync())

    assert len(docs) == 1
    doc = docs[0]
    assert isinstance(doc, Document)
    assert doc.source == "weather"
    assert doc.doc_type == "current"
    assert "overcast" in doc.content
    assert "rain likely around 1 PM" in doc.content
    # The summary is what the briefing formatter reads.
    assert doc.metadata["summary"] == doc.content
    assert doc.metadata["humidity"] == 79
    assert doc.title == "Weather — Calamba, Laguna"


def test_disconnect(connector):
    connector.disconnect()
    assert connector.is_connected() is False
