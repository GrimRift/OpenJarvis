"""Weather tool — current conditions and the next chance of rain.

Asking Sage about the weather used to fall through to ``web_search``, which
answers from whatever a search result happened to say about a city rather
than from the location the user actually configured. This reads the same
OpenWeatherMap credentials the morning briefing uses, so the answer in chat
and the line in the briefing cannot disagree.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.connectors.weather import (
    DEFAULT_UNITS,
    fetch_current,
    fetch_forecast,
    resolve_place,
    summarize,
    unit_labels,
)
from openjarvis.core.config import DEFAULT_CONFIG_DIR
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_DEFAULT_TOKEN_PATH = str(DEFAULT_CONFIG_DIR / "connectors" / "weather.json")


@ToolRegistry.register("weather")
class WeatherTool(BaseTool):
    """Report current weather and whether rain is coming."""

    tool_id = "weather"
    is_local = False

    def __init__(self, token_path: str = _DEFAULT_TOKEN_PATH) -> None:
        super().__init__()
        self._token_path = Path(token_path)

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="weather",
            description=(
                "Current weather and the next likely rain for the user's own "
                "location, or a named place. Use this for ANY question about "
                "the weather, whether it will rain, or whether to take an "
                "umbrella, and quote the 'summary' it returns rather than "
                "searching the web for a forecast."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": (
                            "Place to report for, e.g. 'Cebu City,PH' or "
                            "'Tokyo'. Omit for the user's configured location."
                        ),
                    },
                },
                "required": [],
            },
        )

    def _config(self) -> Dict[str, Any]:
        return json.loads(self._token_path.read_text(encoding="utf-8"))

    def execute(self, **params: Any) -> ToolResult:
        try:
            config = self._config()
        except (OSError, json.JSONDecodeError):
            return ToolResult(
                tool_name="weather",
                content=(
                    "Weather is not configured. Add an OpenWeatherMap API key "
                    f"and location to {self._token_path}."
                ),
                success=False,
            )

        api_key = str(config.get("api_key") or "")
        if not api_key:
            return ToolResult(
                tool_name="weather",
                content=(
                    "Weather is not configured: no API key in "
                    f"{self._token_path}."
                ),
                success=False,
            )

        units = str(config.get("units") or DEFAULT_UNITS)
        labels = unit_labels(units)
        asked_for = params.get("location")
        location, coords = resolve_place(config, str(asked_for) if asked_for else None)

        try:
            current = fetch_current(api_key, location, units, coords)
        except Exception as exc:
            return ToolResult(
                tool_name="weather",
                content=f"Could not reach the weather service: {exc}",
                success=False,
            )

        forecast: Optional[Dict[str, Any]]
        try:
            forecast = fetch_forecast(api_key, location, units, coords=coords)
        except Exception:
            # The rain clause is worth losing; the temperature is not.
            forecast = None

        # The place the provider answered for, which is not the configured
        # city when the coordinates came from the machine.
        location = str(current.get("name") or location)
        summary = summarize(current, forecast, units)

        # Naming the place is only worth the words when the user might not
        # know it. Answering "what is the weather" with the barangay the
        # coordinates landed in ("Prinza: 25C") tells them nothing they did
        # not already know. A place they named is echoed back, so an answer
        # about Tokyo cannot be mistaken for one about here; and the
        # configured city is named too, because seeing it is the signal that
        # the location fix did not happen.
        located_here = coords is not None and not asked_for
        spoken = summary if located_here else f"{location}: {summary}"
        main = current.get("main") or {}
        wind = current.get("wind") or {}
        upcoming: List[Dict[str, Any]] = []
        for entry in (forecast or {}).get("list", [])[:4]:
            upcoming.append(
                {
                    "at": entry.get("dt_txt"),
                    "temp": (entry.get("main") or {}).get("temp"),
                    "chance_of_rain": entry.get("pop"),
                }
            )

        return ToolResult(
            tool_name="weather",
            content=spoken,
            success=True,
            metadata={
                "location": location,
                "summary": summary,
                "temp": main.get("temp"),
                "feels_like": main.get("feels_like"),
                "humidity": main.get("humidity"),
                "wind_speed": wind.get("speed"),
                "temp_unit": labels["temp"],
                "speed_unit": labels["speed"],
                "upcoming": upcoming,
            },
        )


__all__ = ["WeatherTool"]
