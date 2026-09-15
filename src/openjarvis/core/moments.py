"""Moments: the occasions on which Sage speaks first (M36 phase 3).

Three kinds, each its own switch beneath the presence master switch:

- **Good morning** -- the first time the user is at the desk in the morning,
  after a real absence (sleep, in practice).
- **Welcome back** -- the user returns after an absence of at least an hour.
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
from typing import Any, Callable, Dict, List, Optional

from openjarvis.core.config import DEFAULT_CONFIG_DIR
from openjarvis.core.presence import (
    STATE_PRESENT,
    PresenceSettings,
    PresenceSnapshot,
    load_settings,
)

logger = logging.getLogger(__name__)

MOMENT_GOOD_MORNING = "good_morning"
MOMENT_WELCOME_BACK = "welcome_back"
MOMENT_TOLD = "told"
KINDS = (MOMENT_GOOD_MORNING, MOMENT_WELCOME_BACK, MOMENT_TOLD)

# Good morning once a day; welcome back a few times, because a day can hold
# several real absences; told-on-request as often as it was asked for.
DAILY_CAPS = {MOMENT_GOOD_MORNING: 1, MOMENT_WELCOME_BACK: 3, MOMENT_TOLD: 20}
# The morning window closes at noon: a first appearance at 14:00 is a
# welcome back, not a good morning.
MORNING_ENDS_HOUR = 12
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

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MomentsState:
    # The local day "not now" applies to; empty when not snoozed.
    snoozed_day: str = ""
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
    for key in ("last_present_at", "handled_absence_end"):
        value = raw.get(key)
        if isinstance(value, (int, float)):
            setattr(state, key, float(value))
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


def in_morning_window(hour: int, quiet_end: int) -> bool:
    opens = quiet_end if quiet_end < MORNING_ENDS_HOUR else 5
    return opens <= hour < MORNING_ENDS_HOUR


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


def fired_today(state: MomentsState, kind: str, now: float) -> int:
    return sum(1 for t in state.fired.get(kind, []) if now - t < 24 * 3600)


def decide(
    snapshot: PresenceSnapshot,
    settings: PresenceSettings,
    state: MomentsState,
    now: float,
    local: datetime,
    job_finished: Callable[[Watch], Optional[str]],
) -> Decision:
    """Pure: what, if anything, should be said right now.

    *job_finished* answers whether a job watch's task has completed since the
    watch was set; it is injected so the policy needs no scheduler.
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
    if in_quiet_hours(
        local.hour, settings.quiet_hours_start_local, settings.quiet_hours_end_local
    ):
        decision.reason = "quiet hours"
        return decision

    # A return: the monitor saw an absence end that no moment has answered
    # yet. A gap in this module's own readings (the server was down, or the
    # night passed) is evidence only for good morning, never for welcome
    # back -- the monitor must have watched the user leave for that.
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
    long_gap = gap is None or gap >= settings.welcome_back_after_seconds

    if (
        settings.good_morning_enabled
        and in_morning_window(local.hour, settings.quiet_hours_end_local)
        and fired_today(state, MOMENT_GOOD_MORNING, now)
        < DAILY_CAPS[MOMENT_GOOD_MORNING]
        and (observed_return or long_gap)
    ):
        decision.kinds.append(MOMENT_GOOD_MORNING)
        decision.absence_end = absence_end if observed_return else None
        decision.absence_seconds = absence_seconds if observed_return else gap
    elif (
        settings.welcome_back_enabled
        and observed_return
        and fired_today(state, MOMENT_WELCOME_BACK, now)
        < DAILY_CAPS[MOMENT_WELCOME_BACK]
    ):
        decision.kinds.append(MOMENT_WELCOME_BACK)
        decision.absence_end = absence_end
        decision.absence_seconds = absence_seconds

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
                fired_today(state, MOMENT_TOLD, now) + len(decision.watches)
                < DAILY_CAPS[MOMENT_TOLD]
            ):
                decision.watches.append(watch)
    if decision.watches:
        decision.kinds.append(MOMENT_TOLD)
    if not decision.kinds:
        decision.reason = "nothing to say"
    return decision


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


def _today_classes() -> str:
    try:
        from openjarvis.tools.check_class_schedule import CheckClassScheduleTool

        result = CheckClassScheduleTool().execute(full_day=True)
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


