"""Class schedule tool — deterministically check for classes starting soon.

Reads the "## Subjects" markdown table from the user's class schedule note
and computes, in plain Python datetime arithmetic (not LLM reasoning), which
classes start within a lookahead window. Deliberately does not send any
notification itself — it only returns structured data; the caller (an agent
prompt, wired via ``notify_windows``) decides what to do with it. Keeping the
side effect isolated to one tool keeps this one a pure, easily-testable
function of (schedule file, now) -> upcoming classes.

That purity was previously untrue: this tool also wrote the "already
notified" state, so *reading* the schedule consumed the day's reminders. The
08:00 "what's coming up today" task summarised the day with a wide lookahead
and silently burned every class on it, and asking "what's my schedule today"
did the same — the 11:00 reminder never fired because the 08:00 summary had
already marked it sent. Once-per-day suppression belongs to whatever actually
delivers a notification, so it now lives in ``notify_class_schedule``.
"""

from __future__ import annotations

import os
import re
from datetime import date as dt_date
from datetime import datetime, timedelta
from datetime import time as dt_time
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_DEFAULT_SCHEDULE_PATH = r"C:\AI\Sage-Vault\Class Schedule.md"
_TIME_RANGE_SEP = re.compile(r"[\u2013\u2014-]")  # en dash, em dash, hyphen


def _default_schedule_path() -> str:
    return os.environ.get("OPENJARVIS_CLASS_SCHEDULE_PATH", _DEFAULT_SCHEDULE_PATH)


def _parse_clock_time(raw: str) -> dt_time:
    return datetime.strptime(raw.strip().upper(), "%I:%M%p").time()


def _row_time_range(row: Dict[str, str]) -> Optional[tuple[dt_time, Optional[dt_time]]]:
    """Parse "11:00AM-01:00PM" into (start, end). End is None if unparseable."""
    parts = _TIME_RANGE_SEP.split(row.get("Time", ""), maxsplit=1)
    if len(parts) != 2:
        return None
    try:
        start = _parse_clock_time(parts[0])
    except ValueError:
        return None
    try:
        end: Optional[dt_time] = _parse_clock_time(parts[1])
    except ValueError:
        end = None
    return start, end


def _row_start_time(row: Dict[str, str]) -> Optional[dt_time]:
    parsed = _row_time_range(row)
    return parsed[0] if parsed else None


def _row_fields(row: Dict[str, str]) -> Dict[str, Any]:
    return {
        "subject_code": row.get("Subject Code", ""),
        "subject_description": row.get("Subject Description", ""),
        "section": row.get("Section", ""),
        "room": row.get("Room", ""),
        "mode": row.get("Mode", ""),
    }


def _parse_subjects_table(text: str) -> List[Dict[str, str]]:
    """Parse the markdown table under a ``## Subjects`` heading into row dicts."""
    lines = text.splitlines()
    heading_idx = next(
        (i for i, line in enumerate(lines) if line.strip().lower() == "## subjects"),
        None,
    )
    if heading_idx is None:
        raise ValueError("No '## Subjects' section found in schedule file.")

    columns: Optional[List[str]] = None
    rows: List[Dict[str, str]] = []
    for line in lines[heading_idx + 1 :]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            if columns is not None:
                break  # table ended
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if columns is None:
            columns = cells
            continue
        if all(c.replace("-", "") == "" for c in cells):
            continue  # header separator row
        rows.append(dict(zip(columns, cells)))

    if columns is None:
        raise ValueError("No table found under '## Subjects'.")
    return rows


_WEEKDAYS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


class UnknownDay(ValueError):
    """The `day` argument named something that is not a day."""


def _resolve_day(now: datetime, day: Optional[str]) -> dt_date:
    """Turn "tomorrow" or "Monday" into the date it means.

    Without this the tool could only ever answer for today, so "what is my
    class for tomorrow" was answered with today's rows -- on Sunday 20
    September that was an empty list, and Sage reported no class when Monday
    had one.

    A weekday name means the NEXT such day, today included: asking on Monday
    for "Monday" means today, not a week out.
    """
    if day is None:
        return now.date()
    text = day.strip().lower()
    if text in ("", "today"):
        return now.date()
    if text == "tomorrow":
        return now.date() + timedelta(days=1)
    if text == "yesterday":
        return now.date() - timedelta(days=1)
    for index, name in enumerate(_WEEKDAYS):
        if text in (name.lower(), name.lower()[:3]):
            ahead = (index - now.weekday()) % 7
            return now.date() + timedelta(days=ahead)
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise UnknownDay(
            f"'{day}' is not a day. Use today, tomorrow, a weekday name, "
            "or YYYY-MM-DD."
        ) from exc


