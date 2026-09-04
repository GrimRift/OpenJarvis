"""Weather connector — current conditions and forecast via OpenWeatherMap API.

Uses an API key stored in the connector config dir.
All API calls are in module-level functions for easy mocking in tests.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

import httpx

from openjarvis.connectors._stubs import BaseConnector, Document, SyncStatus
from openjarvis.core.config import DEFAULT_CONFIG_DIR
from openjarvis.core.registry import ConnectorRegistry

_DEFAULT_TOKEN_PATH = str(DEFAULT_CONFIG_DIR / "connectors" / "weather.json")


class WeatherAPIError(RuntimeError):
    """A provider failure, described without quoting the request.

    httpx puts the full URL in its own message, and the API key travels in
    that URL as ``appid``. Raising it unaltered put the key into the tool
    result, and from there into the model's context, the chat transcript and
    the logs. A failure never needs to quote the credential that failed.
    """


def _weather_api_get(url: str, params: Dict[str, str]) -> Dict[str, Any]:
    """Call an OpenWeatherMap API endpoint."""
    try:
        return _weather_api_get_raw(url, params)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 401:
            raise WeatherAPIError(
                "OpenWeatherMap rejected the API key (401). A new key takes "
                "up to a couple of hours to activate -- if it was just "
                "created, try again shortly."
            ) from None
        if status == 404:
            raise WeatherAPIError(
                "OpenWeatherMap does not recognise that place (404)."
            ) from None
        if status == 429:
            raise WeatherAPIError(
                "OpenWeatherMap rate limit reached (429)."
            ) from None
        raise WeatherAPIError(f"OpenWeatherMap returned HTTP {status}.") from None
    except httpx.HTTPError as exc:
        raise WeatherAPIError(
            f"Could not reach OpenWeatherMap: {type(exc).__name__}"
        ) from None


def _weather_api_get_raw(url: str, params: Dict[str, str]) -> Dict[str, Any]:
    resp = httpx.get(url, params=params, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


#: Metric, because the machine this runs on is in the Philippines and the old
#: imperial default was a US assumption nobody chose. Configurable all the
#: same: the units belong to the reader, not to the provider.
DEFAULT_UNITS = "metric"

#: Above this chance of precipitation a forecast entry is worth mentioning in
#: a one-line briefing. Below it, saying "20% chance of rain" every morning
#: trains the reader to ignore the line.
RAIN_LIKELY = 0.4


def unit_labels(units: str) -> Dict[str, str]:
    if units == "imperial":
        return {"temp": "°F", "speed": "mph"}
    return {"temp": "°C", "speed": "m/s"}


def _place_params(
    location: str, coords: Optional[Tuple[float, float]]
) -> Dict[str, str]:
    """Coordinates when the machine knows where it is, the named city otherwise."""
    if coords is not None:
        return {"lat": str(coords[0]), "lon": str(coords[1])}
    return {"q": location}


def resolve_place(
    config: Dict[str, Any], explicit: Optional[str] = None
) -> Tuple[str, Optional[Tuple[float, float]]]:
    """Which place to report for, best source first.

    A place the user named wins outright -- asking for Tokyo must not be
    answered with wherever the laptop is sitting. Failing that, the Windows
    Location Service is asked, so a briefing follows the machine when it
    moves. The configured city is the backstop, and it is what a headless run
    falls back to whenever a fix is slow, refused or unavailable.
    """
    if explicit:
        return explicit, None

    configured = str(config.get("location") or "Manila,PH")
    if config.get("use_device_location") is False:
        return configured, None

    from openjarvis.core.device_location import current_coordinates

    return configured, current_coordinates()


def fetch_current(
    api_key: str,
    location: str,
    units: str,
    coords: Optional[Tuple[float, float]] = None,
) -> Dict[str, Any]:
    return _weather_api_get(
        "https://api.openweathermap.org/data/2.5/weather",
        params={
            **_place_params(location, coords),
            "appid": api_key,
            "units": units,
        },
    )


def fetch_forecast(
    api_key: str,
    location: str,
    units: str,
    count: int = 8,
    coords: Optional[Tuple[float, float]] = None,
) -> Dict[str, Any]:
    return _weather_api_get(
        "https://api.openweathermap.org/data/2.5/forecast",
        params={
            **_place_params(location, coords),
            "appid": api_key,
            "units": units,
            "cnt": str(count),
        },
    )


def _describe(payload: Dict[str, Any]) -> str:
    parts = [
        str(w.get("description") or "").strip()
        for w in payload.get("weather") or []
    ]
    return ", ".join(p for p in parts if p)


def _first_wet_entry(forecast: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The next forecast slot likely enough to be worth an umbrella."""
    for entry in forecast.get("list") or []:
        pop = entry.get("pop")
        if isinstance(pop, (int, float)) and pop >= RAIN_LIKELY:
            return entry
    return None