def _finished_jobs(scheduler: Any, since: float, limit: int = 5) -> List[str]:
    """Scheduled runs that finished after *since*, newest first."""
    if scheduler is None or since is None:
        return []
    lines: List[str] = []
    try:
        store = getattr(scheduler, "_store", None)
        for task in scheduler.list_tasks():
            if task.metadata.get("managed_by"):
                # Sage's own housekeeping (episodes, class notifier) is not
                # news to the user.
                continue
            for run in store.get_run_logs(task.id, limit=3) if store else []:
                finished = _iso_to_ts(run.get("finished_at"))
                if finished is None or finished <= since:
                    continue
                outcome = "finished" if run.get("success") else "failed"
                result = str(run.get("result") or run.get("error") or "").strip()
                result = result.replace("\n", " ")[:200]
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
        "profile": _profile_excerpt(config_dir),
    }
    if decision.absence_seconds:
        context["away_for"] = describe_duration(decision.absence_seconds)
    if kind == MOMENT_GOOD_MORNING:
        try:
            from openjarvis.memory.episodes import format_recent_days, recent_episodes

            context["recent_days"] = format_recent_days(
                recent_episodes(2, today=local.date(), config_dir=config_dir),
                today=local.date(),
            )
        except Exception:
            context["recent_days"] = ""
        context["classes_today"] = _today_classes()
        context["calendar_today"] = _today_calendar(timezone_name, now)
    if kind in (MOMENT_GOOD_MORNING, MOMENT_WELCOME_BACK):
        since = decision.absence_end
        if since is not None and decision.absence_seconds:
            since = since - decision.absence_seconds
        elif state.last_present_at is not None:
            since = state.last_present_at
        if since is not None:
            context["finished_while_away"] = "\n".join(_finished_jobs(scheduler, since))
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


def fallback_text(kind: str, context: Dict[str, str]) -> str:
    """Spoken when the model is unavailable; a moment must never go silent."""
    if kind == MOMENT_GOOD_MORNING:
        return "Good morning, sir."
    if kind == MOMENT_WELCOME_BACK:
        away = context.get("away_for")
        return (
            f"Welcome back, sir. You were away {away}."
            if away
            else "Welcome back, sir."
        )
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
        MOMENT_GOOD_MORNING: "Good morning: the user's first appearance today.",
        MOMENT_WELCOME_BACK: "Welcome back: the user has returned after being away.",
        MOMENT_TOLD: (
            "Told on request: something the user asked to be told about has happened."
        ),
    }
    body = "\n".join(f"{key}: {value}" for key, value in context.items())
    messages = [
        Message(role=Role.SYSTEM, content=SYSTEM_PROMPT),
        Message(
            role=Role.USER, content=f"Occasion: {labels[kind]}\n\nContext:\n{body}"
        ),
    ]
    result = engine.generate(
        messages, model=settings.moments_model, temperature=0.7, max_tokens=200
    )
    text = str(result.get("content") or "").strip()
    if not text:
        raise RuntimeError("empty reply")
    return text


# -- Delivery ----------------------------------------------------------------


def speak_aloud(text: str) -> bool:
    """Synthesise with the configured voice and play through the speakers."""
    from openjarvis.core.config import load_config
    from openjarvis.speech.cartesia_tts import CartesiaTTSBackend
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
        return play_file(path)
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
        scheduler_lookup: Optional[Callable[[], Any]] = None,
        timezone_name: Optional[str] = None,
    ) -> None:
        self._monitor = monitor
        self._config_dir = config_dir
        self._clock = clock
        self._composer = composer
        self._speaker = speaker
        self._scheduler_lookup = scheduler_lookup or _default_scheduler
        self._timezone = timezone_name
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
            return {
                "snoozed_today": self._state.snoozed_day == local_day,
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

        with self._lock:
            state = self._state
            decision = decide(
                snapshot,
                settings,
                state,
                now,
                local,
                lambda watch: _job_result(scheduler, watch),
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
                    text = self._composer(kind, context)
                except Exception as exc:
                    logger.warning(
                        "Moment %s: model unavailable (%s); using fallback", kind, exc
                    )
                    text = fallback_text(kind, context)
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
                spoken.append(
                    self._record(kind, text, spoken=played, detail=detail, now=now)
                )

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
            changed = bool(spoken or decision.stale_watches)
            last = self._last_persisted_present
            if changed or (
                state.last_present_at is not None
                and (last is None or state.last_present_at - last >= 60)
            ):
                self._last_persisted_present = state.last_present_at
                self._save()
            return spoken

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
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="moments", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
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


def _default_scheduler() -> Any:
    try:
        from openjarvis.scheduler.tools import ListScheduledTasksTool

        return getattr(ListScheduledTasksTool, "_scheduler", None)
    except Exception:
        return None


__all__ = [
    "DAILY_CAPS",
    "KINDS",
    "MOMENT_GOOD_MORNING",
    "MOMENT_TOLD",
    "MOMENT_WELCOME_BACK",
    "Decision",
    "MomentEngine",
    "MomentRecord",
    "MomentsState",
    "Watch",
    "build_context",
    "compose_with_model",
    "current_engine",
    "decide",
    "describe_duration",
    "fallback_text",
    "in_morning_window",
    "in_quiet_hours",
    "load_state",
    "local_to_timestamp",
    "save_state",
    "set_current_engine",
    "speak_aloud",
]
