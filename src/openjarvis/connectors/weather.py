"""Weather connector -- current conditions and a 7-day forecast via Open-Meteo.

Open-Meteo (M41, 2026-10-01) replaced OpenWeatherMap: it needs no key, and
one call returns the current conditions, the next 24 hours and seven days,
which is what the weather panel draws. The chat tool, the morning briefing and
the car briefing all read through here, so they cannot disagree.

Settings live in ``connectors/weather.json``: the pinned home ``place``
(name + coordinates, picked in Settings > Weather), ``use_device_location``,
``units`` and ``wind_unit``. An old file holding only ``location`` (a city
name) still works: it is geocoded on use.
All API calls are in module-level functions for easy mocking in tests.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import httpx

from openjarvis.connectors._stubs import BaseConnector, Document, SyncStatus
from openjarvis.core.config import DEFAULT_CONFIG_DIR
from openjarvis.core.registry import ConnectorRegistry

_DEFAULT_TOKEN_PATH = str(DEFAULT_CONFIG_DIR / "connectors" / "weather.json")

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
SOURCE = "Open-Meteo"


class WeatherAPIError(RuntimeError):
    """A provider failure, described without quoting the request URL."""


def _weather_api_get(url: str, params: Dict[str, str]) -> Dict[str, Any]:
    """Call an Open-Meteo endpoint."""
    try:
        return _weather_api_get_raw(url, params)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 429:
            raise WeatherAPIError("Open-Meteo rate limit reached (429).") from None
        if status == 400:
            raise WeatherAPIError("Open-Meteo rejected the request (400).") from None
        raise WeatherAPIError(f"Open-Meteo returned HTTP {status}.") from None
    except httpx.HTTPError as exc:
        raise WeatherAPIError(
            f"Could not reach Open-Meteo: {type(exc).__name__}"
        ) from None


#: Measured 2026-10-01 from where Sage runs: about 1 in 3 new connections to
#: api.open-meteo.com (188.40.99.226) stalls -- no handshake, or no answer for
#: many seconds -- while the rest answer in ~1 s. The first live call sat on
#: one for 35 s and missed the tool's limit; retrying one at a time still took
#: up to 20 s. So attempts overlap, started on a schedule whether or not the
#: earlier ones are still waiting, and the first answer wins. The stalls come
#: in bursts (four overlapping attempts inside 5 s all failed once), so the
#: schedule is spread over 12 s rather than packed: worst case ~23 s, inside
#: the tool's 45 s.
_REQUEST_TIMEOUT = httpx.Timeout(8.0, connect=3.0)
_STARTS = (0.0, 1.5, 3.0, 5.0, 8.0, 12.0)


def _attempt(url: str, params: Dict[str, str]) -> httpx.Response:
    resp = httpx.get(url, params=params, timeout=_REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp


def _weather_api_get_raw(url: str, params: Dict[str, str]) -> Dict[str, Any]:
    pool = ThreadPoolExecutor(max_workers=len(_STARTS), thread_name_prefix="weather")
    pending: set[Future] = set()
    last_error: Optional[BaseException] = None
    began = time.monotonic()
    started = 0
    try:
        while started < len(_STARTS) or pending:
            if started < len(_STARTS) and time.monotonic() - began >= _STARTS[started]:
                pending.add(pool.submit(_attempt, url, params))
                started += 1
                continue
            due = (
                max(0.0, began + _STARTS[started] - time.monotonic())
                if started < len(_STARTS)
                else None
            )
            if not pending:
                time.sleep(due or 0.0)
                continue
            done, pending = wait(pending, timeout=due, return_when=FIRST_COMPLETED)
            for future in done:
                error = future.exception()
                if error is None:
                    return future.result().json()
                if isinstance(error, httpx.HTTPStatusError):
                    # The server answered: asking again will not change it.
                    raise error
                last_error = error
        raise last_error or httpx.ConnectTimeout("no attempt finished")
    finally:
        # Stalled attempts end on their own timeouts; nobody waits for them.
        pool.shutdown(wait=False)


#: Metric, because the machine this runs on is in the Philippines and the old
#: imperial default was a US assumption nobody chose. Configurable all the
#: same: the units belong to the reader, not to the provider.
DEFAULT_UNITS = "metric"
UNITS = ("metric", "imperial")
WIND_UNITS = ("kmh", "ms", "mph")
DEFAULT_WIND_UNIT = "kmh"

#: Above this chance of precipitation an hour is worth mentioning in a
#: one-line briefing. Below it, saying "20% chance of rain" every morning
#: trains the reader to ignore the line.
RAIN_LIKELY = 0.4

#: How far ahead the briefing line looks for rain: the rest of the day, not
#: tomorrow afternoon.
RAIN_LOOKAHEAD_HOURS = 12

#: A device fix this close to the pinned home is "home": the panel names the
#: city rather than showing bare coordinates.
HOME_RADIUS_KM = 30.0

_WIND_LABELS = {"kmh": "km/h", "ms": "m/s", "mph": "mph"}


def unit_labels(units: str, wind_unit: Optional[str] = None) -> Dict[str, str]:
    temp = "°F" if units == "imperial" else "°C"
    if wind_unit not in _WIND_LABELS:
        wind_unit = "mph" if units == "imperial" else "ms"
    return {"temp": temp, "speed": _WIND_LABELS[wind_unit]}


# --- WMO weather codes -------------------------------------------------------

#: WMO code -> (words, icon). The icon names are what the panel draws.
_WMO: Dict[int, Tuple[str, str]] = {
    0: ("clear sky", "clear"),
    1: ("mainly clear", "clear"),
    2: ("partly cloudy", "partly-cloudy"),
    3: ("overcast", "cloudy"),
    45: ("fog", "fog"),
    48: ("freezing fog", "fog"),
    51: ("light drizzle", "drizzle"),
    53: ("drizzle", "drizzle"),
    55: ("heavy drizzle", "drizzle"),
    56: ("freezing drizzle", "drizzle"),
    57: ("freezing drizzle", "drizzle"),
    61: ("light rain", "rain"),
    63: ("rain", "rain"),
    65: ("heavy rain", "rain"),
    66: ("freezing rain", "rain"),
    67: ("freezing rain", "rain"),
    71: ("light snow", "snow"),
    73: ("snow", "snow"),
    75: ("heavy snow", "snow"),
    77: ("snow grains", "snow"),
    80: ("light showers", "showers"),
    81: ("showers", "showers"),
    82: ("violent showers", "showers"),
    85: ("snow showers", "snow"),
    86: ("heavy snow showers", "snow"),
    95: ("thunderstorm", "thunder"),
    96: ("thunderstorm with hail", "thunder"),
    99: ("thunderstorm with heavy hail", "thunder"),
}


def describe_code(code: Any) -> Tuple[str, str]:
    try:
        return _WMO[int(code)]
    except (TypeError, ValueError, KeyError):
        return ("conditions unknown", "cloudy")


# --- Places -------------------------------------------------------------------


def _split_place(text: str) -> Tuple[str, Optional[str]]:
    """'Calamba,PH' -> ('Calamba', 'PH'); a trailing two-letter part is a country."""
    parts = [p.strip() for p in str(text or "").split(",") if p.strip()]
    if len(parts) >= 2 and len(parts[-1]) == 2 and parts[-1].isalpha():
        return ", ".join(parts[:-1]), parts[-1].upper()
    return ", ".join(parts), None


def _place_from_hit(hit: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": str(hit.get("name") or ""),
        "admin1": str(hit.get("admin1") or ""),
        "admin2": str(hit.get("admin2") or ""),
        "country": str(hit.get("country") or ""),
        "country_code": str(hit.get("country_code") or ""),
        "latitude": float(hit["latitude"]),
        "longitude": float(hit["longitude"]),
        "timezone": str(hit.get("timezone") or ""),
        "population": hit.get("population"),
    }


def search_places(query: str, count: int = 8) -> List[Dict[str, Any]]:
    """Places matching *query*, most likely first.

    There are four Calambas in the Philippines; the caller (Settings) shows
    them with province so the user picks, rather than trusting the first.
    """
    name, country = _split_place(query)
    if not name:
        return []
    params = {"name": name, "count": str(max(1, min(count, 20))), "language": "en"}
    if country:
        params["countryCode"] = country
    data = _weather_api_get(GEOCODE_URL, params)
    return [_place_from_hit(h) for h in data.get("results") or [] if "latitude" in h]


def place_label(place: Dict[str, Any]) -> str:
    """'Calamba, Laguna' -- province when the API has one, else region/country."""
    name = place.get("name") or ""
    area = str(place.get("admin2") or "").replace("Province of ", "")
    area = area or place.get("admin1") or place.get("country") or ""
    return f"{name}, {area}" if area and area != name else name


def _distance_km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 6371.0 * 2 * math.asin(math.sqrt(h))


_geocode_cache: Dict[str, Dict[str, Any]] = {}
_geocode_lock = threading.Lock()


def geocode(query: str) -> Optional[Dict[str, Any]]:
    """The most likely place for *query*, cached for the process."""
    key = query.strip().lower()
    with _geocode_lock:
        if key in _geocode_cache:
            return _geocode_cache[key]
    hits = search_places(query, count=1)
    if not hits:
        return None
    with _geocode_lock:
        _geocode_cache[key] = hits[0]
    return hits[0]


def home_place(config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The pinned place, else the old ``location`` string geocoded."""
    place = config.get("place")
    if isinstance(place, dict) and "latitude" in place and "longitude" in place:
        return place
    location = str(config.get("location") or "Manila,PH")
    return geocode(location)