@ToolRegistry.register("check_class_schedule")
class CheckClassScheduleTool(BaseTool):
    """Check the user's class schedule note for classes starting soon."""

    tool_id = "check_class_schedule"

    def __init__(self, schedule_path: Optional[str] = None) -> None:
        self._schedule_path = Path(schedule_path or _default_schedule_path())

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="check_class_schedule",
            description=(
                "The user's class schedule. Two modes, and picking the wrong "
                "one produces a false answer:\n"
                "- full_day=true — EVERY class scheduled today, including "
                "ones already in progress or finished, each marked with a "
                "status. Use this for 'what is my schedule today', 'do I "
                "have class today', 'what classes do I have', and any "
                "question about the day as a whole.\n"
                "- full_day=false (default) — only classes STARTING within "
                "the next lookahead_minutes (default 15). This is for 'is a "
                "class about to start' reminders only. An empty result here "
                "means nothing starts in that narrow window; it does NOT "
                "mean there are no classes today, and must never be reported "
                "as such. A larger lookahead_minutes does not help — classes "
                "that already started are excluded in this mode at any "
                "lookahead.\n"
                "Both modes answer for ONE day, `day`, which defaults to "
                "today. For any question about another day — 'tomorrow', "
                "'Monday', 'this Friday' — pass it; the tool cannot infer "
                "it, and left out it silently answers about today instead.\n"
                "Read-only: calling this never suppresses a later reminder."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "day": {
                        "type": "string",
                        "description": (
                            "Which day to read: 'today' (default), "
                            "'tomorrow', a weekday name such as 'Monday' "
                            "(meaning the next one, today included), or a "
                            "YYYY-MM-DD date."
                        ),
                        "default": "today",
                    },
                    "full_day": {
                        "type": "boolean",
                        "description": (
                            "True for the whole day's schedule, including "
                            "classes already in progress or finished. Use "
                            "this for any question about a day rather than "
                            "about the next few minutes."
                        ),
                        "default": False,
                    },
                    "lookahead_minutes": {
                        "type": "integer",
                        "description": (
                            "Reminder window in minutes, used only when "
                            "full_day is false. Default 15. Raising it does "
                            "not reveal classes that already started."
                        ),
                        "default": 15,
                    },
                },
                "required": [],
            },
            category="scheduling",
            timeout_seconds=10.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        now: datetime = params.pop("now", None) or datetime.now()
        raw_lookahead = params.get("lookahead_minutes")
        lookahead_minutes = int(raw_lookahead) if raw_lookahead is not None else 15

        try:
            text = self._schedule_path.read_text(encoding="utf-8")
        except OSError as exc:
            return ToolResult(
                tool_name="check_class_schedule",
                content=f"Could not read schedule file: {exc}",
                success=False,
            )

        try:
            rows = _parse_subjects_table(text)
        except ValueError as exc:
            return ToolResult(
                tool_name="check_class_schedule",
                content=str(exc),
                success=False,
            )

        try:
            target = _resolve_day(now, params.get("day"))
        except UnknownDay as exc:
            return ToolResult(
                tool_name="check_class_schedule",
                content=str(exc),
                success=False,
            )

        if params.get("full_day"):
            skip = params.get("skip_starting_within")
            return self._day_view(
                rows, now, int(skip) if skip is not None else None, target
            )

        upcoming = self._find_upcoming(rows, now, lookahead_minutes, target)

        if not upcoming:
            return ToolResult(
                tool_name="check_class_schedule",
                content=(
                    f"No classes starting in the next {lookahead_minutes} "
                    "minutes. This only covers that narrow window and says "
                    "NOTHING about today's schedule — classes already in "
                    "progress or finished are excluded, so do not report "
                    "this as 'no classes today'. Call again with "
                    "full_day=true for that question."
                ),
                success=True,
                metadata={"upcoming": []},
            )

        summary = "; ".join(
            f"{c['subject_description']} ({c['subject_code']}) at {c['start_time']} "
            f"in {c['room']} ({c['mode']})"
            for c in upcoming
        )
        return ToolResult(
            tool_name="check_class_schedule",
            content=f"Upcoming: {summary}",
            success=True,
            metadata={"upcoming": upcoming},
        )

    def _day_view(
        self,
        rows: List[Dict[str, str]],
        now: datetime,
        skip_starting_within: Optional[int] = None,
        target: Optional[dt_date] = None,
    ) -> ToolResult:
        """Today's classes; optionally without the ones about to start.

        The morning greeting reads this. A class starting within the
        reminder's lead is the reminder's to announce -- on 17 September the
        greeting and the 15-minute reminder said the same 9:40 class in the
        same breath -- so the greeting leaves such a class out and is told
        that it did.
        """
        day = target or now.date()
        classes = self._find_today(rows, now, day)
        day_name = day.strftime("%A")
        when = "today" if day == now.date() else f"on {day_name}"
        left_out = 0
        if skip_starting_within is not None:
            kept = []
            for c in classes:
                soon = 0 <= c["minutes_until"] <= skip_starting_within
                if soon:
                    left_out += 1
                else:
                    kept.append(c)
            classes = kept
        note = (
            f"\n({left_out} class(es) starting within {skip_starting_within} "
            "minutes left out: a reminder announces those, do not mention them.)"
            if left_out
            else ""
        )
        if not classes:
            return ToolResult(
                tool_name="check_class_schedule",
                content=(
                    f"No other classes scheduled {when} "
                    f"({day_name}, {day.isoformat()}).{note}"
                    if left_out
                    else f"No classes scheduled {when} "
                    f"({day_name}, {day.isoformat()})."
                ),
                success=True,
                metadata={
                    "classes": [],
                    "upcoming": [],
                    "day": day_name,
                    "date": day.isoformat(),
                    "left_out": left_out,
                },
            )

        label = {
            "upcoming": "later today",
            "in_progress": "in progress now",
            "ended": "already finished",
        }
        lines = [
            f"- {c['subject_description']} ({c['subject_code']}) "
            f"{c['start_time']}"
            f"{'–' + c['end_time'] if c['end_time'] else ''} "
            f"in {c['room']} ({c['mode']}) — {label[c['status']]}"
            for c in sorted(classes, key=lambda c: c["start_time"])
        ]
        return ToolResult(
            tool_name="check_class_schedule",
            content=(
                f"{len(classes)} class(es) scheduled {when} "
                f"({day_name}, {day.isoformat()}):\n"
                + "\n".join(lines)
                + note
            ),
            success=True,
            metadata={
                "classes": classes,
                "upcoming": [c for c in classes if c["status"] == "upcoming"],
                "day": day_name,
                "date": day.isoformat(),
                "left_out": left_out,
            },
        )

    @staticmethod
    def _find_upcoming(
        rows: List[Dict[str, str]],
        now: datetime,
        lookahead_minutes: int,
        target: Optional[dt_date] = None,
    ) -> List[Dict[str, Any]]:
        found: List[Dict[str, Any]] = []
        for item in CheckClassScheduleTool._find_today(rows, now, target):
            if 0 <= item["minutes_until"] <= lookahead_minutes:
                found.append({k: v for k, v in item.items() if k != "status"})
        return found

    @staticmethod
    def _find_today(
        rows: List[Dict[str, str]],
        now: datetime,
        target: Optional[dt_date] = None,
    ) -> List[Dict[str, Any]]:
        """Every class scheduled on `target` (default today), in start order.

        A class that has already begun is still on today's schedule. Filtering
        it out is right for "is something starting soon" and wrong for "what do
        I have today" -- answering the second with the first is how Sage came
        to report "no classes scheduled for today" at 17:05 with three on the
        note and one in session.
        """
        day = target or now.date()
        today_name = day.strftime("%A")
        found: List[Dict[str, Any]] = []
        for row in rows:
            if row.get("Day", "").strip() != today_name:
                continue
            parsed = _row_time_range(row)
            if parsed is None:
                continue
            start_time, end_time = parsed
            start_dt = datetime.combine(day, start_time)
            minutes_until = (start_dt - now).total_seconds() / 60.0
            end_dt = datetime.combine(day, end_time) if end_time else None
            if minutes_until >= 0:
                status = "upcoming"
            elif end_dt is None or now < end_dt:
                status = "in_progress"
            else:
                status = "ended"
            item = _row_fields(row)
            item.update(
                {
                    "start_time": start_time.strftime("%I:%M%p").lstrip("0"),
                    "end_time": (
                        end_time.strftime("%I:%M%p").lstrip("0") if end_time else ""
                    ),
                    "minutes_until": round(minutes_until, 1),
                    "status": status,
                }
            )
            found.append(item)
        found.sort(key=lambda c: c["minutes_until"])
        return found
