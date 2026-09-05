"""M35: resolve a destination, brief the drive, and return a Waze handoff.

No browser launch, location guessing, saved-place writes, or provider retries.
In particular a phone's origin must never silently become the server's origin.
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from openjarvis.connectors import weather
from openjarvis.core.config import NavigationConfig, load_config
from openjarvis.core.paths import get_config_dir
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
ROUTE_FIELDS = "routes.duration,routes.staticDuration,routes.distanceMeters"
PLACE_FIELDS = "places.id,places.displayName,places.formattedAddress,places.location"


class NavigationError(ValueError):
    """Safe, fixed error text; never provider bodies, URLs or credentials."""


def coordinates(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        raise NavigationError("Provide latitude and longitude for the location.")
    result = {}
    for key, limit in (("latitude", 90), ("longitude", 180)):
        number = value.get(key)
        if (
            isinstance(number, bool)
            or not isinstance(number, (int, float))
            or not math.isfinite(number)
            or abs(number) > limit
        ):
            raise NavigationError("Location coordinates are missing or invalid.")
        result[key] = float(number)
    return result


def waze_link(
    destination: str,
    point: dict[str, float] | None = None,
    *,
    app: bool = False,
) -> str:
    """A Waze link, either as a web URL or straight into the app.

    Both forms carry ``navigate=yes``, but only the app scheme reliably acts
    on it. Opening the https form on iOS can land in Safari first and hand
    off to Waze on a second hop, which drops the intent: Waze opens showing
    the map and no route, which is exactly what the first real drive did.

    Chat keeps the https form -- it is tappable, and still works for someone
    without Waze installed. The phone endpoint uses the app form, where Waze
    is known to be present because the Shortcut is opening it.
    """
    params = {"utm_source": "sage"}
    if point is None:
        params["q"] = destination
    else:
        # q travels with ll, as every example in Waze's deep-link docs does.
        # Coordinates alone opened the planned-drive planner ("Find the best
        # time to leave") with a Go now button rather than navigating: with
        # no name to show, Waze appears to treat the link as a trip to
        # schedule instead of one to start.
        params["q"] = destination
        params["ll"] = f"{point['latitude']},{point['longitude']}"
        params["navigate"] = "yes"
    base = "waze://?" if app else "https://waze.com/ul?"
    return base + urlencode(params)


def _post(url: str, key: str, fields: str, body: dict) -> dict:
    try:
        response = httpx.post(
            url,
            headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": fields},
            json=body,
            timeout=10.0,
            follow_redirects=False,
        )
        if response.status_code != 200:
            if response.status_code == 403:
                try:
                    details = response.json().get("error", {}).get("details", [])
                    disabled = any(
                        isinstance(item, dict)
                        and item.get("reason") == "SERVICE_DISABLED"
                        for item in details
                    )
                except (ValueError, AttributeError, TypeError):
                    disabled = False
                if disabled:
                    service = "Places API (New)" if url == PLACES_URL else "Routes API"
                    raise NavigationError(
                        f"{service} is disabled on the Google project."
                    )
            raise NavigationError(
                f"Google navigation service returned HTTP {response.status_code}; "
                "check API enablement, restrictions, billing and quota."
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise NavigationError("Google navigation response was invalid.")
        return payload
    except (httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, NavigationError):
            raise
        raise NavigationError("Google navigation service is unavailable.") from None


def search_places(query: str, key: str, origin: dict | None) -> list[dict]:
    body: dict[str, Any] = {"textQuery": query, "pageSize": 5}
    if origin is not None:
        body["locationBias"] = {"circle": {"center": origin, "radius": 50000.0}}
    payload = _post(PLACES_URL, key, PLACE_FIELDS, body)
    places = payload.get("places", [])
    if not isinstance(places, list):
        raise NavigationError("Google Places response was invalid.")
    candidates = []
    for place in places:
        if not isinstance(place, dict):
            raise NavigationError("Google Places response was invalid.")
        name = place.get("displayName")
        name = name.get("text") if isinstance(name, dict) else None
        if not isinstance(name, str) or not name or not place.get("id"):
            raise NavigationError("Google Places returned an incomplete destination.")
        candidates.append(
            {
                "place_id": place["id"],
                "name": name,
                "address": place.get("formattedAddress") or "Address unavailable",
                "coordinates": coordinates(place.get("location")),
            }
        )
    return candidates


def _duration(value: Any) -> float | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d+)?s", value):
        return None
    seconds = float(value[:-1])
    return seconds if math.isfinite(seconds) else None


def compute_route(origin: dict, destination: dict, key: str) -> dict:
    payload = _post(
        ROUTES_URL,
        key,
        ROUTE_FIELDS,
        {
            "origin": {"location": {"latLng": origin}},
            "destination": {"location": {"latLng": destination}},
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_AWARE",
            "computeAlternativeRoutes": False,
            "units": "METRIC",
        },
    )
    routes = payload.get("routes")
    if not isinstance(routes, list) or not routes or not isinstance(routes[0], dict):
        raise NavigationError("Google Routes did not return a driving route.")
    route = routes[0]
    duration = _duration(route.get("duration"))
    if duration is None:
        raise NavigationError("Google Routes returned no usable ETA.")
    static = _duration(route.get("staticDuration"))
    distance = route.get("distanceMeters")
    if (
        isinstance(distance, bool)
        or not isinstance(distance, (int, float))
        or not math.isfinite(distance)
        or distance < 0
    ):
        distance = None
    return {
        "duration_seconds": duration,
        "distance_meters": distance,
        "traffic_delay_seconds": None if static is None else max(0, duration - static),
    }


def destination_weather(root: Path, point: dict) -> str:
    try:
        config = json.loads((root / "connectors" / "weather.json").read_text("utf-8"))
        key = config.get("api_key") if isinstance(config, dict) else None
        if not key:
            return "Weather unavailable: weather is not configured."
        units = config.get("units") or weather.DEFAULT_UNITS
        coords = (point["latitude"], point["longitude"])
        current = weather.fetch_current(key, "", units, coords)
        try:
            forecast = weather.fetch_forecast(key, "", units, coords=coords)
        except Exception:
            forecast = None
        return weather.summarize(current, forecast, units)
    except Exception:
        return "Weather unavailable for this drive."


def _saved_places(root: Path) -> dict:
    path = root / "saved_places.json"
    try:
        payload = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise NavigationError(
            "Saved places could not be read; check saved_places.json."
        ) from None
    if not isinstance(payload, dict):
        raise NavigationError("Saved places must be an object keyed by place name.")
    normalized = {}
    for name, point in payload.items():
        normalized_name = name.strip().casefold()
        if not normalized_name or normalized_name in normalized:
            raise NavigationError("Saved place names are empty or duplicated.")
        normalized[normalized_name] = coordinates(point)
    return normalized


@ToolRegistry.register("navigate")
class NavigateTool(BaseTool):
    """Prepare a drive; opening navigation remains the caller's action."""

    tool_id = "navigate"
    is_local = False

    def __init__(
        self,
        *,
        config: NavigationConfig | None = None,
        data_dir: Path | None = None,
    ) -> None:
        self._config = config
        self._root = data_dir if data_dir is not None else get_config_dir()

    @property
    def spec(self) -> ToolSpec:
        point = {
            "type": "object",
            "properties": {
                "latitude": {"type": "number", "minimum": -90, "maximum": 90},
                "longitude": {"type": "number", "minimum": -180, "maximum": 180},
            },
            "required": ["latitude", "longitude"],
            "additionalProperties": False,
        }
        return ToolSpec(
            name=self.tool_id,
            description=(
                "Prepare driving directions to a saved place, new business or address. "
                "Use for 'drive home', 'navigate to', traffic or driving ETA requests. "
                "Returns briefing and Waze link; it DOES NOT open Waze. Ask the user "
                "to choose when candidates are returned. Never invent coordinates "
                "or a place ID. Origin must be supplied by the user/phone; "
                "no PC fallback."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "destination": {"type": "string", "minLength": 1, "maxLength": 300},
                    "origin": {
                        **point,
                        "description": "Current phone/user coordinates.",
                    },
                    "destination_coordinates": point,
                    "place_id": {
                        "type": "string",
                        "description": (
                            "ID chosen by the user from candidates returned "
                            "for this destination."
                        ),
                    },
                },
                "required": ["destination"],
                "additionalProperties": False,
            },
            timeout_seconds=100.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            return self._execute(params)
        except NavigationError as exc:
            return ToolResult(tool_name=self.tool_id, content=str(exc), success=False)
        except Exception:
            # The executor also sanitizes, but no exception text is useful here.
            return ToolResult(
                tool_name=self.tool_id,
                success=False,
                content="Navigation could not be prepared.",
            )

    def _execute(self, params: dict) -> ToolResult:
        query = params.get("destination")
        if not isinstance(query, str) or not query.strip() or len(query) > 300:
            raise NavigationError(
                "Name a destination or provide its address (1–300 characters)."
            )
        query = query.strip()
        origin = coordinates(params["origin"]) if "origin" in params else None
        config = self._config if self._config is not None else load_config().navigation
        point = None
        if "destination_coordinates" in params:
            point = coordinates(params["destination_coordinates"])
        else:
            point = _saved_places(self._root).get(query.casefold())
        key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
        if point is None:
            if (
                config.places_enabled is not True
                or config.places_quota_confirmed is not True
            ):
                return self._selection(
                    query,
                    [],
                    "Place lookup is disabled. Choose the destination in Waze.",
                )
            if not key:
                return self._selection(
                    query,
                    [],
                    "Place lookup is not configured. Choose the destination in Waze.",
                )
            try:
                candidates = search_places(query, key, origin)
            except NavigationError as exc:
                return self._selection(query, [], str(exc))
            chosen_id = params.get("place_id")
            if chosen_id:
                selected = [c for c in candidates if c["place_id"] == chosen_id]
            else:
                # Google ranks its text search by relevance, so the first
                # result is taken rather than asking. Requiring exactly one
                # candidate meant almost every real place asked instead --
                # "SM City Calamba" returns the mall, a diner inside it and
                # the supermarket -- and a driver cannot pick from a list.
                # The chosen name is spoken back below, so a wrong pick is
                # heard before the car moves rather than discovered later.
                selected = candidates[:1]
            if len(selected) != 1:
                return self._selection(
                    query,
                    candidates,
                    (
                        "Choose a matching destination; no route has been selected."
                        if candidates
                        else "No matching destination found. Try a fuller address."
                    ),
                )
            point = selected[0]["coordinates"]
            query = selected[0]["name"]

        route = None
        if origin is None:
            reason = "ETA unavailable: provide your current location."
        elif (
            config.routes_enabled is not True
            or config.routes_quota_confirmed is not True
        ):
            reason = (
                "ETA unavailable: Google Routes is disabled "
                "pending API and quota setup."
            )
        elif not key:
            reason = "ETA unavailable: Google Routes is not configured."
        else:
            try:
                route = compute_route(origin, point, key)
                reason = ""
            except NavigationError as exc:
                reason = f"ETA unavailable: {exc}"
        # `query` is the chosen candidate's name by this point, not what was
        # asked for -- which is the useful thing to say aloud, since "SM City
        # Calamba" can resolve to a diner inside the mall and the only moment
        # to catch that is before setting off.
        line = f"Directions to {query}. "
        if route is not None:
            minutes = math.ceil(route["duration_seconds"] / 60)
            line += f"Google estimates {minutes} minutes driving. "
            delay = route["traffic_delay_seconds"]
            if delay is not None:
                line += f"Estimated traffic delay: {math.ceil(delay / 60)} minutes. "
            else:
                line += "Traffic delay unavailable. "
            line += "Waze may choose a different route and ETA. "
        else:
            line += reason + " "
        weather_line = (
            destination_weather(self._root, point)
            if config.weather_enabled is True
            else "Weather unavailable: navigation weather is disabled."
        )
        link = waze_link(query, point)
        # No instruction to open anything: this line is spoken aloud through
        # the phone endpoint, where the Shortcut opens Waze itself, and
        # hearing "open the Waze link" as Waze opens is a small lie. Chat
        # still gets the tappable link in `content` below, which needs no
        # narration to explain it.
        line += f"At the destination: {weather_line} Drive safely."
        return ToolResult(
            tool_name=self.tool_id,
            success=True,
            content=f"{line}\n[Open Waze]({link})",
            metadata={
                "status": "ready",
                "destination": query,
                "maps_url": link,
                "maps_app_url": waze_link(query, point, app=True),
                "briefing": line,
                "route": route,
                "weather": weather_line,
                "destination_coordinates": point,
                "navigation_started": False,
            },
        )

    def _selection(self, query: str, candidates: list, reason: str) -> ToolResult:
        link = waze_link(query)
        choices = "\n".join(
            f"{c['name']} — {c['address']} (place_id: {c['place_id']})"
            for c in candidates
        )
        return ToolResult(
            tool_name=self.tool_id,
            success=True,
            content=f"{reason}\n{choices}\n[Search in Waze]({link})",
            metadata={
                "status": "needs_selection",
                "candidates": candidates,
                "maps_url": link,
                "maps_app_url": waze_link(query, app=True),
                "route": None,
                "navigation_started": False,
            },
        )
