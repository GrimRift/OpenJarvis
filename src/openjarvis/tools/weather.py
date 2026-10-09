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

import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.connectors.weather import (
    RAIN_LIKELY,
    _hour_label,
    home_place,
    load_config,
    place_label,
    report_for,
    summarize,
)
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


def _hours_line(report: Dict[str, Any]) -> str:
    """'1 AM 72%, 2 AM 55%, ...' for the next 24 hours.

    The panel draws these hours; without them "will it rain at 7 AM?" was
    answered from the summary's "rain likely now" and came out wrong while
    the panel showed 4% at 7 AM (user report, 2026-10-07).
    """
    parts = []
    for entry in report.get("hourly") or []:
        chance = entry.get("rain_chance")
        if isinstance(chance, (int, float)):
            label = _hour_label(str(entry.get("time") or ""))
            parts.append(f"{label} {round(chance * 100)}%")
    return ", ".join(parts)


def _rain_spells(report: Dict[str, Any]) -> str:
    """'now until about 4 AM (peak 72%); 3 PM to 6 PM (peak 61%)'.

    When rain starts and when it eases, so a question about one hour can be
    answered with the rain around it. A spell ends at its first dry hour.
    """
    hours = report.get("hourly") or []
    if not hours:
        return ""
    now = str(report.get("observed_at") or "")[:13]
    spells: List[str] = []
    start: Optional[int] = None
    peak = 0.0
    for i, entry in enumerate([*hours, {}]):
        chance = entry.get("rain_chance")
        if isinstance(chance, (int, float)) and chance >= RAIN_LIKELY:
            if start is None:
                start, peak = i, 0.0
            peak = max(peak, float(chance))
            continue
        if start is None:
            continue
        first = str(hours[start]["time"])
        begins = "now" if first[:13] == now else _hour_label(first)
        if i < len(hours):
            ends = _hour_label(str(hours[i]["time"]))
            span = (
                f"now until about {ends}" if begins == "now" else f"{begins} to {ends}"
            )
        else:
            span = "from now on" if begins == "now" else f"from {begins} on"
        spells.append(f"{span} (peak {round(peak * 100)}%)")
        start = None
    return "; ".join(spells) or f"not in the next {len(hours)} hours"


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
                "the result and add a later day only if the user asked about it. "
                "For a particular hour, give that hour's chance from 'Rain "
                "chance by hour', then when the nearest rain starts or eases "
                "from 'Rain likely'."
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
        if asked_for and not _named_by_user(asked_for):
            # The model filled in a place the user never said -- on 9 October
            # "Calamba, Laguna, Philippines" from the profile, while the user
            # was in Lucban. Their own location is the PC's, so use that.
            asked_for = None
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
        # Away from home (Lucban, 9 October) the town is named, so "here" is
        # never mistaken for the saved place.
        home = home_place(config) if located_here else None
        at_home = bool(home) and place == place_label(home)
        spoken = summary if not place or at_home else f"{place}: {summary}"
        days = _days_line(report)
        content = spoken
        hours = _hours_line(report)
        if hours:
            content += (
                f"\nRain likely: {_rain_spells(report)}\nRain chance by hour: {hours}"
            )
        content += f"\nNext days: {days}" if days else ""
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


#: Words in a place name that say nothing about which place it is.
_GENERIC_PLACE_WORDS = frozenset(
    {"city", "province", "of", "the", "municipality", "town", "philippines", "ph"}
)


def _named_by_user(location: str) -> bool:
    """Whether the user's own message names *location*.

    Only a place the user asked about overrides where they are ("weather in
    Manila"). With no message to check against -- a scheduled briefing, a
    call outside a chat turn -- the place is taken as given.
    """
    from openjarvis.security import page_access

    text = page_access.turn_text().lower()
    if not text.strip():
        return True
    words = [
        word
        for word in re.split(r"[^\w]+", location.lower())
        if len(word) > 2 and word not in _GENERIC_PLACE_WORDS
    ]
    return any(re.search(rf"\b{re.escape(word)}\b", text) for word in words)


__all__ = ["WeatherTool"]
