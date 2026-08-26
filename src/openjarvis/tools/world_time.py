"""World time tool — current time anywhere, computed rather than reasoned.

Asked what time it was in Japan, the local model answered correctly for the
user's own clock and then claimed UTC+9 is "3 hours ahead" of UTC+8, landing
two hours out. It had the current time all along; it failed the arithmetic.

So this returns every value already computed — the target time, the offset,
the signed difference, and a ready-made sentence — and the caller quotes it.
Same reasoning as ``check_class_schedule`` doing its date maths in Python.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

# Country and region names that are not IANA zones and have no legacy alias.
# Each maps to the zone of the place people mean when they name the country;
# only unambiguous single-zone (or overwhelmingly dominant) cases belong here.
_ALIASES: Dict[str, str] = {
    "philippines": "Asia/Manila",
    "ph": "Asia/Manila",
    "manila": "Asia/Manila",
    "uk": "Europe/London",
    "united kingdom": "Europe/London",
    "britain": "Europe/London",
    "england": "Europe/London",
    "korea": "Asia/Seoul",
    "south korea": "Asia/Seoul",
    "china": "Asia/Shanghai",
    "taiwan": "Asia/Taipei",
    "vietnam": "Asia/Ho_Chi_Minh",
    "thailand": "Asia/Bangkok",
    "indonesia": "Asia/Jakarta",
    "malaysia": "Asia/Kuala_Lumpur",
    "india": "Asia/Kolkata",
    "pakistan": "Asia/Karachi",
    "uae": "Asia/Dubai",
    "dubai": "Asia/Dubai",
    "germany": "Europe/Berlin",
    "france": "Europe/Paris",
    "spain": "Europe/Madrid",
    "italy": "Europe/Rome",
    "netherlands": "Europe/Amsterdam",
    "russia": "Europe/Moscow",
    "new zealand": "Pacific/Auckland",
    "nz": "Pacific/Auckland",
    "nepal": "Asia/Kathmandu",
    "bangladesh": "Asia/Dhaka",
    "sri lanka": "Asia/Colombo",
    "saudi arabia": "Asia/Riyadh",
    "turkey": "Europe/Istanbul",
    "greece": "Europe/Athens",
    "poland": "Europe/Warsaw",
    "sweden": "Europe/Stockholm",
    "norway": "Europe/Oslo",
    "switzerland": "Europe/Zurich",
    "belgium": "Europe/Brussels",
    "austria": "Europe/Vienna",
    "ireland": "Europe/Dublin",
    "portugal": "Europe/Lisbon",
    "egypt": "Africa/Cairo",
    "nigeria": "Africa/Lagos",
    "kenya": "Africa/Nairobi",
    "south africa": "Africa/Johannesburg",
    "israel": "Asia/Jerusalem",
    "iran": "Asia/Tehran",
    "argentina": "America/Argentina/Buenos_Aires",
    "brazil": "America/Sao_Paulo",
    "chile": "America/Santiago",
    "colombia": "America/Bogota",
    "peru": "America/Lima",
    "mexico": "America/Mexico_City",
}


def _city_index() -> Dict[str, str]:
    """Map each zone's final segment to its full name, e.g. tokyo -> Asia/Tokyo.

    Lets a bare city through: ``ZoneInfo("Tokyo")`` raises, only the full
    ``Asia/Tokyo`` resolves. Sorted so a name appearing in several regions
    resolves the same way every call rather than by set iteration order.
    """
    index: Dict[str, str] = {}
    for zone in sorted(available_timezones()):
        city = zone.rsplit("/", 1)[-1].replace("_", " ").lower()
        index.setdefault(city, zone)
    return index


def _resolve_zone(name: str) -> Tuple[Optional[ZoneInfo], str]:
    """Resolve a user-written place name to a zone, plus its canonical name."""
    raw = (name or "").strip()
    if not raw:
        return None, ""

    # Exact / legacy alias ("Japan" and "Singapore" are real zone names).
    try:
        return ZoneInfo(raw), raw
    except (ZoneInfoNotFoundError, ValueError):
        pass

    lowered = raw.lower()
    if lowered in _ALIASES:
        canonical = _ALIASES[lowered]
        return ZoneInfo(canonical), canonical

    for zone in sorted(available_timezones()):
        if zone.lower() == lowered:
            return ZoneInfo(zone), zone

    canonical = _city_index().get(lowered.replace("_", " "))
    if canonical:
        return ZoneInfo(canonical), canonical

    return None, ""


def _offset_hours(moment: datetime) -> float:
    offset = moment.utcoffset() or timedelta(0)
    return offset.total_seconds() / 3600


def _format_offset(hours: float) -> str:
    """Render an offset as UTC+8 or UTC+5:30 — some zones are not whole hours."""
    sign = "-" if hours < 0 else "+"
    total = abs(int(round(hours * 60)))
    whole, minutes = divmod(total, 60)
    return f"UTC{sign}{whole}" + (f":{minutes:02d}" if minutes else "")


def _format_magnitude(hours: float) -> str:
    """'1 hour', '2 hours and 30 minutes' — some offsets are not whole hours."""
    total = abs(int(round(hours * 60)))
    whole, minutes = divmod(total, 60)
    parts = []
    if whole:
        parts.append(f"{whole} hour{'s' if whole != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minutes")
    return " and ".join(parts)


def _format_difference(hours: float) -> str:
    if hours == 0:
        return "the same time"
    return f"{_format_magnitude(hours)} {'ahead' if hours > 0 else 'behind'}"


def _comparison_phrase(hours: float, reference_label: str) -> str:
    """Grammatical comparison: 'ahead of' takes a preposition, 'behind' does not."""
    if hours == 0:
        return f"the same time as {reference_label}"
    direction = "ahead of" if hours > 0 else "behind"
    return f"{_format_magnitude(hours)} {direction} {reference_label}"


@ToolRegistry.register("world_time")
class WorldTimeTool(BaseTool):
    """Report the current time in a place and its offset from another."""

    tool_id = "world_time"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="world_time",
            description=(
                "Current date and time in a given place, and how far ahead or "
                "behind it is from here. Use this for ANY question about the "
                "time somewhere else or the difference between two places, "
                "and quote the 'summary' it returns — never work out a UTC "
                "offset or add hours yourself."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": (
                            "Place to report the time for: a country, city, "
                            "or IANA zone. e.g. 'Japan', 'Tokyo', "
                            "'Asia/Tokyo', 'New York'."
                        ),
                    },
                    "compare_to": {
                        "type": "string",
                        "description": (
                            "Place to compare against. Defaults to the "
                            "user's own local time."
                        ),
                    },
                },
                "required": ["location"],
            },
            category="utility",
        )

    def execute(self, **params: Any) -> ToolResult:
        location = params.get("location", "")
        if not location:
            return ToolResult(
                tool_name=self.tool_id,
                content="Missing required parameter: location.",
                success=False,
            )

        target_zone, target_name = _resolve_zone(location)
        if target_zone is None:
            return ToolResult(
                tool_name=self.tool_id,
                content=(
                    f"Could not resolve {location!r} to a time zone. Try a "
                    "major city or an IANA name like 'Asia/Tokyo'."
                ),
                success=False,
            )

        now = datetime.now().astimezone()
        compare_raw = params.get("compare_to") or ""
        if compare_raw:
            compare_zone, compare_name = _resolve_zone(compare_raw)
            if compare_zone is None:
                return ToolResult(
                    tool_name=self.tool_id,
                    content=(
                        f"Could not resolve {compare_raw!r} to a time zone. "
                        "Try a major city or an IANA name."
                    ),
                    success=False,
                )
            reference = now.astimezone(compare_zone)
            reference_label = compare_name
        else:
            reference = now
            reference_label = "your local time"

        there = now.astimezone(target_zone)
        difference = _offset_hours(there) - _offset_hours(reference)

        payload = {
            "location": target_name,
            "time": there.strftime("%I:%M %p").lstrip("0"),
            "date": there.strftime("%A, %B %d, %Y"),
            "utc_offset": _format_offset(_offset_hours(there)),
            "compared_to": reference_label,
            "compared_to_time": reference.strftime("%I:%M %p").lstrip("0"),
            "difference": _format_difference(difference),
            "summary": (
                f"It is {there.strftime('%I:%M %p').lstrip('0')} on "
                f"{there.strftime('%A, %B %d, %Y')} in {target_name} "
                f"({_format_offset(_offset_hours(there))}), "
                f"{_comparison_phrase(difference, reference_label)}."
            ),
        }
        return ToolResult(
            tool_name=self.tool_id,
            content=json.dumps(payload),
            success=True,
        )


__all__ = ["WorldTimeTool"]