def resolve_place(
    config: Dict[str, Any], explicit: Optional[str] = None
) -> Tuple[str, Optional[Tuple[float, float]]]:
    """Which place to report for, best source first.

    A place the user named wins outright -- asking for Tokyo must not be
    answered with wherever the laptop is sitting. Failing that, the Windows
    Location Service is asked, so a briefing follows the machine when it
    moves. The configured place is the backstop, and it is what a headless
    run falls back to whenever a fix is slow, refused or unavailable.

    Returns (name, device coordinates or None).
    """
    if explicit:
        return explicit, None

    place = config.get("place")
    if isinstance(place, dict) and place.get("name"):
        configured = place_label(place)
    else:
        configured = str(config.get("location") or "Manila,PH")
    if config.get("use_device_location") is False:
        return configured, None

    from openjarvis.core.device_location import current_coordinates

    return configured, current_coordinates()


def locate(
    config: Dict[str, Any], explicit: Optional[str] = None
) -> Tuple[Dict[str, Any], bool]:
    """(place dict with latitude/longitude, located_by_device).

    Raises WeatherAPIError when a named place cannot be found.
    """
    name, coords = resolve_place(config, explicit)
    if explicit:
        found = geocode(explicit)
        if found is None:
            raise WeatherAPIError(f"Could not find a place called {explicit!r}.")
        return found, False

    home = home_place(config)
    if coords is not None:
        if (
            home
            and _distance_km(coords, (home["latitude"], home["longitude"]))
            <= HOME_RADIUS_KM
        ):
            return {**home, "latitude": coords[0], "longitude": coords[1]}, True
        return {"name": "", "latitude": coords[0], "longitude": coords[1]}, True
    if home is None:
        raise WeatherAPIError(f"Could not find a place called {name!r}.")
    return home, False


