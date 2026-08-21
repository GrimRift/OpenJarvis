"""Class schedule tool — deterministically check for classes starting soon.

Reads the "## Subjects" markdown table from the user's class schedule note
and computes, in plain Python datetime arithmetic (not LLM reasoning), which
classes start within a lookahead window. Deliberately does not send any
notification itself — it only returns structured data; the caller (an agent
prompt, wired via ``notify_windows``) decides what to do with it. Keeping the
side effect isolated to one tool keeps this one a pure, easily-testable
function of (schedule file, now) -> upcoming classes.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from datetime import time as dt_time
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.core.paths import get_data_dir
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_DEFAULT_SCHEDULE_PATH = r"C:\AI\Sage-Vault\Class Schedule.md"
_TIME_RANGE_SEP = re.compile(r"[\u2013\u2014-]")  # en dash, em dash, hyphen


def _default_schedule_path() -> str:
    return os.environ.get("OPENJARVIS_CLASS_SCHEDULE_PATH", _DEFAULT_SCHEDULE_PATH)


def _default_state_path() -> Path:
    return get_data_dir() / "class_schedule_notify_state.json"


def _parse_clock_time(raw: str) -> dt_time:
    return datetime.strptime(raw.strip().upper(), "%I:%M%p").time()


def _row_start_time(row: Dict[str, str]) -> Optional[dt_time]:
    parts = _TIME_RANGE_SEP.split(row.get("Time", ""), maxsplit=1)
    if len(parts) != 2:
        return None
    try:
        return _parse_clock_time(parts[0])
    except ValueError:
        return None


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


@ToolRegistry.register("check_class_schedule")
class CheckClassScheduleTool(BaseTool):
    """Check the user's class schedule note for classes starting soon."""

    tool_id = "check_class_schedule"

    def __init__(
        self,
        schedule_path: Optional[str] = None,
        state_path: Optional[Path] = None,
    ) -> None:
        self._schedule_path = Path(schedule_path or _default_schedule_path())
        self._state_path = Path(state_path) if state_path else _default_state_path()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="check_class_schedule",
            description=(
                "Check for a class starting imminently, within the next N "
                "minutes (default 15, meant for 'is a class about to start' "
                "reminders). An empty result means nothing is starting in "
                "that narrow window — it does NOT mean there are no classes "
                "later today. For 'what's my schedule today/this week' or "
                "any question not about the next few minutes, do not rely "
                "on this tool alone: pass a much larger lookahead_minutes "
                "(e.g. 1440 for the rest of today) or read/search the "
                "schedule note directly instead. Returns upcoming classes "
                "in metadata.upcoming, deduplicated so the same class "
                "occurrence is only ever reported once per day."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "lookahead_minutes": {
                        "type": "integer",
                        "description": (
                            "How many minutes ahead to look for an upcoming "
                            "class. Use the default (15) only for 'is "
                            "something starting right now' checks. Use a "
                            "much larger value (e.g. 1440) when asked about "
                            "today's full schedule or classes later in the day."
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

        upcoming = self._find_upcoming(rows, now, lookahead_minutes)
        upcoming = self._drop_already_notified(upcoming, now)

        if not upcoming:
            return ToolResult(
                tool_name="check_class_schedule",
                content=(
                    f"No classes starting in the next {lookahead_minutes} "
                    "minutes. This only covers that narrow window — there "
                    "may still be classes later today or on other days; "
                    "re-check with a larger lookahead_minutes or read the "
                    "schedule note directly if asked about those."
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

    @staticmethod
    def _find_upcoming(
        rows: List[Dict[str, str]], now: datetime, lookahead_minutes: int
    ) -> List[Dict[str, Any]]:
        today_name = now.strftime("%A")
        found: List[Dict[str, Any]] = []
        for row in rows:
            if row.get("Day", "").strip() != today_name:
                continue
            start_time = _row_start_time(row)
            if start_time is None:
                continue
            start_dt = datetime.combine(now.date(), start_time)
            minutes_until = (start_dt - now).total_seconds() / 60.0
            if 0 <= minutes_until <= lookahead_minutes:
                found.append(
                    {
                        "subject_code": row.get("Subject Code", ""),
                        "subject_description": row.get("Subject Description", ""),
                        "section": row.get("Section", ""),
                        "room": row.get("Room", ""),
                        "mode": row.get("Mode", ""),
                        "start_time": start_time.strftime("%I:%M%p").lstrip("0"),
                        "minutes_until": round(minutes_until, 1),
                    }
                )
        return found

    @staticmethod
    def _occurrence_key(item: Dict[str, Any], now: datetime) -> str:
        return f"{item['subject_code']}|{now.strftime('%A')}|{item['section']}"

    def _drop_already_notified(
        self, upcoming: List[Dict[str, Any]], now: datetime
    ) -> List[Dict[str, Any]]:
        state = self._load_state()
        today_str = now.date().isoformat()
        if state.get("date") != today_str:
            state = {"date": today_str, "notified": []}

        notified = set(state.get("notified", []))
        fresh: List[Dict[str, Any]] = []
        for item in upcoming:
            key = self._occurrence_key(item, now)
            if key in notified:
                continue
            fresh.append(item)
            notified.add(key)

        if fresh:
            state["notified"] = sorted(notified)
            self._save_state(state)

        return fresh

    def _load_state(self) -> Dict[str, Any]:
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self, state: Dict[str, Any]) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps(state), encoding="utf-8")
        except OSError:
            pass
