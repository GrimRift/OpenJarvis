"""Create a Google Calendar event.

The OAuth consent already grants ``auth/calendar`` (full read/write, not
``.readonly``) so the proactive agent can accept and decline invites. Nothing
had ever used that write access to *create* anything, so "put that in my
calendar" was the one obvious assistant request Sage could not do at all.

Two guards exist because of a failure this codebase actually has rather than
one imagined for it. The model is documented to get weekdays and years wrong
even with the correct date in its prompt --- live, it wrote "Friday, August 22"
in the same response where it had just written "Saturday, August 22". So:

* The resolved date is echoed back **with its weekday**, so a wrong date is
  visible in the reply instead of only in the calendar weeks later.
* A start more than a day in the past is refused. A backdated event is almost
  always a mistyped year, and creating one puts it somewhere the user will
  never scroll to and never find.

Times are sent as naive local datetimes paired with an IANA zone, which is how
Google resolves the wall-clock time the user meant.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_DEFAULT_DURATION_MINUTES = 60
_MAX_DURATION_MINUTES = 60 * 24 * 14
#: How far back a start may sit before it is treated as a mistyped year.
_PAST_TOLERANCE = timedelta(days=1)


def _configured_timezone() -> str:
    """The user's zone, falling back to the calendar's own default."""
    try:
        from openjarvis.core.config import load_config

        config = load_config()
    except Exception:
        return "UTC"
    for section in ("proactive", "digest", "scheduler"):
        zone = getattr(getattr(config, section, None), "timezone", "")
        if zone:
            return str(zone)
    return "UTC"


def _parse_local(value: str) -> Optional[datetime]:
    """Parse an ISO datetime, discarding any offset.

    An offset is dropped rather than honoured: the zone is supplied separately
    and Google applies it, so keeping both would let a model that appended a
    wrong offset move the event by hours.
    """
    text = (value or "").strip().replace("Z", "")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=None)


@ToolRegistry.register("create_calendar_event")
class CreateCalendarEventTool(BaseTool):
    """Add a timed event to the user's Google Calendar."""

    tool_id = "create_calendar_event"
    is_local = False

    def __init__(self, connector: Any = None, timezone: str = "") -> None:
        self._connector = connector
        self._timezone = timezone

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="create_calendar_event",
            description=(
                "Create an event in the user's Google Calendar. Give start as "
                "a local ISO 8601 datetime (YYYY-MM-DDTHH:MM) in the user's "
                "own timezone — never a UTC instant and never an offset. "
                "Work the date out from the current date given in your "
                "instructions and state the weekday you intend, so a wrong "
                "date is caught before it is written."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Event title, e.g. 'Dentist appointment'.",
                    },
                    "start": {
                        "type": "string",
                        "description": "Local start, ISO 8601: 2026-09-02T15:00",
                    },
                    "end": {
                        "type": "string",
                        "description": (
                            "Local end, ISO 8601. Omit to use duration_minutes."
                        ),
                    },
                    "duration_minutes": {
                        "type": "integer",
                        "description": "Length when end is omitted. Default 60.",
                    },
                    "description": {"type": "string"},
                    "location": {"type": "string"},
                },
                "required": ["summary", "start"],
            },
            category="calendar",
            # Confirmed on the next turn, not by a callback nobody supplies:
            # see security/confirmations.py. Writing to a real calendar is
            # worth one "yes", and the fingerprint covers the arguments, so
            # agreeing to one event never authorises a different one.
            requires_confirmation=True,
            timeout_seconds=45.0,
        )

    def _fail(self, message: str) -> ToolResult:
        return ToolResult(
            tool_name=self.tool_id, content=message, success=False
        )

    def _get_connector(self) -> Any:
        if self._connector is not None:
            return self._connector
        from openjarvis.connectors.gcalendar import GCalendarConnector

        return GCalendarConnector()

    def execute(self, **params: Any) -> ToolResult:
        summary = str(params.get("summary", "") or "").strip()
        if not summary:
            return self._fail("An event needs a title.")

        start = _parse_local(str(params.get("start", "") or ""))
        if start is None:
            return self._fail(
                "start must be a local ISO 8601 datetime, e.g. 2026-09-02T15:00."
            )

        raw_end = str(params.get("end", "") or "").strip()
        if raw_end:
            end = _parse_local(raw_end)
            if end is None:
                return self._fail(
                    "end must be a local ISO 8601 datetime, e.g. 2026-09-02T16:00."
                )
        else:
            try:
                minutes = int(
                    params.get("duration_minutes") or _DEFAULT_DURATION_MINUTES
                )
            except (TypeError, ValueError):
                minutes = _DEFAULT_DURATION_MINUTES
            if minutes <= 0 or minutes > _MAX_DURATION_MINUTES:
                return self._fail(
                    f"duration_minutes must be between 1 and {_MAX_DURATION_MINUTES}."
                )
            end = start + timedelta(minutes=minutes)

        if end <= start:
            return self._fail("The event ends before it starts; check the times.")

        if start < datetime.now() - _PAST_TOLERANCE:
            return self._fail(
                f"Refusing to create '{summary}' at "
                f"{start.strftime('%A, %d %B %Y at %H:%M')} — that is in the "
                "past, which usually means the year or date is wrong. Confirm "
                "the intended date and try again."
            )

        connector = self._get_connector()
        if not connector.is_connected():
            return self._fail(
                "Google Calendar is not connected. Run: jarvis connect gcalendar"
            )

        timezone = self._timezone or _configured_timezone()
        try:
            created = connector.create_event(
                summary=summary,
                # Seconds, not minutes: Google wants RFC3339 and rejects
                # "2026-09-02T12:00" with a bare 400 Bad Request and no
                # explanation. Adding ":00" is the entire difference.
                start=start.isoformat(timespec="seconds"),
                end=end.isoformat(timespec="seconds"),
                timezone=timezone,
                description=str(params.get("description", "") or ""),
                location=str(params.get("location", "") or ""),
                calendar_id="primary",
            )
        except Exception as exc:
            return self._fail(f"Could not create the event: {exc}")

        when = start.strftime("%A, %d %B %Y at %H:%M")
        return ToolResult(
            tool_name=self.tool_id,
            content=(
                f"Created '{summary}' on {when} ({timezone}), running "
                f"{int((end - start).total_seconds() // 60)} minutes."
            ),
            success=True,
            metadata={
                "event_id": (created or {}).get("id", ""),
                "html_link": (created or {}).get("htmlLink", ""),
                "start": start.isoformat(timespec="seconds"),
                "end": end.isoformat(timespec="seconds"),
                "timezone": timezone,
            },
        )
