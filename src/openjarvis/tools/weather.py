"""Weather tool -- current conditions, the day's rain, and seven days ahead.

Asking Sage about the weather used to fall through to ``web_search``, which
answers from whatever a search result happened to say about a city rather
than from the location the user actually configured. This reads the same
connector the morning briefing uses, so the answer in chat and the line in
the briefing cannot disagree.

Since M41 (2026-10-01) the result also carries the whole report in
``metadata.weather``; the app draws it as the weather panel, so the spoken
answer can stay one or two sentences.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.connectors.weather import load_config, report_for, summarize
from openjarvis.core.config import DEFAULT_CONFIG_DIR
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_DEFAULT_TOKEN_PATH = str(DEFAULT_CONFIG_DIR / "connectors" / "weather.json")


def _days_line(report: Dict[str, Any]) -> str:
    """'Thu 24-32°C thunderstorm, rain 98%; Fri ...' for questions about later days."""
    unit = report.get("temp_unit", "°C")
    parts: List[str] = []
    for day in report.get("daily") or []:
        try:
            label = date.fromisoformat(str(day["date"])).strftime("%a")
        except (KeyError, ValueError):
            continue
        low, high = day.get("low"), day.get("high")
        temps = (
            f"{round(low)}-{round(high)}{unit}"
            if isinstance(low, (int, float)) and isinstance(high, (int, float))
            else "?"
        )
        rain = day.get("rain_chance")
        rain_text = (
            f", rain {round(rain * 100)}%" if isinstance(rain, (int, float)) else ""
        )
        parts.append(f"{label} {temps} {day.get('conditions', '')}{rain_text}")
    return "; ".join(parts)


_WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def match_day(report: Dict[str, Any], asked: str) -> Optional[int]:
    """Index into ``report['daily']`` for 'Friday', 'tomorrow' or '2026-10-02'.

    The panel opens on this day, so "the weather for Friday" shows Friday
    rather than today with Friday one click away (user report, 2026-10-02).
    Days come from the report, so "Friday" means the place's next Friday.
    """
    text = asked.strip().lower()
    days = [str(d.get("date") or "") for d in report.get("daily") or []]
    if not text or not days:
        return None
    if text in ("today", "tonight", "now"):
        return 0
    if text == "tomorrow":
        return 1 if len(days) > 1 else None
    if text in days:
        return days.index(text)
    for name in _WEEKDAYS:
        if name.startswith(text[:3]) and len(text) >= 3:
            for i, day in enumerate(days):
                try:
                    if date.fromisoformat(day).weekday() == _WEEKDAYS.index(name):
                        return i
                except ValueError:
                    continue
    return None


@ToolRegistry.register("weather")
class WeatherTool(BaseTool):
    """Report current weather, whether rain is coming, and the week ahead."""

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
                "Current weather, the next likely rain and a 7-day forecast for "
                "the user's own location, or a named place. Use this for ANY "
                "question about the weather, rain, temperature, the forecast, "
                "or whether to take an umbrella, rather than searching the web. "
                "The app shows the full forecast in a weather panel, so answer "
                "in one or two spoken sentences: start from the first line of "
                "the result and add a later day only if the user asked about it."
            ),
            # A Windows location fix (up to 6 s) plus a lookup and the
            # forecast, each retried once when the network stalls.
            timeout_seconds=45.0,
            parameters={
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": (
                            "Place to report for, e.g. 'Cebu City,PH' or "
                            "'Tokyo'. Omit for the user's own location."
                        ),
                    },
                    "day": {
                        "type": "string",
                        "description": (
                            "The day the user asked about, if any: 'today', "
                            "'tomorrow', a weekday such as 'Friday', or "
                            "YYYY-MM-DD. The panel opens on that day. Omit "
                            "for a general weather question."
                        ),
                    },
                },
                "required": [],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            config = load_config(self._token_path)
        except (OSError, ValueError):
            config = {}

        asked_for = str(params.get("location") or "").strip() or None
        try:
            report, located_here = report_for(config, asked_for)
        except Exception as exc:
            return ToolResult(
                tool_name="weather",
                content=f"Could not reach the weather service: {exc}",
                success=False,
            )

        summary = summarize(report)
        # Naming the place is only worth the words when the user might not
        # know it. A device fix near home is reported bare; a place they named
        # is echoed back, so an answer about Tokyo cannot be mistaken for one
        # about here; and the configured place is named too, because seeing
        # it is the signal that the location fix did not happen.
        place = report.get("place") or ""
        spoken = summary if located_here or not place else f"{place}: {summary}"
        days = _days_line(report)
        content = spoken + (f"\nNext days: {days}" if days else "")
        asked_day = str(params.get("day") or "").strip()
        focus = match_day(report, asked_day) if asked_day else None
        if focus is not None and focus > 0:
            # Named for the model, so "Friday" is answered from Friday's line.
            day = report["daily"][focus]
            content += (
                f"\nAsked about: {day['date']} ({day.get('conditions', '')}): "
                f"{day.get('advice', '')}"
            )

        current = report["current"]
        return ToolResult(
            tool_name="weather",
            content=content,
            success=True,
            metadata={
                "location": place,
                "summary": summary,
                "temp": current["temp"],
                "feels_like": current["feels_like"],
                "humidity": current["humidity"],
                "wind_speed": current["wind_speed"],
                "temp_unit": report["temp_unit"],
                "speed_unit": report["speed_unit"],
                # The panel's data (M41). Persisted with the tool call, so the
                # chat card can reopen it after a reload.
                "weather": {**report, "summary": summary, "focus_day": focus},
            },
        )


__all__ = ["WeatherTool"]
