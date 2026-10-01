"""Settings > Weather (M41): the pinned place, device location and units.

Kept in ``connectors/weather.json`` on the server, because the chat tool and
both briefings read it there and a Telegram or scheduled turn never passes
through the app. "Open the panel automatically" only matters where the panel
is shown, so the app keeps that one.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from openjarvis.connectors import weather

_lock = threading.Lock()

_PLACE_KEYS = (
    "name",
    "admin1",
    "admin2",
    "country",
    "country_code",
    "latitude",
    "longitude",
    "timezone",
)


def _public(config: Dict[str, Any]) -> Dict[str, Any]:
    place = config.get("place") if isinstance(config.get("place"), dict) else None
    return {
        "place": place,
        "place_label": weather.place_label(place) if place else "",
        # A file from before M41 names a city but pins no coordinates.
        "legacy_location": "" if place else str(config.get("location") or ""),
        "use_device_location": config.get("use_device_location") is not False,
        "units": config.get("units") or weather.DEFAULT_UNITS,
        "wind_unit": config.get("wind_unit") or weather.DEFAULT_WIND_UNIT,
        "units_options": list(weather.UNITS),
        "wind_unit_options": list(weather.WIND_UNITS),
        "source": weather.SOURCE,
    }


def _clean_place(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("place must be an object")
    try:
        lat, lon = float(value["latitude"]), float(value["longitude"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("place needs latitude and longitude") from None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("place coordinates are out of range")
    if not str(value.get("name") or "").strip():
        raise ValueError("place needs a name")
    place = {k: value.get(k) for k in _PLACE_KEYS if value.get(k) is not None}
    place.update(latitude=lat, longitude=lon, name=str(value["name"]).strip())
    return place


def update(changes: Dict[str, Any], path: Path) -> Dict[str, Any]:
    """Merge *changes* into weather.json, keeping keys this page does not own."""
    with _lock:
        config = weather.load_config(path)
        for key, value in (changes or {}).items():
            if key == "place":
                config["place"] = _clean_place(value)
                # The city name no longer decides anything once a place is pinned.
                config.pop("location", None)
            elif key == "use_device_location":
                config[key] = bool(value)
            elif key == "units":
                if value not in weather.UNITS:
                    raise ValueError(f"Unknown units {value!r}")
                config[key] = value
            elif key == "wind_unit":
                if value not in weather.WIND_UNITS:
                    raise ValueError(f"Unknown wind unit {value!r}")
                config[key] = value
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        return config


def create_weather_router(*, settings_path: Optional[Path] = None) -> APIRouter:
    router = APIRouter(prefix="/v1/weather", tags=["weather"])

    def _path() -> Path:
        return settings_path or Path(weather._DEFAULT_TOKEN_PATH)

    @router.get("/settings")
    async def get_settings():
        return _public(weather.load_config(_path()))

    @router.put("/settings")
    async def put_settings(body: Dict[str, Any]):
        try:
            config = await asyncio.to_thread(update, body, _path())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _public(config)

    @router.get("/places")
    async def search_places(q: str = ""):
        if not q.strip():
            return {"results": []}
        try:
            hits = await asyncio.to_thread(weather.search_places, q, 8)
        except weather.WeatherAPIError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"results": [{**h, "label": weather.place_label(h)} for h in hits]}

    return router
