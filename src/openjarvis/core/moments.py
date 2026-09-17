"""Moments: the occasions on which Sage speaks first (M36 phase 3).

Three kinds, each its own switch beneath the presence master switch:

- **Greeting** -- the first time the user is at the desk today, named for
  the time of day: good morning after sleep, good afternoon when Sage is
  first switched on at one. Sage is not on around the clock, so "first
  appearance" has to mean the first Sage sees, whenever that is.
- **Welcome back** -- the user returns after an absence of at least an hour,
  whether Sage watched them leave or was off in between.
- **Told on request** -- the user said "tell me when X" and X happened: a
  time arrived, or a scheduled job finished.

Four guards apply to every moment and none is optional: the presence gate
(never while ``away``; talking to an empty room is the failure this
milestone exists to avoid), a daily cap per kind, quiet hours, and "not now",
which silences everything for the rest of the local day.

The decision is a pure function over the presence snapshot, the settings and
this module's own small state, so the whole policy can be tested with a fake
clock. Delivery is server-side and out loud only: the text is written by the
cloud model from real context (yesterday's episode, today's calendar, what
finished while the user was away), synthesised with the configured voice and
played through the speakers. A model failure never silences a moment -- a
plain fallback line is spoken instead -- and every moment is recorded so what
Sage said unprompted can be read afterwards, not only heard once.
"""

from __future__ import annotations

import json
import logging
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from openjarvis.core.config import DEFAULT_CONFIG_DIR
from openjarvis.core.presence import (
    STATE_PRESENT,
    PresenceSettings,
    PresenceSnapshot,
    load_settings,
)

logger = logging.getLogger(__name__)

MOMENT_GREETING = "greeting"
MOMENT_WELCOME_BACK = "welcome_back"
MOMENT_TOLD = "told"
# Initiative (M37): Sage starting a conversation of its own accord.
MOMENT_INITIATIVE = "initiative"
KINDS = (MOMENT_GREETING, MOMENT_WELCOME_BACK, MOMENT_TOLD, MOMENT_INITIATIVE)

# One greeting a day; welcome back a few times, because a day can hold
# several real absences; told-on-request as often as it was asked for.
DAILY_CAPS = {
    MOMENT_GREETING: 1,
    MOMENT_WELCOME_BACK: 3,
    MOMENT_TOLD: 20,
    MOMENT_INITIATIVE: 40,
}

# What each initiative mode may talk about. Gentle stays with today's work.
INITIATIVE_CATEGORIES = {
    "gentle": ("contextual", "useful"),
    "curious": ("contextual", "useful", "curious", "interesting"),
    "social": ("contextual", "useful", "curious", "interesting", "reflective"),
}
# The writer's way of saying that silence is better.
SKIP = "SKIP"

# Follow-up: one short line if a prompt goes unanswered this long, then let
# it go. Fixed and rotating -- instant, free, never wrong.
FOLLOW_UP_AFTER_SECONDS = 60
FOLLOW_UP_LINES = (
    "No rush, sir.",
    "Only if you feel like it, sir.",
    "I'll leave it there, sir.",
    "Whenever suits you, sir.",
)
# Two unanswered in a row: the cooldown doubles for this long. Being
# ignored is a signal.
BACKOFF_SECONDS = 3600
# A line held because the user started talking is said once both sides
# have been quiet this long, and dropped if it waits longer than this.
HELD_LINE_LULL_SECONDS = 20
HELD_LINE_STALE_SECONDS = 600
# A watch that could not be delivered for this long (asleep, away, snoozed)
# is stale: "your class started yesterday" helps nobody.
WATCH_STALE_SECONDS = 12 * 3600
HISTORY_LIMIT = 50

_STATE_FILE = "moments.json"


# -- State -------------------------------------------------------------------


@dataclass
class Watch:
    id: str
    what: str
    created_at: float
    # Exactly one of these is set: a time watch or a job watch.
    due_at: Optional[float] = None
    task_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MomentRecord:
    at: float
    kind: str
    text: str
    spoken: bool
    detail: str = ""
    # Wall-clock moment the audio finished, so the browser can open the
    # microphone for a reply right after, not a poll later.
    ended_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MomentsState:
    # The local day "not now" applies to; empty when not snoozed.
    snoozed_day: str = ""
    # Timed quiet ("be quiet for 30 minutes"): epoch until which nothing
    # unprompted is said. Reminders the user scheduled are not moments and
    # are unaffected.
    snoozed_until: Optional[float] = None
    # When Sage last said anything unprompted, of any kind: a greeting resets
    # the initiative cooldown too, so two things are not said minutes apart.
    last_unprompted_at: Optional[float] = None
    # When initiative last spoke or, at half weight, declined.
    last_initiative_at: Optional[float] = None
    # An initiative prompt awaiting an answer: when it was spoken, and
    # whether the single follow-up has been said.
    pending_prompt_at: Optional[float] = None
    pending_followed_up: bool = False
    # Prompts in a row that went unanswered, and the back-off it earned.
    unanswered_streak: int = 0
    backoff_until: Optional[float] = None
    # A line written while the user started talking: said after their
    # exchange, once both sides have been quiet a short while, unless it
    # has gone stale.
    held_line: str = ""
    held_at: Optional[float] = None
    # Last time a poll saw the user present. Persisted so the gap that means
    # "slept" survives a server restart.
    last_present_at: Optional[float] = None
    # End time of the last absence a moment was spoken for, so one return
    # earns one greeting however many polls follow it.
    handled_absence_end: Optional[float] = None
    # kind -> timestamps of moments spoken today (older ones are pruned).
    fired: Dict[str, List[float]] = field(default_factory=dict)
    watches: List[Watch] = field(default_factory=list)
    history: List[MomentRecord] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snoozed_day": self.snoozed_day,
            "snoozed_until": self.snoozed_until,
            "last_unprompted_at": self.last_unprompted_at,
            "last_initiative_at": self.last_initiative_at,
            "pending_prompt_at": self.pending_prompt_at,
            "pending_followed_up": self.pending_followed_up,
            "unanswered_streak": self.unanswered_streak,
            "backoff_until": self.backoff_until,
            "held_line": self.held_line,
            "held_at": self.held_at,
            "last_present_at": self.last_present_at,
            "handled_absence_end": self.handled_absence_end,
            "fired": self.fired,
            "watches": [w.to_dict() for w in self.watches],
            "history": [h.to_dict() for h in self.history],
        }


def state_path(config_dir: Optional[Path] = None) -> Path:
    return (config_dir or DEFAULT_CONFIG_DIR) / _STATE_FILE


