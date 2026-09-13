"""Episodes: memory that spans days.

Automatic memory keeps up to 500 durable *facts* -- "prefers metric",
"has a class on Tuesdays" -- re-injected into every prompt. That is the right
shape for things that stay true, and the wrong shape for what happened: it
cannot say "yesterday you were debugging the Waze voice pack", because that
is not a fact about the user, it is a day.

An *episode* is one day's conversations, summarised in a few sentences and
stored by date. The last few are offered to the model as "recent days", so
"what were we working on yesterday" has a true answer, and the moments in
M36 phase 3 have something real to draw on.

The source is the trace store, which already records every chat turn's query
and result. Only turns from the chat agents count; scheduled jobs (the class
notifier, the digest, the proactive run) are Sage talking to itself and would
make every day read as "checked the schedule 96 times".

This is the rolling summary M30 deferred, built for a different reason:
recall, not context compression. The conversation window is untouched.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from openjarvis.core.config import DEFAULT_CONFIG_DIR

logger = logging.getLogger(__name__)

_EPISODES_FILE = "episodes.json"
_TRACES_FILE = "traces.db"
_SCHEDULER_FILE = "scheduler.db"

# Agents whose turns are the user talking to Sage. Everything else in the
# trace store is Sage talking to itself on a schedule.
USER_AGENTS = frozenset({"orchestrator", "simple"})

# Days kept on file. Enough to answer "last week", not a second archive.
KEEP_DAYS = 30

# What each turn contributes to the summariser's input. Long answers are
# cut, not dropped: the shape of the day matters more than any one reply.
_QUERY_CHARS = 300
_RESULT_CHARS = 400
_MAX_TURNS = 80


@dataclass
class Episode:
    day: str  # YYYY-MM-DD, local
    summary: str
    turns: int
    written_at: float
    model: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Turn:
    at: float
    query: str
    result: str


# -- Store -------------------------------------------------------------------


def episodes_path(config_dir: Optional[Path] = None) -> Path:
    return (config_dir or DEFAULT_CONFIG_DIR) / _EPISODES_FILE


def load_episodes(config_dir: Optional[Path] = None) -> Dict[str, Episode]:
    """Episodes by day. A missing or damaged file is simply no history."""
    try:
        raw = json.loads(episodes_path(config_dir).read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Episode] = {}
    for day, entry in raw.items():
        if not isinstance(entry, dict) or not entry.get("summary"):
            continue
        try:
            out[str(day)] = Episode(
                day=str(day),
                summary=str(entry["summary"]),
                turns=int(entry.get("turns") or 0),
                written_at=float(entry.get("written_at") or 0),
                model=str(entry.get("model") or ""),
            )
        except Exception:
            continue
    return out


def save_episode(episode: Episode, config_dir: Optional[Path] = None) -> None:
    """Write one episode, replacing any for the same day, pruning old ones."""
    episodes = load_episodes(config_dir)
    episodes[episode.day] = episode
    cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
    kept = {d: e.to_dict() for d, e in sorted(episodes.items()) if d >= cutoff}
    path = episodes_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(kept, indent=2), encoding="utf-8")


def recent_episodes(
    count: int = 3,
    *,
    today: Optional[date] = None,
    config_dir: Optional[Path] = None,
) -> List[Episode]:
    """The last *count* episodes before today, oldest first.

    Episodes on file, not calendar days: the user asked "what were we working
    on yesterday" after four days without a conversation, and a
    calendar-window lookup found nothing and said so. The useful answer is
    "we did not talk yesterday; on Tuesday we...", which needs the last
    conversations, whenever they were.

    Today is excluded: its episode is written at night, and until then the
    conversation itself is the record.
    """
    today = today or date.today()
    cutoff = today.isoformat()
    episodes = load_episodes(config_dir)
    past = sorted(d for d in episodes if d < cutoff)
    return [episodes[d] for d in past[-count:]]


def _day_label(day: str, today: date) -> str:
    try:
        delta = (today - date.fromisoformat(day)).days
    except Exception:
        return day
    if delta == 1:
        return "Yesterday"
    if 1 < delta <= 6:
        return datetime.strptime(day, "%Y-%m-%d").strftime("%A")
    return day


def format_recent_days(
    episodes: Sequence[Episode], today: Optional[date] = None
) -> str:
    """Render episodes for the prompt, naming each day relative to today.

    Says outright when the most recent conversation was not yesterday, so
    the model can answer "we did not talk yesterday" instead of treating a
    gap as a mystery.
    """
    if not episodes:
        return ""
    today = today or date.today()
    lines = [f"{_day_label(ep.day, today)}: {ep.summary}" for ep in episodes]
    latest = episodes[-1].day
    yesterday = (today - timedelta(days=1)).isoformat()
    if latest < yesterday:
        try:
            gap = (today - date.fromisoformat(latest)).days
        except Exception:
            gap = None
        when = _day_label(latest, today)
        if gap:
            lines.append(
                f"There were no conversations between {when} and today "
                f"({gap} days). The most recent conversation was {when}."
            )
    return "\n".join(lines)


# -- Source ------------------------------------------------------------------


def _day_bounds(day: date) -> tuple[float, float]:
    start = datetime(day.year, day.month, day.day)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def scheduled_prompts(
    *, scheduler_path: Optional[Path] = None, config_dir: Optional[Path] = None
) -> set:
    """Prompts of every scheduled task, past or present.

    A job configured on the orchestrator agent leaves a trace indistinguishable
    from a typed message -- "Check my calendar for events before 9 AM" is not
    something the user said, it is something the scheduler says every morning.
    The only reliable marker is the prompt itself, so those are excluded by
    exact match. Cancelled tasks are included because they ran once.
    """
    path = scheduler_path or (config_dir or DEFAULT_CONFIG_DIR) / _SCHEDULER_FILE
    if not path.exists():
        return set()
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT prompt FROM scheduled_tasks").fetchall()
        finally:
            conn.close()
    except Exception:
        return set()
    return {str(r[0]).strip() for r in rows if r and r[0]}


def collect_turns(
    day: date,
    *,
    traces_path: Optional[Path] = None,
    scheduler_path: Optional[Path] = None,
    config_dir: Optional[Path] = None,
) -> List[Turn]:
    """The user's chat turns on *day*, oldest first, from the trace store."""
    path = traces_path or (config_dir or DEFAULT_CONFIG_DIR) / _TRACES_FILE
    if not path.exists():
        return []
    scheduled = scheduled_prompts(scheduler_path=scheduler_path, config_dir=config_dir)
    start, end = _day_bounds(day)
    placeholders = ",".join("?" for _ in USER_AGENTS)
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT started_at, query, result FROM traces "
                f"WHERE agent IN ({placeholders}) "
                "AND CAST(started_at AS REAL) >= ? AND CAST(started_at AS REAL) < ? "
                "ORDER BY CAST(started_at AS REAL)",
                (*sorted(USER_AGENTS), start, end),
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("Episode source unreadable: %s", exc)
        return []
    turns = []
    for started_at, query, result in rows:
        query = (query or "").strip()
        if not query or query in scheduled:
            continue
        try:
            at = float(started_at)
        except Exception:
            at = 0.0
        turns.append(Turn(at=at, query=query, result=(result or "").strip()))
    return turns


