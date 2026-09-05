"""Offline contracts; no test reads the user's config, location or databases."""

import json
import subprocess
import sys
from urllib.parse import parse_qs, urlparse

import httpx
import pytest


@pytest.fixture
def nav(tmp_path, monkeypatch):
    from openjarvis.core.config import NavigationConfig
    from openjarvis.tools import navigate

    monkeypatch.setenv("OPENJARVIS_HOME", str(tmp_path))
    monkeypatch.delenv("OPENJARVIS_CONFIG", raising=False)
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    monkeypatch.setattr(
        navigate, "load_config", lambda: pytest.fail("Ambient config read")
    )
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: pytest.fail("Unexpected network call")
    )
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: pytest.fail("Unexpected network call")
    )
    tool = navigate.NavigateTool(config=NavigationConfig(), data_dir=tmp_path)
    return navigate, tool


ORIGIN = {"latitude": 14.0, "longitude": 121.0}
DEST = {"latitude": 14.2, "longitude": 121.2}


def test_builtin_discovery_in_fresh_process(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENJARVIS_HOME", str(tmp_path))
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import openjarvis.tools; "
            "from openjarvis.core.registry import ToolRegistry; "
            "assert ToolRegistry.get('navigate').tool_id == 'navigate'",
        ],
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr


def test_unsaved_destination_offline_is_search_not_navigation(nav):
    _, tool = nav
    result = tool.execute(destination="SM City Calamba & cafe")
    params = parse_qs(urlparse(result.metadata["maps_url"]).query)
    assert params["q"] == ["SM City Calamba & cafe"]
    assert "navigate" not in params
    assert result.metadata["status"] == "needs_selection"
    assert result.metadata["route"] is None
    assert not result.metadata["navigation_started"]


def test_saved_alias_casefolds_and_never_writes(nav, tmp_path):
    _, tool = nav
    path = tmp_path / "saved_places.json"
    text = json.dumps({" Home ": DEST, "school": ORIGIN})
    path.write_text(text)
    result = tool.execute(destination=" HOME ", origin=ORIGIN)
    assert result.metadata["destination_coordinates"] == DEST
    assert "navigate=yes" in result.metadata["maps_url"]
    assert "disabled" in result.content
    assert path.read_text() == text


def test_missing_origin_never_uses_pc_location(nav):
    _, tool = nav
    result = tool.execute(destination="a place", destination_coordinates=DEST)
    assert "provide your current location" in result.content
    assert result.metadata["route"] is None


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"latitude": True, "longitude": 0},
        {"latitude": 91, "longitude": 0},
        {"latitude": 0, "longitude": float("nan")},
        {"latitude": 0, "longitude": 181},
        {"latitude": "14", "longitude": 120},
    ],
)
def test_invalid_coordinates_fail_before_network(nav, value):
    _, tool = nav
    assert not tool.execute(destination="there", origin=value).success


def test_zero_coordinates_are_real_not_absent(nav):
    _, tool = nav
    point = {"latitude": 0, "longitude": 0}
    result = tool.execute(destination="there", destination_coordinates=point)
    assert result.metadata["destination_coordinates"] == point


@pytest.mark.parametrize(
    "text", ["{", "[]", '{"home": {}}', json.dumps({"home": DEST, "HOME": DEST})]
)
def test_corrupt_saved_places_not_silently_treated_as_empty(nav, tmp_path, text):
    _, tool = nav
    (tmp_path / "saved_places.json").write_text(text)
    assert not tool.execute(destination="home").success


@pytest.mark.parametrize("destination", [None, "", "  ", 4, "a" * 301])
def test_destination_validation(nav, destination):
    _, tool = nav
    assert not tool.execute(destination=destination).success


def enable(tool, monkeypatch):
    tool._config.routes_enabled = True
    tool._config.routes_quota_confirmed = True
    tool._config.places_enabled = True
    tool._config.places_quota_confirmed = True
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-only-value")


def test_flags_and_quota_are_strict_even_with_ambient_key(nav, monkeypatch):
    _, tool = nav
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-only-value")
    for enabled, quota in [
        (False, True),
        (True, False),
        ("true", True),
        (True, "true"),
    ]:
        tool._config.routes_enabled = enabled
        tool._config.routes_quota_confirmed = quota
        result = tool.execute(
            destination="there", destination_coordinates=DEST, origin=ORIGIN
        )
        assert "disabled" in result.content
        assert result.metadata["route"] is None