# --- Forecast -----------------------------------------------------------------

_CURRENT_FIELDS = (
    "temperature_2m,apparent_temperature,relative_humidity_2m,is_day,"
    "weather_code,wind_speed_10m,wind_direction_10m,pressure_msl,uv_index"
)
_HOURLY_FIELDS = "temperature_2m,precipitation_probability,weather_code,is_day"
_DAILY_FIELDS = (
    "weather_code,temperature_2m_max,temperature_2m_min,"
    "precipitation_probability_max,uv_index_max,sunrise,sunset"
)


#: Open-Meteo updates its models every 15 minutes or slower, so a question
#: asked again (or the panel and a follow-up) reuses the last answer.
FORECAST_CACHE_SECONDS = 600.0
_forecast_cache: Dict[Tuple[Tuple[str, str], ...], Tuple[float, Dict[str, Any]]] = {}
_forecast_lock = threading.Lock()


def fetch_forecast(
    latitude: float,
    longitude: float,
    units: str = DEFAULT_UNITS,
    wind_unit: Optional[str] = None,
    days: int = 7,
) -> Dict[str, Any]:
    """One call: current conditions, hourly and daily, in the place's own time."""
    labels_wind = (
        wind_unit
        if wind_unit in WIND_UNITS
        else ("mph" if units == "imperial" else "ms")
    )
    params = {
        # ~1 km: finer changes nothing in a forecast, and it lets two
        # questions from the same desk share the cache below.
        "latitude": f"{latitude:.2f}",
        "longitude": f"{longitude:.2f}",
        "current": _CURRENT_FIELDS,
        "hourly": _HOURLY_FIELDS,
        "daily": _DAILY_FIELDS,
        "timezone": "auto",
        "forecast_days": str(days),
        "temperature_unit": "fahrenheit" if units == "imperial" else "celsius",
        "wind_speed_unit": labels_wind,
    }
    key = tuple(sorted(params.items()))
    with _forecast_lock:
        hit = _forecast_cache.get(key)
        if hit and time.monotonic() - hit[0] < FORECAST_CACHE_SECONDS:
            return hit[1]
    data = _weather_api_get(FORECAST_URL, params)
    with _forecast_lock:
        _forecast_cache[key] = (time.monotonic(), data)
    return data