# -- Summary -----------------------------------------------------------------

SUMMARY_SYSTEM_PROMPT = (
    "You write a short diary entry for an assistant named Sage about one day "
    "of conversations with its user. Write in the third person, past tense, "
    "as Sage recalling the day: what the user asked about, what was worked "
    "on, what was decided or left unfinished. Three to five sentences. Name "
    "specific things -- a file, a feature, a place, a problem -- rather than "
    "generalities. Skip greetings, tests and one-word exchanges. Never invent "
    "anything not in the transcript, and never include secrets, keys, codes "
    "or full URLs. Output the entry only, with no heading."
)


def transcript_for_prompt(turns: Sequence[Turn]) -> str:
    """Turns as a compact transcript, bounded so a busy day still fits."""
    kept = list(turns)[-_MAX_TURNS:]
    lines = []
    for turn in kept:
        stamp = time.strftime("%H:%M", time.localtime(turn.at)) if turn.at else "--:--"
        q = turn.query[:_QUERY_CHARS]
        r = turn.result[:_RESULT_CHARS]
        lines.append(f"[{stamp}] User: {q}\nSage: {r}")
    return "\n\n".join(lines)


__all__ = [
    "KEEP_DAYS",
    "SUMMARY_SYSTEM_PROMPT",
    "USER_AGENTS",
    "Episode",
    "Turn",
    "collect_turns",
    "episodes_path",
    "format_recent_days",
    "load_episodes",
    "recent_episodes",
    "save_episode",
    "scheduled_prompts",
    "transcript_for_prompt",
]