def load_state(config_dir: Optional[Path] = None) -> MomentsState:
    try:
        raw = json.loads(state_path(config_dir).read_text(encoding="utf-8"))
    except Exception:
        return MomentsState()
    if not isinstance(raw, dict):
        return MomentsState()
    state = MomentsState()
    if isinstance(raw.get("snoozed_day"), str):
        state.snoozed_day = raw["snoozed_day"]
    for key in (
        "last_present_at",
        "handled_absence_end",
        "snoozed_until",
        "last_unprompted_at",
        "last_initiative_at",
        "pending_prompt_at",
        "backoff_until",
        "held_at",
    ):
        value = raw.get(key)
        if isinstance(value, (int, float)):
            setattr(state, key, float(value))
    if isinstance(raw.get("held_line"), str):
        state.held_line = raw["held_line"]
    if isinstance(raw.get("pending_followed_up"), bool):
        state.pending_followed_up = raw["pending_followed_up"]
    if isinstance(raw.get("unanswered_streak"), int):
        state.unanswered_streak = raw["unanswered_streak"]
    fired = raw.get("fired")
    if isinstance(fired, dict):
        state.fired = {
            str(k): [float(t) for t in v if isinstance(t, (int, float))]
            for k, v in fired.items()
            if isinstance(v, list)
        }
    for item in raw.get("watches") or []:
        try:
            state.watches.append(
                Watch(
                    id=str(item["id"]),
                    what=str(item["what"]),
                    created_at=float(item["created_at"]),
                    due_at=item.get("due_at"),
                    task_id=item.get("task_id"),
                )
            )
        except Exception:
            continue
    for item in raw.get("history") or []:
        try:
            state.history.append(
                MomentRecord(
                    at=float(item["at"]),
                    kind=str(item["kind"]),
                    text=str(item["text"]),
                    spoken=bool(item.get("spoken")),
                    detail=str(item.get("detail") or ""),
                    ended_at=item.get("ended_at"),
                )
            )
        except Exception:
            continue
    return state


def save_state(state: MomentsState, config_dir: Optional[Path] = None) -> None:
    path = state_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")


# -- Time --------------------------------------------------------------------


def configured_timezone() -> str:
    """The timezone the user lives in, from the one config field maintained."""
    try:
        from openjarvis.core.config import load_config

        cfg = load_config()
        return (
            getattr(cfg.proactive, "timezone", "")
            or getattr(cfg.digest, "timezone", "")
            or "UTC"
        )
    except Exception:
        return "UTC"


def to_local(ts: float, timezone_name: str) -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.fromtimestamp(ts, ZoneInfo(timezone_name))
    except Exception:
        return datetime.fromtimestamp(ts)


def local_to_timestamp(text: str, timezone_name: str) -> Optional[float]:
    """Parse an ISO local datetime ('2026-09-15T15:00') into an epoch."""
    try:
        naive = datetime.fromisoformat(text.strip())
    except Exception:
        return None
    if naive.tzinfo is not None:
        return naive.timestamp()
    try:
        from zoneinfo import ZoneInfo

        return naive.replace(tzinfo=ZoneInfo(timezone_name)).timestamp()
    except Exception:
        return naive.timestamp()


def in_quiet_hours(hour: int, start: int, end: int) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def time_of_day(hour: int) -> str:
    if hour < 12:
        return "morning"
    if hour < 18:
        return "afternoon"
    return "evening"


def describe_duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{max(1, minutes)} minute" + ("s" if minutes != 1 else "")
    hours = minutes // 60
    rest = minutes % 60
    text = f"{hours} hour" + ("s" if hours != 1 else "")
    if rest and hours < 6:
        text += f" {rest} min"
    return text


# -- Decision ----------------------------------------------------------------


@dataclass
class Decision:
    kinds: List[str] = field(default_factory=list)
    watches: List[Watch] = field(default_factory=list)
    stale_watches: List[Watch] = field(default_factory=list)
    # The absence (end time) a greeting is spoken for, if any.
    absence_end: Optional[float] = None
    absence_seconds: Optional[float] = None
    reason: str = ""
    # Why initiative held back this tick, for the record and the Health page.
    initiative_reason: str = ""


def fired_today(state: MomentsState, kind: str, local: datetime) -> int:
    """How often *kind* was spoken on the local calendar day of *local*.

    A calendar day, not the last 24 hours: a greeting at 22:40 must not use
    up the next morning's.
    """
    day = local.date()
    return sum(
        1
        for t in state.fired.get(kind, [])
        if datetime.fromtimestamp(t, local.tzinfo).date() == day
    )


def decide(
    snapshot: PresenceSnapshot,
    settings: PresenceSettings,
    state: MomentsState,
    now: float,
    local: datetime,
    job_finished: Callable[[Watch], Optional[str]],
    *,
    activity: Optional[Any] = None,
    busy: Sequence[str] = (),
) -> Decision:
    """Pure: what, if anything, should be said right now.

    *job_finished* answers whether a job watch's task has completed since the
    watch was set; it is injected so the policy needs no scheduler.
    *activity* and *busy* (see ``core/activity`` and ``core/busy``) gate
    initiative only; the fixed moments never depended on them.
    """
    decision = Decision()
    if not settings.enabled or not settings.moments_enabled:
        decision.reason = "moments switched off"
        return decision
    if snapshot.state != STATE_PRESENT:
        decision.reason = f"presence is {snapshot.state}"
        return decision
    today = local.date().isoformat()
    if state.snoozed_day == today:
        decision.reason = "not now, until tomorrow"
        return decision
    if state.snoozed_until is not None and state.snoozed_until > now:
        decision.reason = f"quiet for {describe_duration(state.snoozed_until - now)}"
        return decision
    if in_quiet_hours(
        local.hour, settings.quiet_hours_start_local, settings.quiet_hours_end_local
    ):
        decision.reason = "quiet hours"
        return decision

    # A return: either the monitor saw an absence end that no moment has
    # answered yet, or this module's own readings have a gap -- Sage was off,
    # or the night passed. The user's choice: a gap counts, because Sage is
    # shut down for the night and for outings, and an absence it did not
    # watch is still an absence.
    absence_end = snapshot.last_absence_ended_at
    absence_seconds = None
    if (
        absence_end is not None
        and snapshot.last_absence_started_at is not None
        and absence_end != state.handled_absence_end
    ):
        # The monitor calls the desk empty only after the idle threshold
        # has passed, so the user left that much earlier than it noticed.
        absence_seconds = (
            absence_end - snapshot.last_absence_started_at + snapshot.threshold_seconds
        )
    observed_return = (
        absence_seconds is not None
        and absence_seconds >= settings.welcome_back_after_seconds
    )
    gap = None if state.last_present_at is None else now - state.last_present_at
    long_gap = gap is not None and gap >= settings.welcome_back_after_seconds
    # No reading ever is a first appearance (greeting), not a return.
    returned = observed_return or long_gap or gap is None
    away_for = absence_seconds if observed_return else gap

    if (
        settings.greeting_enabled
        and returned
        and fired_today(state, MOMENT_GREETING, local) < DAILY_CAPS[MOMENT_GREETING]
    ):
        decision.kinds.append(MOMENT_GREETING)
        decision.absence_end = absence_end if observed_return else None
        decision.absence_seconds = away_for
    elif (
        settings.welcome_back_enabled
        and (observed_return or long_gap)
        and fired_today(state, MOMENT_WELCOME_BACK, local)
        < DAILY_CAPS[MOMENT_WELCOME_BACK]
    ):
        decision.kinds.append(MOMENT_WELCOME_BACK)
        decision.absence_end = absence_end if observed_return else None
        decision.absence_seconds = away_for

    if settings.told_enabled:
        for watch in state.watches:
            due_since: Optional[float] = None
            if watch.due_at is not None and watch.due_at <= now:
                due_since = watch.due_at
            elif watch.task_id and job_finished(watch) is not None:
                due_since = watch.created_at
            if due_since is None:
                continue
            if now - due_since > WATCH_STALE_SECONDS:
                decision.stale_watches.append(watch)
            elif (
                fired_today(state, MOMENT_TOLD, local) + len(decision.watches)
                < DAILY_CAPS[MOMENT_TOLD]
            ):
                decision.watches.append(watch)
    if decision.watches:
        decision.kinds.append(MOMENT_TOLD)

    # Initiative: only when nothing else is being said this tick.
    if not decision.kinds:
        decision.initiative_reason = initiative_holdback(
            settings, state, now, local, activity=activity, busy=busy
        )
        if not decision.initiative_reason:
            decision.kinds.append(MOMENT_INITIATIVE)
    if not decision.kinds:
        decision.reason = decision.initiative_reason or "nothing to say"
    return decision