def _num(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) else None


def _at(series: Dict[str, Any], key: str, i: int) -> Any:
    values = series.get(key) or []
    return values[i] if i < len(values) else None


def build_report(
    data: Dict[str, Any],
    place: Dict[str, Any],
    units: str = DEFAULT_UNITS,
    wind_unit: Optional[str] = None,
) -> Dict[str, Any]:
    """The panel's data: plain numbers, labelled, nothing provider-shaped."""
    labels = unit_labels(units, wind_unit)
    current = data.get("current") or {}
    now = str(current.get("time") or "")
    words, icon = describe_code(current.get("weather_code"))

    hourly_in = data.get("hourly") or {}
    times = list(hourly_in.get("time") or [])
    # Hours from the current one on; the API's "now" is a quarter-hour stamp.
    start = next((i for i, t in enumerate(times) if t[:13] >= now[:13]), 0)
    hourly = []
    for i in range(start, min(start + 24, len(times))):
        rain = _num(_at(hourly_in, "precipitation_probability", i))
        hourly.append(
            {
                "time": times[i],
                "temp": _num(_at(hourly_in, "temperature_2m", i)),
                "rain_chance": None if rain is None else rain / 100.0,
                "icon": describe_code(_at(hourly_in, "weather_code", i))[1],
                "is_day": bool(_at(hourly_in, "is_day", i)),
            }
        )

    daily_in = data.get("daily") or {}
    daily = []
    for i, day in enumerate(daily_in.get("time") or []):
        d_words, d_icon = describe_code(_at(daily_in, "weather_code", i))
        rain = _num(_at(daily_in, "precipitation_probability_max", i))
        daily.append(
            {
                "date": day,
                "conditions": d_words,
                "icon": d_icon,
                "high": _num(_at(daily_in, "temperature_2m_max", i)),
                "low": _num(_at(daily_in, "temperature_2m_min", i)),
                "rain_chance": None if rain is None else rain / 100.0,
                "uv_max": _num(_at(daily_in, "uv_index_max", i)),
                "sunrise": _at(daily_in, "sunrise", i),
                "sunset": _at(daily_in, "sunset", i),
            }
        )

    name = place_label(place) if place.get("name") else ""
    return {
        "place": name,
        "latitude": place.get("latitude"),
        "longitude": place.get("longitude"),
        "timezone": data.get("timezone") or place.get("timezone") or "",
        "observed_at": now,
        "units": units,
        "temp_unit": labels["temp"],
        "speed_unit": labels["speed"],
        "source": SOURCE,
        "current": {
            "temp": _num(current.get("temperature_2m")),
            "feels_like": _num(current.get("apparent_temperature")),
            "humidity": _num(current.get("relative_humidity_2m")),
            "wind_speed": _num(current.get("wind_speed_10m")),
            "wind_direction": _num(current.get("wind_direction_10m")),
            "pressure": _num(current.get("pressure_msl")),
            "uv_index": _num(current.get("uv_index")),
            "is_day": bool(current.get("is_day", 1)),
            "conditions": words,
            "icon": icon,
        },
        "hourly": hourly,
        "daily": daily,
    }


def _hour_label(stamp: str) -> str:
    try:
        return datetime.strptime(stamp[:16], "%Y-%m-%dT%H:%M").strftime(
            "%-I %p" if os.name != "nt" else "%#I %p"
        )
    except ValueError:
        return stamp or "later"


