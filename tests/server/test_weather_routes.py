"""Settings > Weather routes (M41)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.connectors import weather
from openjarvis.server.weather_routes import create_weather_router

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


@pytest.fixture()
def client(tmp_path):
    app = FastAPI()
    app.include_router(create_weather_router(settings_path=tmp_path / "weather.json"))
    return TestClient(app)


def test_an_old_file_shows_its_city_and_keeps_its_other_keys(client, tmp_path):
    path = tmp_path / "weather.json"
    path.write_text(json.dumps({"api_key": "old", "location": "Calamba,PH"}))
    got = client.get("/v1/weather/settings").json()
    assert got["place"] is None and got["legacy_location"] == "Calamba,PH"
    assert got["use_device_location"] is True and got["wind_unit"] == "kmh"
    assert "api_key" not in got

    saved = client.put("/v1/weather/settings", json={"place": CALAMBA}).json()
    assert saved["place_label"] == "Calamba, Laguna"
    assert saved["legacy_location"] == ""
    on_disk = json.loads(path.read_text())
    # Keys this page does not own are left alone; the city name is retired.
    assert on_disk["api_key"] == "old" and "location" not in on_disk
    assert on_disk["place"]["latitude"] == pytest.approx(14.21167)


def test_switches_and_units_round_trip(client):
    saved = client.put(
        "/v1/weather/settings",
        json={"use_device_location": False, "units": "imperial", "wind_unit": "mph"},
    ).json()
    assert saved["use_device_location"] is False
    assert (saved["units"], saved["wind_unit"]) == ("imperial", "mph")


@pytest.mark.parametrize(
    "body",
    [
        {"units": "kelvin"},
        {"wind_unit": "knots"},
        {"place": {"name": "X"}},
        {"place": {"name": "X", "latitude": 99, "longitude": 0}},
        {"place": {"name": "", "latitude": 1, "longitude": 1}},
    ],
)
def test_bad_values_are_refused_and_nothing_is_written(client, tmp_path, body):
    assert client.put("/v1/weather/settings", json=body).status_code == 400
    assert not (tmp_path / "weather.json").exists()


def test_place_search_labels_each_hit_with_its_province(client):
    with patch.object(weather, "search_places", return_value=[CALAMBA]) as search:
        got = client.get("/v1/weather/places", params={"q": "Calamba,PH"}).json()
    assert search.call_args.args[0] == "Calamba,PH"
    assert got["results"][0]["label"] == "Calamba, Laguna"
    assert client.get("/v1/weather/places").json() == {"results": []}