def test_traffic_request_and_briefing_use_same_destination_as_waze(nav, monkeypatch):
    module, tool = nav
    enable(tool, monkeypatch)
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(
            200,
            json={
                "routes": [
                    {
                        "duration": "901s",
                        "staticDuration": "600s",
                        "distanceMeters": 12000,
                    }
                ]
            },
        )

    monkeypatch.setattr(httpx, "post", post)
    result = tool.execute(
        destination="there", destination_coordinates=DEST, origin=ORIGIN
    )
    # Spoken in a car: the drive time and how much of it is traffic, and
    # nothing else. The provider's name and the caveat that Waze may route
    # differently were dropped -- neither is something a driver can act on.
    assert "16 minutes, 6 in traffic" in result.content
    assert "Google" not in result.content
    assert "may choose a different route" not in result.content
    assert len(calls) == 1
    url, options = calls[0]
    assert url == module.ROUTES_URL
    assert options["json"]["destination"]["location"]["latLng"] == DEST
    assert options["json"]["origin"]["location"]["latLng"] == ORIGIN
    assert options["json"]["routingPreference"] == "TRAFFIC_AWARE"
    assert options["headers"]["X-Goog-FieldMask"] == module.ROUTE_FIELDS
    assert options["follow_redirects"] is False
    assert "key" not in url
    assert "test-only-value" not in result.content


@pytest.mark.parametrize("route", [{}, {"duration": "bad"}, {"duration": None}])
def test_missing_eta_does_not_become_zero(nav, monkeypatch, route):
    _, tool = nav
    enable(tool, monkeypatch)
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: httpx.Response(200, json={"routes": [route]})
    )
    result = tool.execute(
        destination="there", destination_coordinates=DEST, origin=ORIGIN
    )
    assert result.metadata["route"] is None
    assert "no usable ETA" in result.content
    assert "0 minutes" not in result.content


def test_missing_static_duration_and_distance_stay_unknown(nav, monkeypatch):
    _, tool = nav
    enable(tool, monkeypatch)
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(
            200,
            json={
                "routes": [{"duration": "0s"}],
            },
        ),
    )
    result = tool.execute(
        destination="there", destination_coordinates=DEST, origin=ORIGIN
    )
    assert result.metadata["route"]["duration_seconds"] == 0
    assert result.metadata["route"]["traffic_delay_seconds"] is None
    assert result.metadata["route"]["distance_meters"] is None
    # The briefing is spoken in a car, so an unknown delay is simply not
    # mentioned rather than announced. What matters is that none is invented.
    assert "traffic" not in result.content.lower()
    assert "0 minutes" in result.content


@pytest.mark.parametrize("status", [301, 400, 403, 429, 500])
def test_provider_errors_never_echo_body_or_retry(nav, monkeypatch, status):
    _, tool = nav
    enable(tool, monkeypatch)
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        return httpx.Response(status, text="test-only-value")

    monkeypatch.setattr(httpx, "post", post)
    result = tool.execute(
        destination="there", destination_coordinates=DEST, origin=ORIGIN
    )
    assert f"HTTP {status}" in result.content
    assert "test-only-value" not in result.content
    assert len(calls) == 1
    assert result.metadata["route"] is None


def test_timeout_does_not_leak_exception_text(nav, monkeypatch):
    _, tool = nav
    enable(tool, monkeypatch)

    def post(*args, **kwargs):
        raise httpx.ReadTimeout("test-only-value")

    monkeypatch.setattr(httpx, "post", post)
    result = tool.execute(
        destination="there", destination_coordinates=DEST, origin=ORIGIN
    )
    assert "test-only-value" not in result.content
    assert "unavailable" in result.content


def routes():
    return {
        "routes": [
            {"duration": "901s", "staticDuration": "600s", "distanceMeters": 12000}
        ]
    }


def places():
    return {
        "places": [
            {
                "id": str(i),
                "displayName": {"text": f"Mall {i}"},
                "formattedAddress": f"City {i}",
                "location": point,
            }
            for i, point in enumerate([DEST, ORIGIN])
        ]
    }


def test_an_ambiguous_search_takes_the_top_result(nav, monkeypatch):
    """Requiring exactly one match meant almost every real place asked.

    "SM City Calamba" returns the mall, a diner inside it and the
    supermarket, so the tool returned needs_selection -- no briefing, no
    audio, and a Waze search rather than a route. A driver cannot pick from
    a list, so Google's top-ranked result is taken and its name is spoken
    back, which is the moment a wrong pick can be caught.

    This costs a Routes call on an ambiguous query, which the previous
    behaviour avoided. That is the accepted trade, not an oversight.
    """
    module, tool = nav
    enable(tool, monkeypatch)
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if url == module.PLACES_URL:
            return httpx.Response(200, json=places())
        return httpx.Response(200, json=routes())

    monkeypatch.setattr(httpx, "post", post)
    result = tool.execute(destination="mall", origin=ORIGIN)

    assert result.metadata["status"] == "ready"
    assert module.ROUTES_URL in calls
    # The first candidate, and the briefing names it rather than "mall".
    assert result.metadata["destination"] == "Mall 0"
    assert "Mall 0" in result.metadata["briefing"]