def _hour_label(entry: Dict[str, Any]) -> str:
    stamp = str(entry.get("dt_txt") or "")
    try:
        return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").strftime(
            "%-I %p" if os.name != "nt" else "%#I %p"
        )
    except ValueError:
        return stamp or "later"


def summarize(
    current: Dict[str, Any],
    forecast: Optional[Dict[str, Any]] = None,
    units: str = DEFAULT_UNITS,
) -> str:
    """One decision-shaped line: what it is now, and whether to expect rain.

    Written for a spoken briefing that already carries mail, calendar and
    Teams, so it answers the only question a morning weather line is asked --
    do I need an umbrella -- rather than reciting a forecast table.
    """
    labels = unit_labels(units)
    temp = (current.get("main") or {}).get("temp")
    shown = f"{round(temp)}{labels['temp']}" if isinstance(temp, (int, float)) else "?"
    conditions = _describe(current) or "conditions unknown"
    line = f"{shown}, {conditions}"

    if not forecast:
        return line
    wet = _first_wet_entry(forecast)
    if wet is None:
        return f"{line} — no rain expected"
    chance = round(float(wet.get("pop") or 0) * 100)
    return f"{line} — rain likely around {_hour_label(wet)} ({chance}%)"


@ConnectorRegistry.register("weather")
class WeatherConnector(BaseConnector):
    """Fetch current weather and short-term forecast from OpenWeatherMap."""

    connector_id = "weather"
    display_name = "Weather"
    auth_type = "token"

    def __init__(self, *, token_path: str = _DEFAULT_TOKEN_PATH) -> None:
        self._token_path = Path(token_path)
        self._status = SyncStatus()

    def _load_config(self) -> Dict[str, str]:
        """Load API key and location from disk."""
        data = json.loads(self._token_path.read_text(encoding="utf-8"))
        return data

    def is_connected(self) -> bool:
        if not self._token_path.exists():
            return False
        try:
            data = json.loads(self._token_path.read_text(encoding="utf-8"))
            return bool(data.get("api_key"))
        except (json.JSONDecodeError, OSError):
            return False

    def disconnect(self) -> None:
        if self._token_path.exists():
            self._token_path.unlink()

    def sync(
        self, *, since: Optional[datetime] = None, cursor: Optional[str] = None
    ) -> Iterator[Document]:
        """Yield one document: what it is doing now, and whether rain is coming.

        A single document rather than the previous current-plus-forecast pair.
        The briefing wants one line, and the forecast document existed only to
        be truncated into it.
        """
        config = self._load_config()
        api_key = config["api_key"]
        units = config.get("units") or DEFAULT_UNITS
        labels = unit_labels(units)
        location, coords = resolve_place(config)

        current = fetch_current(api_key, location, units, coords)
        try:
            forecast = fetch_forecast(api_key, location, units, coords=coords)
        except Exception:
            # A missing forecast costs the rain clause, not the whole line.
            forecast = None

        # The provider names the place it actually answered for, which is the
        # only honest label when the coordinates came from the machine rather
        # than from the configured city.
        location = str(current.get("name") or location)

        main = current.get("main") or {}
        wind = current.get("wind") or {}
        summary = summarize(current, forecast, units)

        yield Document(
            doc_id=f"weather-current-{location}",
            source="weather",
            doc_type="current",
            content=summary,
            title=f"Weather — {location}",
            timestamp=datetime.now(),
            metadata={
                "location": location,
                "units": units,
                "summary": summary,
                "temp": main.get("temp"),
                "feels_like": main.get("feels_like"),
                "conditions": _describe(current),
                "humidity": main.get("humidity"),
                "wind_speed": wind.get("speed"),
                "temp_unit": labels["temp"],
                "speed_unit": labels["speed"],
            },
        )

        self._status.state = "idle"
        self._status.last_sync = datetime.now()

    def sync_status(self) -> SyncStatus:
        return self._status