def follow_up_due(state: MomentsState, activity: Optional[Any], now: float) -> str:
    """Pure: what to do about a prompt awaiting an answer.

    Returns "answered", "follow-up", "give-up" or "" (keep waiting).
    """
    if state.pending_prompt_at is None:
        return ""
    last_user = getattr(activity, "last_user_turn_at", None) if activity else None
    if last_user is not None and last_user > state.pending_prompt_at:
        return "answered"
    waited = now - state.pending_prompt_at
    if not state.pending_followed_up:
        return "follow-up" if waited >= FOLLOW_UP_AFTER_SECONDS else ""
    return "give-up" if waited >= 2 * FOLLOW_UP_AFTER_SECONDS else ""


def initiative_holdback(
    settings: PresenceSettings,
    state: MomentsState,
    now: float,
    local: datetime,
    *,
    activity: Optional[Any] = None,
    busy: Sequence[str] = (),
) -> str:
    """Pure: why initiative should hold back now, or "" if it may consider
    speaking. The timer means "consider", not "speak": the writer may still
    decline once asked."""
    if settings.initiative_mode == "off":
        return "initiative is off"
    if state.pending_prompt_at is not None:
        return "waiting on an answer"
    if state.held_line:
        return "a line is waiting for a lull"
    if busy:
        return "busy: " + "; ".join(busy)
    if fired_today(state, MOMENT_INITIATIVE, local) >= DAILY_CAPS[MOMENT_INITIATIVE]:
        return "daily cap"
    last_user = getattr(activity, "last_user_turn_at", None) if activity else None
    last_reply = getattr(activity, "last_reply_end_at", None) if activity else None
    quiet_since = max(
        [t for t in (last_user, last_reply, state.last_unprompted_at) if t] or [0.0]
    )
    if quiet_since and now - quiet_since < settings.initiative_idle_seconds:
        return "conversation is recent"
    cooldown = settings.initiative_cooldown_seconds
    if state.backoff_until is not None and state.backoff_until > now:
        cooldown *= 2
    if (
        state.last_initiative_at is not None
        and now - state.last_initiative_at < cooldown
    ):
        return (
            "backing off"
            if cooldown > settings.initiative_cooldown_seconds
            else "cooling down"
        )
    hour_ago = now - 3600
    spoken_this_hour = sum(
        1 for t in state.fired.get(MOMENT_INITIATIVE, []) if t > hour_ago
    )
    if spoken_this_hour >= settings.initiative_per_hour:
        return "hourly cap"
    return ""


# -- Context and wording -----------------------------------------------------

SYSTEM_PROMPT = (
    "You are Sage, a personal AI assistant, speaking ALOUD and unprompted to "
    "the user, who has just sat down at their computer. Write exactly what "
    "you will say: one to three short sentences of natural spoken English. "
    "No markdown, no lists, no headings, no emoji. Mention only things that "
    "appear in the context below; if the context is thin, keep it to a warm "
    "greeting. Do not ask questions that need an answer. Do not mention that "
    "you are an AI, that this is unprompted, or how you know these things. "
    "Address the user as the profile says to."
)


def _profile_excerpt(config_dir: Optional[Path], limit: int = 800) -> str:
    try:
        text = ((config_dir or DEFAULT_CONFIG_DIR) / "USER.md").read_text(
            encoding="utf-8"
        )
    except Exception:
        return ""
    return text[:limit].strip()


#: A class starting this soon is the reminder's to announce (it fires at
#: 15 and 5 minutes before), so a greeting composed now leaves it out.
CLASS_REMINDER_LEAD_MINUTES = 20


def _today_classes(now: Optional[float] = None) -> str:
    try:
        from datetime import datetime

        from openjarvis.tools.check_class_schedule import CheckClassScheduleTool

        result = CheckClassScheduleTool().execute(
            full_day=True,
            skip_starting_within=CLASS_REMINDER_LEAD_MINUTES,
            now=datetime.fromtimestamp(now) if now is not None else None,
        )
        return result.content if result.success else ""
    except Exception:
        return ""


def _today_calendar(timezone_name: str, now: float) -> str:
    """Today's Google Calendar events, or nothing if the connector is absent."""
    try:
        from openjarvis.connectors.gcalendar import GCalendarConnector

        connector = GCalendarConnector()
        if not connector.is_connected():
            return ""
        local = to_local(now, timezone_name)
        day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        lines = []
        # The connector formats *since* as a naive UTC stamp.
        since = datetime.fromtimestamp(day_start.timestamp(), timezone.utc).replace(
            tzinfo=None
        )
        for doc in connector.sync(since=since):
            when = doc.timestamp
            try:
                stamp = when.timestamp()
            except Exception:
                continue
            event_local = to_local(stamp, timezone_name)
            if event_local.date() != local.date():
                continue
            lines.append(f"{event_local.strftime('%H:%M')} {doc.title or '(untitled)'}")
            if len(lines) >= 8:
                break
        return "\n".join(lines)
    except Exception as exc:
        logger.debug("Calendar unavailable for moment: %s", exc)
        return ""


# Sage's own recurring jobs. Their runs are news only when they actually
# told the user something: thirty "nothing upcoming" class checks handed to
# the model came back as "I checked your class schedule" on a return at
# ten at night.
HOUSEKEEPING_AGENTS = frozenset(
    {"class_notifier", "episode_writer", "proactive", "morning_digest"}
)
NOTIFYING_TOOLS = frozenset({"notify_windows", "notify_class_schedule", "channel_send"})


def _is_housekeeping(task: Any) -> bool:
    meta = task.metadata or {}
    return (
        task.agent in HOUSEKEEPING_AGENTS
        or bool(meta.get("managed_by"))
        or bool(meta.get("openjarvis_task_key"))
    )