def _first_wet_hour(report: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The next hour likely enough to be worth an umbrella, today."""
    for entry in (report.get("hourly") or [])[:RAIN_LOOKAHEAD_HOURS]:
        chance = entry.get("rain_chance")
        if isinstance(chance, (int, float)) and chance >= RAIN_LIKELY:
            return entry
    return None


def summarize(report: Dict[str, Any], rain: bool = True) -> str:
    """One decision-shaped line: what it is now, and whether to expect rain.

    Written for a spoken briefing that already carries mail, calendar and
    Teams, so it answers the only question a morning weather line is asked --
    do I need an umbrella -- rather than reciting a forecast table. ``rain``
    off drops that clause (the car briefing: a short drive, spoken while Waze
    counts down).
    """
    current = report.get("current") or {}
    temp = current.get("temp")
    shown = (
        f"{round(temp)}{report.get('temp_unit', '°C')}"
        if isinstance(temp, (int, float))
        else "?"
    )
    line = f"{shown}, {current.get('conditions') or 'conditions unknown'}"
    if not rain or not report.get("hourly"):
        return line
    wet = _first_wet_hour(report)
    if wet is None:
        return f"{line} — no rain expected"
    chance = round(float(wet["rain_chance"]) * 100)
    # The hour already under way: "around 5 PM" said at 5:45 reads as past.
    if str(wet["time"])[:13] == str(report.get("observed_at") or "")[:13]:
        return f"{line} — rain likely now ({chance}%)"
    return f"{line} — rain likely around {_hour_label(str(wet['time']))} ({chance}%)"


def load_config(path: Path | str = _DEFAULT_TOKEN_PATH) -> Dict[str, Any]:
    """weather.json, or {} when there is none (Open-Meteo needs no key)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return data if isinstance(data, dict) else {}


def report_for(
    config: Dict[str, Any], explicit: Optional[str] = None
) -> Tuple[Dict[str, Any], bool]:
    """(report, located_by_device) for the configured or named place."""
    place, by_device = locate(config, explicit)
    units = str(config.get("units") or DEFAULT_UNITS)
    wind_unit = config.get("wind_unit") or DEFAULT_WIND_UNIT
    data = fetch_forecast(place["latitude"], place["longitude"], units, wind_unit)
    return build_report(data, place, units, wind_unit), by_device


@ConnectorRegistry.register("weather")
class WeatherConnector(BaseConnector):
    """Current weather and the day's rain outlook, from Open-Meteo."""

    connector_id = "weather"
    display_name = "Weather"
    auth_type = "token"

    def __init__(self, *, token_path: str = _DEFAULT_TOKEN_PATH) -> None:
        self._token_path = Path(token_path)
        self._status = SyncStatus()

    def _load_config(self) -> Dict[str, Any]:
        return load_config(self._token_path)

    def is_connected(self) -> bool:
        # No key to hold: the settings file existing is what "set up" means.
        if not self._token_path.exists():
            return False
        try:
            return isinstance(
                json.loads(self._token_path.read_text(encoding="utf-8")), dict
            )
        except (json.JSONDecodeError, OSError):
            return False

    def disconnect(self) -> None:
        if self._token_path.exists():
            self._token_path.unlink()

    def sync(
        self, *, since: Optional[datetime] = None, cursor: Optional[str] = None
    ) -> Iterator[Document]:
        """Yield one document: what it is doing now, and whether rain is coming."""
        report, _ = report_for(self._load_config())
        summary = summarize(report)
        location = report["place"] or "here"
        current = report["current"]

        yield Document(
            doc_id=f"weather-current-{location}",
            source="weather",
            doc_type="current",
            content=summary,
            title=f"Weather — {location}",
            timestamp=datetime.now(),
            metadata={
                "location": location,
                "units": report["units"],
                "summary": summary,
                "temp": current["temp"],
                "feels_like": current["feels_like"],
                "conditions": current["conditions"],
                "humidity": current["humidity"],
                "wind_speed": current["wind_speed"],
                "temp_unit": report["temp_unit"],
                "speed_unit": report["speed_unit"],
            },
        )

        self._status.state = "idle"
        self._status.last_sync = datetime.now()

    def sync_status(self) -> SyncStatus:
        return self._status