def test_chosen_place_id_must_match_returned_candidates(nav, monkeypatch):
    module, tool = nav
    enable(tool, monkeypatch)

    def post(url, **kwargs):
        if url == module.PLACES_URL:
            return httpx.Response(200, json=places())
        assert kwargs["json"]["destination"]["location"]["latLng"] == ORIGIN
        return httpx.Response(200, json={"routes": [{"duration": "60s"}]})

    monkeypatch.setattr(httpx, "post", post)
    rejected = tool.execute(destination="mall", place_id="invented", origin=ORIGIN)
    assert rejected.metadata["status"] == "needs_selection"
    result = tool.execute(destination="mall", place_id="1", origin=ORIGIN)
    assert result.metadata["destination"] == "Mall 1"
    assert result.metadata["destination_coordinates"] == ORIGIN


def test_weather_uses_destination_and_existing_summarizer(nav, monkeypatch, tmp_path):
    module, tool = nav
    tool._config.weather_enabled = True
    (tmp_path / "connectors").mkdir()
    (tmp_path / "connectors" / "weather.json").write_text(
        '{"api_key":"test-only-value"}'
    )

    def current(key, location, units, coords):
        assert coords == (DEST["latitude"], DEST["longitude"])
        assert units == "metric"
        return {"main": {"temp": 25}, "weather": [{"description": "rain"}]}

    monkeypatch.setattr(module.weather, "fetch_current", current)
    monkeypatch.setattr(module.weather, "fetch_forecast", lambda *a, **k: None)
    result = tool.execute(destination="there", destination_coordinates=DEST)
    assert result.metadata["weather"] == "25°C, rain"
    assert "test-only-value" not in result.content


def test_config_loads_navigation_section(tmp_path, monkeypatch):
    from openjarvis.core import config

    monkeypatch.setenv("OPENJARVIS_HOME", str(tmp_path))
    monkeypatch.setattr(config, "detect_hardware", lambda: config.HardwareInfo())
    path = tmp_path / "config.toml"
    path.write_text("[navigation]\nroutes_enabled=true\nroutes_quota_confirmed=true\n")
    loaded = config.load_config(path).navigation
    assert loaded.routes_enabled is True
    assert loaded.routes_quota_confirmed is True
    assert loaded.places_enabled is False
    assert loaded.weather_enabled is False


def test_server_build_and_executor_use_the_registered_tool(nav, monkeypatch):
    from openjarvis.cli.serve import _build_tool, _resolve_allowed_tools
    from openjarvis.core.config import JarvisConfig
    from openjarvis.core.types import ToolCall
    from openjarvis.tools._stubs import ToolExecutor

    module, tool = nav
    config = JarvisConfig()
    config.agent.tools = "navigate"
    monkeypatch.setattr(module, "load_config", lambda: config)
    allowed, explicit = _resolve_allowed_tools(config)
    assert explicit and "navigate" in allowed
    built = _build_tool(module.NavigateTool)
    result = ToolExecutor([built]).execute(
        ToolCall(
            id="drive",
            name="navigate",
            arguments=json.dumps(
                {
                    "destination": "test destination",
                    "destination_coordinates": DEST,
                    "origin": ORIGIN,
                }
            ),
        )
    )
    assert result.success
    assert "navigate=yes" in result.metadata["maps_url"]
    assert "disabled" in result.content
    assert result.metadata["navigation_started"] is False


def test_disabled_new_places_is_named_without_provider_body(nav, monkeypatch):
    _, tool = nav
    enable(tool, monkeypatch)
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(
            403,
            json={
                "error": {
                    "message": "private-value",
                    "details": [{"reason": "SERVICE_DISABLED"}],
                },
            },
        ),
    )
    result = tool.execute(destination="mall", origin=ORIGIN)
    assert "Places API (New) is disabled" in result.content
    assert "private-value" not in result.content


class TestTheAppLinkActuallyNavigates:
    """Waze opened on the map with no route on the first real drive.

    The https form carries navigate=yes but iOS can route it through Safari
    and hand off to the app on a second hop, losing the intent. The app
    scheme goes straight there. Chat keeps https: it is tappable and works
    for someone without Waze installed.
    """

    POINT = {"latitude": 14.166002, "longitude": 121.13933}

    def test_the_app_link_uses_the_waze_scheme(self):
        from openjarvis.tools import navigate

        link = navigate.waze_link("home", self.POINT, app=True)
        assert link.startswith("waze://")
        assert "navigate=yes" in link

    def test_the_web_link_is_unchanged(self):
        from openjarvis.tools import navigate

        link = navigate.waze_link("home", self.POINT)
        assert link.startswith("https://waze.com/ul?")
        assert "navigate=yes" in link

    def test_both_carry_the_same_coordinates(self):
        from openjarvis.tools import navigate

        web = navigate.waze_link("home", self.POINT)
        app = navigate.waze_link("home", self.POINT, app=True)
        assert "14.166002" in web and "14.166002" in app
        assert "121.13933" in web and "121.13933" in app

    def test_a_search_link_has_no_navigate_intent(self):
        """Nothing to navigate to until a place is chosen."""
        from openjarvis.tools import navigate

        link = navigate.waze_link("SM Calamba", app=True)
        assert link.startswith("waze://")
        assert "navigate=yes" not in link