def _run_summary(run: Dict[str, Any]) -> tuple[str, bool]:
    """(what the run said, whether it notified the user)."""
    raw = str(run.get("result") or run.get("error") or "").strip()
    notified = False
    text = raw
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        text = str(parsed.get("content") or raw)
        for tool in parsed.get("tool_results") or []:
            if (
                isinstance(tool, dict)
                and tool.get("tool_name") in NOTIFYING_TOOLS
                and tool.get("success", True)
            ):
                notified = True
    return text.replace("\n", " ")[:200], notified


def _finished_jobs(scheduler: Any, since: float, limit: int = 5) -> List[str]:
    """Scheduled runs that finished after *since* and are news to the user.

    The user's own tasks always are. Housekeeping runs only when they sent a
    notification, so a class alert counts and a "nothing upcoming" does not.
    """
    if scheduler is None or since is None:
        return []
    lines: List[str] = []
    try:
        store = getattr(scheduler, "_store", None)
        for task in scheduler.list_tasks():
            housekeeping = _is_housekeeping(task)
            for run in store.get_run_logs(task.id, limit=3) if store else []:
                finished = _iso_to_ts(run.get("finished_at"))
                if finished is None or finished <= since:
                    continue
                result, notified = _run_summary(run)
                if housekeeping and not notified:
                    continue
                outcome = "finished" if run.get("success") else "failed"
                lines.append(f"{outcome}: {task.prompt[:80]} -> {result}")
                if len(lines) >= limit:
                    return lines
    except Exception as exc:
        logger.debug("Could not read finished jobs: %s", exc)
    return lines


def _iso_to_ts(value: Any) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except Exception:
        return None


def _job_result(scheduler: Any, watch: Watch) -> Optional[str]:
    """The result of a job watch's task if it ran after the watch was set."""
    if scheduler is None or not watch.task_id:
        return None
    try:
        store = getattr(scheduler, "_store", None)
        if store is None:
            return None
        for run in store.get_run_logs(watch.task_id, limit=3):
            finished = _iso_to_ts(run.get("finished_at"))
            if finished is not None and finished >= watch.created_at:
                text = str(run.get("result") or run.get("error") or "").strip()
                return text.replace("\n", " ")[:300] or "done"
    except Exception as exc:
        logger.debug("Could not read job watch %s: %s", watch.id, exc)
    return None


def build_context(
    kind: str,
    decision: Decision,
    *,
    now: float,
    timezone_name: str,
    config_dir: Optional[Path],
    scheduler: Any,
    state: MomentsState,
) -> Dict[str, str]:
    local = to_local(now, timezone_name)
    context: Dict[str, str] = {
        "now": local.strftime("%A %d %B %Y, %H:%M"),
        "time_of_day": time_of_day(local.hour),
        "profile": _profile_excerpt(config_dir),
        "recent_openings": recent_openings(state.history, kind),
    }
    if decision.absence_seconds:
        context["away_for"] = describe_duration(decision.absence_seconds)
    if kind == MOMENT_GREETING:
        try:
            from openjarvis.memory.episodes import format_recent_days, recent_episodes

            context["recent_days"] = format_recent_days(
                recent_episodes(2, today=local.date(), config_dir=config_dir),
                today=local.date(),
            )
        except Exception:
            context["recent_days"] = ""
        context["classes_today"] = _today_classes(now)
        context["calendar_today"] = _today_calendar(timezone_name, now)
    if kind in (MOMENT_GREETING, MOMENT_WELCOME_BACK):
        since = decision.absence_end
        if since is not None and decision.absence_seconds:
            since = since - decision.absence_seconds
        elif state.last_present_at is not None:
            since = state.last_present_at
        if since is not None:
            context["finished_while_away"] = "\n".join(_finished_jobs(scheduler, since))
    if kind == MOMENT_INITIATIVE:
        context.update(initiative_context(now, timezone_name, config_dir, state))
    if kind == MOMENT_TOLD:
        lines = []
        for watch in decision.watches:
            if watch.task_id:
                lines.append(
                    f"The user asked to be told when: {watch.what}. It has finished. "
                    f"Result: {_job_result(scheduler, watch) or 'done'}"
                )
            else:
                late = now - (watch.due_at or now)
                when = (
                    f" (this is {describe_duration(late)} late; the user was away)"
                    if late > 120
                    else ""
                )
                lines.append(
                    f"The user asked to be told when: {watch.what}. It is time{when}."
                )
        context["told"] = "\n".join(lines)
    return {k: v for k, v in context.items() if v}


# A few ways to say each thing, so the fallback does not sound like a
# recording. {tod} is the time of day, {away} the absence. The line used
# last time is skipped, the way the wake-word clips rotate.
FALLBACK_LINES = {
    MOMENT_GREETING: [
        "Good {tod}, sir.",
        "Good {tod}, sir. I'm here when you need me.",
        "{Tod} greetings, sir.",
        "Good {tod}. Ready when you are, sir.",
    ],
    MOMENT_WELCOME_BACK: [
        "Welcome back, sir. You were away {away}.",
        "Good to have you back, sir. It's been {away}.",
        "You're back, sir. {Away} away.",
        "Welcome back, sir. That was {away}.",
    ],
}
_NO_AWAY = {
    MOMENT_WELCOME_BACK: ["Welcome back, sir.", "Good to have you back, sir."],
}


def recent_openings(history: Sequence[MomentRecord], kind: str, count: int = 3) -> str:
    """The first few words of the last *count* spoken moments of *kind*."""
    spoken = [h for h in history if h.kind == kind and h.spoken]
    lines = []
    for record in spoken[-count:]:
        words = record.text.split()
        lines.append("- " + " ".join(words[:8]) + ("..." if len(words) > 8 else ""))
    return "\n".join(lines)


def last_fallback(history: Sequence[MomentRecord], kind: str) -> Optional[str]:
    """The fallback line said last for *kind*, if the last one was a fallback."""
    for record in reversed(history):
        if record.kind == kind:
            return (
                record.text if record.detail.startswith("model unavailable") else None
            )
    return None


def _todays_turns(local_now: datetime, limit: int = 12) -> str:
    """The day's conversation so far, most recent last, for the writer."""
    try:
        from openjarvis.memory.episodes import collect_turns

        turns = collect_turns(local_now.date())[-limit:]
        lines = []
        for turn in turns:
            when = to_local(turn.at, str(local_now.tzinfo)).strftime("%H:%M")
            lines.append(f"[{when}] User: {turn.query[:200]}")
            lines.append(f"[{when}] Sage: {turn.result[:200]}")
        return "\n".join(lines)
    except Exception:
        return ""


def _facts_for_initiative(excluded: Sequence[str], limit: int = 60) -> str:
    """Long-term facts the writer may see, minus the excluded ones."""
    try:
        from openjarvis.memory.store import LocalFactStore

        facts = [f for f in LocalFactStore().list() if f.trusted_for_recall]
    except Exception:
        return ""
    keep = []
    for fact in facts:
        if getattr(fact, "private", False):
            # Marked on the Memory page: never in anything Sage says first.
            continue
        text = fact.text.strip()
        if any(word and word.lower() in text.lower() for word in excluded):
            continue
        keep.append(text)
    return "\n".join(f"- {t}" for t in keep[-limit:])


def initiative_context(
    now: float,
    timezone_name: str,
    config_dir: Optional[Path],
    state: MomentsState,
) -> Dict[str, str]:
    settings = load_settings(config_dir)
    local = to_local(now, timezone_name)
    context: Dict[str, str] = {
        "mode": settings.initiative_mode,
        "allowed_categories": ", ".join(
            INITIATIVE_CATEGORIES.get(settings.initiative_mode, ())
        ),
        "conversation_today": _todays_turns(local),
        "long_term_facts": _facts_for_initiative(settings.initiative_excluded_facts),
    }
    try:
        from openjarvis.memory.episodes import format_recent_days, recent_episodes

        context["recent_days"] = format_recent_days(
            recent_episodes(2, today=local.date(), config_dir=config_dir),
            today=local.date(),
        )
    except Exception:
        context["recent_days"] = ""
    if state.last_present_at and state.handled_absence_end:
        at_desk = now - max(state.handled_absence_end, 0)
        if at_desk > 0:
            context["at_desk_for"] = describe_duration(at_desk)
    # The last few days of initiatives, with their categories, so a theme
    # is not repeated across days and the category mix varies.
    two_days_ago = now - 2 * 86400
    recent = [
        h
        for h in state.history
        if h.kind == MOMENT_INITIATIVE and h.spoken and h.at >= two_days_ago
    ][-8:]
    context["recent_initiatives"] = "\n".join(
        f"- [{_category_of(h) or '?'}] {h.text}" for h in recent
    )
    context["fields"] = (
        "civil engineering (the user's degree: concrete, structures, "
        "construction methods, project management); AI and Sage itself "
        "(voice pipelines, models, what the user builds here); and whatever "
        "the recent conversations were about"
    )
    if settings.initiative_mode == "social":
        context["memory_rule"] = (
            "Social: you may open any subject from long-term memory that is "
            "not on the excluded list, with tact."
        )
    else:
        context["memory_rule"] = (
            "Never open a personal subject from long-term memory unless the "
            "user raised it themselves in the last few days."
        )
    return {k: v for k, v in context.items() if v}


def _category_of(record: MomentRecord) -> str:
    for part in record.detail.split(";"):
        part = part.strip()
        if part.startswith("category="):
            return part[len("category=") :]
    return ""


def parse_initiative_reply(raw: str) -> tuple[str, str]:
    """(category, line) from the writer's reply; ("", "") for a SKIP.

    The writer is asked to open with the category in brackets. A reply
    without one is still a line -- untagged rather than dropped.
    """
    text = (raw or "").strip()
    if not text or text.strip(" .!\"'").upper() == SKIP:
        return "", ""
    import re

    match = re.match(
        r"^\s*\[?\s*(contextual|useful|curious|interesting|reflective)\s*\]?\s*[:\-]?\s*(.+)$",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        return match.group(1).lower(), match.group(2).strip()
    return "", text


INITIATIVE_SYSTEM_PROMPT = (
    "You are Sage, a personal AI assistant sharing a room with the user, who "
    "is at their computer and has not spoken to you for a while. You may "
    "start a short conversation: ask one question, or offer one useful "
    "nudge, fact or observation -- one to two sentences, spoken aloud, no "
    "markdown, no lists, at most one question. This is a lull the user has "
    "chosen to let you into: in the normal case, say something. Reply with "
    "the single word SKIP only for a real reason -- today's conversation is "
    "about something absorbing and a remark would break it, or everything "
    "you could say would repeat an earlier initiative or touch something "
    "personal. A light, specific question about what they were working on "
    "today is always acceptable. Begin your reply with the category in "
    "square brackets, then the line: for example "
    "[contextual] Did the control-group design settle, sir?\n\n"
    "Rules. Stay within the allowed categories for the mode: contextual "
    "(something from today's conversation), useful (a break, water, the "
    "time, something coming up), curious (a question in the user's field), "
    "interesting (a fact in or near their field), reflective (an "
    "observation about how they are working). Curious and interesting draw "
    "on the fields given in the context. Do not repeat a theme from your "
    "recent initiatives, and do not use the same category as the last one. "
    "Follow the memory rule given in the context; on that alone, when in "
    "doubt, SKIP. Do not mention that you are an AI, that this is "
    "unprompted, or how you know things. Address the user as the profile "
    "says to."
)


def compose_initiative(context: Dict[str, str]) -> str:
    """Ask the cloud model whether to say something, and what. Raises on
    failure; returns "" when the model declines (SKIP)."""
    from openjarvis.core.config import load_config
    from openjarvis.core.types import Message, Role
    from openjarvis.engine._discovery import get_engine

    settings = load_settings()
    config = load_config()
    resolved = get_engine(
        config, engine_key=settings.moments_engine, model=settings.moments_model
    )
    if resolved is None:
        raise RuntimeError(
            f"engine {settings.moments_engine!r} cannot serve "
            f"{settings.moments_model!r}"
        )
    body = "\n".join(f"{key}: {value}" for key, value in context.items())
    messages = [
        Message(role=Role.SYSTEM, content=INITIATIVE_SYSTEM_PROMPT),
        Message(role=Role.USER, content=f"Context:\n{body}\n\nSay something, or SKIP."),
    ]
    # A reasoning model spends tokens before the first word; 120 left it
    # with nothing to say, which read as a decline every single time.
    result = resolved[1].generate(
        messages, model=settings.moments_model, temperature=0.8, max_tokens=600
    )
    category, line = parse_initiative_reply(str(result.get("content") or ""))
    if not line:
        return ""
    # The category rides in front of the line so the engine can record it;
    # it is stripped before anything is spoken.
    return f"[{category}] {line}" if category else line


def fallback_text(
    kind: str, context: Dict[str, str], *, avoid: Optional[str] = None
) -> str:
    """Spoken when the model is unavailable; a moment must never go silent.

    *avoid* is the line spoken last time, so two fallbacks in a row differ.
    """
    import random

    if kind in FALLBACK_LINES:
        away = context.get("away_for", "")
        tod = context.get("time_of_day") or "day"
        pool = FALLBACK_LINES[kind]
        if kind == MOMENT_WELCOME_BACK and not away:
            pool = _NO_AWAY[kind]
        lines = [
            line.format(
                tod=tod, Tod=tod.capitalize(), away=away, Away=away.capitalize()
            )
            for line in pool
        ]
        choices = [line for line in lines if line != avoid] or lines
        return random.choice(choices)
    if kind == MOMENT_INITIATIVE:
        return "Sir, how is the work going?"
    told = context.get("told", "")
    whats = [
        line.split("when: ", 1)[1].split(". It ", 1)[0]
        for line in told.splitlines()
        if "when: " in line
    ]
    if whats:
        return "Sir, you asked me to tell you: " + "; ".join(whats) + "."
    return "Sir, something you asked about has happened."


def compose_with_model(kind: str, context: Dict[str, str]) -> str:
    """Ask the configured cloud model for the words. Raises on failure."""
    from openjarvis.core.config import load_config
    from openjarvis.core.types import Message, Role
    from openjarvis.engine._discovery import get_engine

    settings = load_settings()
    config = load_config()
    resolved = get_engine(
        config, engine_key=settings.moments_engine, model=settings.moments_model
    )
    if resolved is None:
        raise RuntimeError(
            f"engine {settings.moments_engine!r} cannot serve "
            f"{settings.moments_model!r}"
        )
    engine = resolved[1]
    labels = {
        MOMENT_GREETING: (
            "Greeting: the user's first appearance today. Greet them for the "
            "time of day given in the context, not by assumption."
        ),
        MOMENT_WELCOME_BACK: "Welcome back: the user has returned after being away.",
        MOMENT_TOLD: (
            "Told on request: something the user asked to be told about has happened."
        ),
    }
    body = "\n".join(
        f"{key}: {value}" for key, value in context.items() if key != "recent_openings"
    )
    # Without this the model opens every greeting the same way; it has no
    # memory of yesterday's. The record is the memory.
    variety = ""
    if context.get("recent_openings"):
        variety = (
            "\n\nYou opened your last few unprompted remarks with:\n"
            f"{context['recent_openings']}\n"
            "Do not open the same way, and vary the rhythm of the sentence."
        )
    messages = [
        Message(role=Role.SYSTEM, content=SYSTEM_PROMPT),
        Message(
            role=Role.USER,
            content=f"Occasion: {labels[kind]}\n\nContext:\n{body}{variety}",
        ),
    ]
    result = engine.generate(
        messages, model=settings.moments_model, temperature=0.7, max_tokens=600
    )
    text = str(result.get("content") or "").strip()
    if not text:
        raise RuntimeError("empty reply")
    return text


# -- Delivery ----------------------------------------------------------------


def chime_now() -> bool:
    """The chime on its own: played the moment there is something to say,
    before the model has written it, so the acknowledgement is instant even
    when the wording takes a few seconds."""
    from openjarvis.speech.chime import chime_path
    from openjarvis.speech.player import play_file

    return play_file(str(chime_path()))


def speak_aloud(text: str) -> bool:
    """Synthesise with the configured voice and play through the speakers."""
    from openjarvis.core.config import load_config
    from openjarvis.speech.cartesia_tts import CartesiaTTSBackend
    from openjarvis.speech.ducking import ducked
    from openjarvis.speech.player import play_file
    from openjarvis.speech.spoken_text import to_spoken_text

    spoken = to_spoken_text(text) or text
    config = load_config()
    speech = getattr(config, "speech", None)
    result = CartesiaTTSBackend().synthesize(
        spoken,
        voice_id=getattr(speech, "voice_id", "") or "",
        speed=float(getattr(speech, "voice_speed", 1.0) or 1.0),
        volume=float(getattr(speech, "voice_volume", 1.0) or 1.0),
    )
    with tempfile.NamedTemporaryFile(
        prefix="sage-moment-", suffix=f".{result.format}", delete=False
    ) as handle:
        handle.write(result.audio)
        path = handle.name
    try:
        with ducked():
            return play_file(path, duck=False)
    finally:
        try:
            Path(path).unlink()
        except OSError:
            pass


# -- Engine ------------------------------------------------------------------


class MomentEngine:
    """Polls presence and speaks when a moment is due.

    The composer, speaker, clock and scheduler lookup are injectable so the
    loop can be driven in a test without a model, a voice or a desk.
    """

    def __init__(
        self,
        monitor: Any,
        *,
        config_dir: Optional[Path] = None,
        clock: Callable[[], float] = time.time,
        composer: Callable[[str, Dict[str, str]], str] = compose_with_model,
        speaker: Callable[[str], bool] = speak_aloud,
        chimer: Callable[[], bool] = chime_now,
        initiative_composer: Callable[[Dict[str, str]], str] = compose_initiative,
        busy_sensor: Optional[Callable[[Any, PresenceSettings], List[str]]] = None,
        activity_source: Optional[Callable[[], Any]] = None,
        scheduler_lookup: Optional[Callable[[], Any]] = None,
        timezone_name: Optional[str] = None,
        startup_hook: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._monitor = monitor
        self._config_dir = config_dir
        self._clock = clock
        self._composer = composer
        self._speaker = speaker
        self._chimer = chimer
        self._initiative_composer = initiative_composer
        self._busy_sensor = busy_sensor or _default_busy
        self._activity_source = activity_source or _default_activity
        self._scheduler_lookup = scheduler_lookup or _default_scheduler
        self._timezone = timezone_name
        # Runs once before the first tick: the greeting draws on yesterday's
        # episode, and with Sage off at 23:00 that has to be written first.
        self._startup_hook = startup_hook
        self._state = load_state(config_dir)
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_reason = ""
        self._last_persisted_present: Optional[float] = self._state.last_present_at

    # -- state access ------------------------------------------------------

    @property
    def timezone_name(self) -> str:
        if self._timezone is None:
            self._timezone = configured_timezone()
        return self._timezone

    def _save(self) -> None:
        try:
            save_state(self._state, self._config_dir)
        except Exception as exc:
            logger.warning("Could not save moments state: %s", exc)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            local_day = to_local(self._clock(), self.timezone_name).date().isoformat()
            now = self._clock()
            until = self._state.snoozed_until
            return {
                "snoozed_today": self._state.snoozed_day == local_day,
                "snoozed_until": until if until and until > now else None,
                "initiative_mode": self._monitor.settings().initiative_mode,
                "last_reason": self._last_reason,
                "watches": [w.to_dict() for w in self._state.watches],
                "history": [h.to_dict() for h in self._state.history[-HISTORY_LIMIT:]],
                "running": self.running(),
            }

    def snooze_today(self, snoozed: bool = True) -> bool:
        with self._lock:
            local_day = to_local(self._clock(), self.timezone_name).date().isoformat()
            self._state.snoozed_day = local_day if snoozed else ""
            self._save()
            return snoozed

    def snoozed_today(self) -> bool:
        return self.snapshot()["snoozed_today"]

    def snooze_for(self, seconds: float) -> float:
        """Timed quiet: nothing unprompted until *seconds* from now."""
        with self._lock:
            self._state.snoozed_until = self._clock() + max(0.0, seconds)
            self._save()
            return self._state.snoozed_until

    def resume(self) -> None:
        """Lift any quiet, timed or for the day."""
        with self._lock:
            self._state.snoozed_until = None
            self._state.snoozed_day = ""
            self._save()

    def add_watch(
        self,
        what: str,
        *,
        due_at: Optional[float] = None,
        task_id: Optional[str] = None,
    ) -> Watch:
        if (due_at is None) == (task_id is None):
            raise ValueError("a watch is either a time or a job, not both or neither")
        watch = Watch(
            id=uuid.uuid4().hex[:8],
            what=what.strip(),
            created_at=self._clock(),
            due_at=due_at,
            task_id=task_id,
        )
        with self._lock:
            self._state.watches.append(watch)
            self._save()
        return watch

    def cancel_watch(self, watch_id: str) -> bool:
        with self._lock:
            before = len(self._state.watches)
            self._state.watches = [w for w in self._state.watches if w.id != watch_id]
            removed = len(self._state.watches) != before
            if removed:
                self._save()
            return removed

    def list_watches(self) -> List[Watch]:
        with self._lock:
            return list(self._state.watches)

    def history(self) -> List[MomentRecord]:
        with self._lock:
            return list(self._state.history)

    # -- the loop ----------------------------------------------------------

    def tick(self) -> List[MomentRecord]:
        """One poll: decide, and speak what is due. Returns what was said."""
        now = self._clock()
        settings = self._monitor.settings()
        snapshot = self._monitor.snapshot()
        scheduler = self._scheduler_lookup()
        local = to_local(now, self.timezone_name)

        activity = None
        busy: List[str] = []
        if settings.initiative_mode != "off":
            try:
                activity = self._activity_source()
                busy = list(self._busy_sensor(activity, settings))
            except Exception:
                logger.debug("Busy sensor failed", exc_info=True)
        with self._lock:
            state = self._state
            settled = self._settle_pending(state, activity, snapshot, now)
            released = self._release_held(state, activity, busy, snapshot, now)
            if released is not None:
                return released
            decision = decide(
                snapshot,
                settings,
                state,
                now,
                local,
                lambda watch: _job_result(scheduler, watch),
                activity=activity,
                busy=busy,
            )
            self._last_reason = decision.reason or ", ".join(decision.kinds)
            for watch in decision.stale_watches:
                state.watches = [w for w in state.watches if w.id != watch.id]
                self._record(
                    MOMENT_TOLD,
                    f"(not said) {watch.what}",
                    spoken=False,
                    detail="too late to be useful by the time you were back",
                    now=now,
                )

            spoken: List[MomentRecord] = []
            # Initiative is decided before the chime: the writer may decline,
            # and a chime followed by nothing would be its own small alarm.
            initiative_text: Optional[str] = None
            if MOMENT_INITIATIVE in decision.kinds:
                context = build_context(
                    MOMENT_INITIATIVE,
                    decision,
                    now=now,
                    timezone_name=self.timezone_name,
                    config_dir=self._config_dir,
                    scheduler=scheduler,
                    state=state,
                )
                try:
                    initiative_text = self._initiative_composer(context)
                except Exception as exc:
                    logger.warning("Initiative writer unavailable: %s", exc)
                    initiative_text = ""
                if initiative_text and self._user_engaged_since(now):
                    # They started talking while the line was being written.
                    # Not dropped: said after the exchange, if still fresh.
                    decision.kinds.remove(MOMENT_INITIATIVE)
                    state.held_line = initiative_text
                    state.held_at = now
                    self._last_reason = "held: the user started talking"
                    self._record(
                        MOMENT_INITIATIVE,
                        initiative_text,
                        spoken=False,
                        detail="held: the user started talking",
                        now=now,
                    )
                    self._save()
                    initiative_text = None
                elif not initiative_text:
                    # Declined, or unavailable: silence, at half a cooldown,
                    # so a run of declines does not become a run of attempts.
                    decision.kinds.remove(MOMENT_INITIATIVE)
                    state.last_initiative_at = (
                        now - settings.initiative_cooldown_seconds / 2
                    )
                    self._last_reason = "initiative declined"
                    self._record(
                        MOMENT_INITIATIVE,
                        "(nothing worth saying)",
                        spoken=False,
                        detail="skip",
                        now=now,
                    )
                    self._save()
            if decision.kinds:
                # Acknowledge at once; the words follow when written.
                try:
                    self._chimer()
                except Exception as exc:
                    logger.debug("Chime skipped: %s", exc)
            for kind in decision.kinds:
                context = build_context(
                    kind,
                    decision,
                    now=now,
                    timezone_name=self.timezone_name,
                    config_dir=self._config_dir,
                    scheduler=scheduler,
                    state=state,
                )
                detail = ""
                try:
                    if kind == MOMENT_INITIATIVE and initiative_text:
                        category, text = parse_initiative_reply(initiative_text)
                        if category:
                            detail = f"category={category}"
                    else:
                        text = self._composer(kind, context)
                except Exception as exc:
                    logger.warning(
                        "Moment %s: model unavailable (%s); using fallback", kind, exc
                    )
                    text = fallback_text(
                        kind, context, avoid=last_fallback(state.history, kind)
                    )
                    detail = f"model unavailable: {exc}"
                try:
                    played = bool(self._speaker(text))
                except Exception as exc:
                    logger.warning("Moment %s could not be spoken: %s", kind, exc)
                    played = False
                    detail = (detail + "; " if detail else "") + f"not spoken: {exc}"
                # Counted whether or not the audio worked: retrying a failed
                # voice every fifteen seconds would be its own kind of noise.
                state.fired.setdefault(kind, []).append(now)
                state.fired[kind] = [
                    t for t in state.fired[kind] if now - t < 24 * 3600
                ]
                if kind == MOMENT_TOLD:
                    done = {w.id for w in decision.watches}
                    state.watches = [w for w in state.watches if w.id not in done]
                if decision.absence_end is not None:
                    state.handled_absence_end = decision.absence_end
                state.last_unprompted_at = now
                if kind == MOMENT_INITIATIVE:
                    state.last_initiative_at = now
                    # Now wait for an answer (see follow_up_due).
                    state.pending_prompt_at = now
                    state.pending_followed_up = False
                record = self._record(kind, text, spoken=played, detail=detail, now=now)
                record.ended_at = time.time()
                spoken.append(record)

            # An absence too short for a greeting is still answered, so it
            # is not greeted later when a longer one ends.
            if (
                snapshot.state == STATE_PRESENT
                and snapshot.last_absence_ended_at is not None
                and decision.absence_end is None
            ):
                state.handled_absence_end = snapshot.last_absence_ended_at

            if snapshot.state == STATE_PRESENT:
                state.last_present_at = now
            changed = bool(spoken or decision.stale_watches or settled)
            last = self._last_persisted_present
            if changed or (
                state.last_present_at is not None
                and (last is None or state.last_present_at - last >= 60)
            ):
                self._last_persisted_present = state.last_present_at
                self._save()
            return spoken

    def _user_engaged_since(self, since: float) -> bool:
        """Whether the user began a turn, or Sage is mid-turn, since *since*.
        Read fresh: the writer took seconds, and the desk may have changed."""
        try:
            activity = self._activity_source()
        except Exception:
            return False
        last_user = getattr(activity, "last_user_turn_at", None)
        if last_user is not None and last_user >= since:
            return True
        return bool(getattr(activity, "sage_mid_turn", False))

    def _release_held(
        self,
        state: MomentsState,
        activity: Any,
        busy: Sequence[str],
        snapshot: PresenceSnapshot,
        now: float,
    ) -> Optional[List[MomentRecord]]:
        """Say a held line once the exchange is over, or let it go stale.
        Returns the records spoken, or None when nothing was done."""
        if not state.held_line:
            return None
        if state.held_at is not None and now - state.held_at > HELD_LINE_STALE_SECONDS:
            self._record(
                MOMENT_INITIATIVE,
                state.held_line,
                spoken=False,
                detail="held too long; dropped",
                now=now,
            )
            state.held_line, state.held_at = "", None
            self._save()
            return None
        if snapshot.state != STATE_PRESENT or busy:
            return None
        last_user = getattr(activity, "last_user_turn_at", None) if activity else None
        last_reply = getattr(activity, "last_reply_end_at", None) if activity else None
        recent = max([t for t in (last_user, last_reply) if t] or [0.0])
        if recent and now - recent < HELD_LINE_LULL_SECONDS:
            self._last_reason = "a line is waiting for a lull"
            return None
        category, line = parse_initiative_reply(state.held_line)
        state.held_line, state.held_at = "", None
        try:
            self._chimer()
        except Exception as exc:
            logger.debug("Chime skipped: %s", exc)
        try:
            played = bool(self._speaker(line))
        except Exception as exc:
            logger.warning("Held line could not be spoken: %s", exc)
            played = False
        state.fired.setdefault(MOMENT_INITIATIVE, []).append(now)
        state.last_unprompted_at = now
        state.last_initiative_at = now
        state.pending_prompt_at = now
        state.pending_followed_up = False
        record = self._record(
            MOMENT_INITIATIVE,
            line,
            spoken=played,
            detail="said after the exchange"
            + (f"; category={category}" if category else ""),
            now=now,
        )
        record.ended_at = time.time()
        self._last_reason = "initiative (held line)"
        self._save()
        return [record]

    def _settle_pending(
        self, state: MomentsState, activity: Any, snapshot: PresenceSnapshot, now: float
    ) -> bool:
        """Answer, follow up, or let go of a prompt awaiting an answer.
        Returns whether the state changed."""
        if state.pending_prompt_at is None:
            return False
        if snapshot.state != STATE_PRESENT:
            # They left; nobody ignored anything.
            state.pending_prompt_at = None
            self._last_reason = "prompt dropped: user left"
            return True
        local = to_local(now, self.timezone_name)
        settings = self._monitor.settings()
        if (
            state.snoozed_day == local.date().isoformat()
            or (state.snoozed_until is not None and state.snoozed_until > now)
            or in_quiet_hours(
                local.hour,
                settings.quiet_hours_start_local,
                settings.quiet_hours_end_local,
            )
        ):
            # Told to be quiet: no follow-up, and no mark against them.
            state.pending_prompt_at = None
            return True
        outcome = follow_up_due(state, activity, now)
        if outcome == "answered":
            state.pending_prompt_at = None
            state.unanswered_streak = 0
            state.backoff_until = None
            self._record(
                MOMENT_INITIATIVE,
                "(answered)",
                spoken=False,
                detail="answered",
                now=now,
            )
            return True
        if outcome == "follow-up":
            state.pending_followed_up = True
            last = next(
                (h.text for h in reversed(state.history) if h.detail == "follow-up"),
                None,
            )
            import random

            choices = [line for line in FOLLOW_UP_LINES if line != last] or list(
                FOLLOW_UP_LINES
            )
            line = random.choice(choices)
            try:
                played = bool(self._speaker(line))
            except Exception as exc:
                logger.warning("Follow-up could not be spoken: %s", exc)
                played = False
            state.last_unprompted_at = now
            record = self._record(
                MOMENT_INITIATIVE, line, spoken=played, detail="follow-up", now=now
            )
            record.ended_at = time.time()
            return True
        if outcome == "give-up":
            state.pending_prompt_at = None
            state.unanswered_streak += 1
            detail = "unanswered"
            if state.unanswered_streak >= 2:
                state.backoff_until = now + BACKOFF_SECONDS
                detail = "unanswered twice; backing off"
            self._record(
                MOMENT_INITIATIVE, "(no answer)", spoken=False, detail=detail, now=now
            )
            return True
        return False

    def _record(
        self, kind: str, text: str, *, spoken: bool, detail: str, now: float
    ) -> MomentRecord:
        record = MomentRecord(
            at=now, kind=kind, text=text, spoken=spoken, detail=detail
        )
        self._state.history.append(record)
        self._state.history = self._state.history[-HISTORY_LIMIT:]
        logger.info(
            "Moment %s (%s): %s", kind, "spoken" if spoken else "not spoken", text
        )
        return record

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        # The monitor's own poll is the moment a return is known; waiting for
        # this loop's next poll on top of it made a greeting arrive half a
        # minute after the user sat down.
        add = getattr(self._monitor, "add_listener", None)
        if callable(add):
            add(self._on_presence_change)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="moments", daemon=True)
        self._thread.start()

    def _on_presence_change(self, previous: str, current: str) -> None:
        if current == STATE_PRESENT and not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.debug("Moments tick on presence change failed", exc_info=True)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        if self._startup_hook is not None:
            try:
                self._startup_hook()
            except Exception:
                logger.warning("Moments startup hook failed", exc_info=True)
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.debug("Moments tick failed", exc_info=True)
            try:
                interval = max(1, self._monitor.settings().poll_interval_seconds)
            except Exception:
                interval = 15
            self._stop.wait(interval)


# The running engine, for the tools. Set by ``jarvis serve`` (which hand-
# assembles its system, so there is no builder injection to ride); a CLI run
# without a server finds None and edits the state file directly.
_current: Optional[MomentEngine] = None


def set_current_engine(engine: Optional[MomentEngine]) -> None:
    global _current
    _current = engine


def current_engine() -> Optional[MomentEngine]:
    return _current


def _default_activity() -> Any:
    from openjarvis.core.activity import snapshot as activity_snapshot

    return activity_snapshot()


def _default_busy(activity: Any, settings: PresenceSettings) -> List[str]:
    from openjarvis.core.busy import busy_reasons

    return busy_reasons(
        activity,
        call_titles=settings.initiative_call_titles,
        call_mic_apps=settings.initiative_call_mic_apps,
    )


def _default_scheduler() -> Any:
    try:
        from openjarvis.scheduler.tools import ListScheduledTasksTool

        return getattr(ListScheduledTasksTool, "_scheduler", None)
    except Exception:
        return None


__all__ = [
    "DAILY_CAPS",
    "KINDS",
    "MOMENT_GREETING",
    "MOMENT_INITIATIVE",
    "INITIATIVE_CATEGORIES",
    "SKIP",
    "FOLLOW_UP_AFTER_SECONDS",
    "FOLLOW_UP_LINES",
    "MOMENT_TOLD",
    "MOMENT_WELCOME_BACK",
    "Decision",
    "MomentEngine",
    "MomentRecord",
    "MomentsState",
    "Watch",
    "build_context",
    "chime_now",
    "compose_initiative",
    "compose_with_model",
    "current_engine",
    "decide",
    "describe_duration",
    "FALLBACK_LINES",
    "fallback_text",
    "follow_up_due",
    "last_fallback",
    "recent_openings",
    "in_quiet_hours",
    "initiative_context",
    "initiative_holdback",
    "parse_initiative_reply",
    "load_state",
    "local_to_timestamp",
    "save_state",
    "set_current_engine",
    "speak_aloud",
    "time_of_day",
]
