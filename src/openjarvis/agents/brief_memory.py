"""What the morning briefs already told the user, and what is done (2 October).

The briefs had no memory of themselves. The same "PMFC exam -- submit by
tonight" led them for days after the exam was taken, and "two failed Sage CI
runs" stayed after the next run passed. The user's rules: an item reported
before and still open stays, marked "Still open since <day>", after the new
ones; an item is dropped when the user says it is done, or when the evidence
shows they acted on it; a CI failure is dropped when GitHub's latest run on
that branch passed.

The model applies the rules -- items are free text from several sources, so
there is no stable key to match on -- but every fact it decides from is
handed to it here: the earlier briefs, the done list, and the CI status
asked of GitHub rather than inferred from old email.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import sqlite3
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from openjarvis.core.paths import get_config_dir

logger = logging.getLogger(__name__)

_DONE_FILE = "brief_done.json"
#: A done item is forgotten after this long; by then nothing still nags.
DONE_KEEP_DAYS = 30
#: How many days of earlier briefs a new one is shown.
PREVIOUS_DAYS = 3
_PREVIOUS_CHARS = 900

Brief = Tuple[_dt.date, str]


def _done_path(config_dir: Optional[Path] = None) -> Path:
    return (config_dir or get_config_dir()) / _DONE_FILE


def done_items(config_dir: Optional[Path] = None) -> List[Dict[str, str]]:
    """Items the user marked done in the last ``DONE_KEEP_DAYS``."""
    try:
        raw = json.loads(_done_path(config_dir).read_text(encoding="utf-8"))
    except Exception:
        return []
    cutoff = (_dt.date.today() - _dt.timedelta(days=DONE_KEEP_DAYS)).isoformat()
    return [
        item
        for item in raw
        if isinstance(item, dict) and str(item.get("day", "")) >= cutoff
    ]


def mark_done(item: str, config_dir: Optional[Path] = None) -> Dict[str, str]:
    item = " ".join((item or "").split())[:200]
    if not item:
        raise ValueError("nothing to mark done")
    entry = {"item": item, "day": _dt.date.today().isoformat()}
    kept = [e for e in done_items(config_dir) if e["item"].lower() != item.lower()]
    path = _done_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(kept + [entry], indent=1), encoding="utf-8")
    return entry


def digest_briefs(
    config_dir: Optional[Path] = None, days: int = PREVIOUS_DAYS
) -> List[Brief]:
    """Earlier 05:00 digests (digest.db), oldest first, today excluded."""
    path = (config_dir or get_config_dir()) / "digest.db"
    today = _dt.date.today()
    since = (today - _dt.timedelta(days=days)).isoformat()
    try:
        with sqlite3.connect(str(path)) as db:
            rows = db.execute(
                "select generated_at, text from digests where generated_at >= ? "
                "order by generated_at",
                (since,),
            ).fetchall()
    except sqlite3.Error:
        return []
    out: List[Brief] = []
    for stamp, text in rows:
        try:
            day = _dt.date.fromisoformat(str(stamp)[:10])
        except ValueError:
            continue
        if day < today and text:
            out.append((day, str(text)))
    return out


def operator_briefs(
    agent_id: str, config_dir: Optional[Path] = None, days: int = PREVIOUS_DAYS
) -> List[Brief]:
    """An Operator's earlier reports (traces.db), oldest first, today excluded."""
    path = (config_dir or get_config_dir()) / "traces.db"
    today = _dt.date.today()
    since = _dt.datetime.combine(
        today - _dt.timedelta(days=days), _dt.time()
    ).timestamp()
    try:
        with sqlite3.connect(str(path)) as db:
            rows = db.execute(
                "select started_at, result from traces where agent = ? "
                "and started_at >= ? order by started_at",
                (agent_id, since),
            ).fetchall()
    except sqlite3.Error:
        return []
    out: List[Brief] = []
    for started, text in rows:
        day = _dt.date.fromtimestamp(float(started))
        if day < today and text and not str(text).startswith("Maximum turns"):
            out.append((day, str(text)))
    return out


def ci_status(timeout: float = 10.0) -> str:
    """GitHub's latest CI run on the branch Sage runs from, as one line.

    Asked of GitHub (the user's ``gh`` login on this PC) because a "run
    failed" email says nothing about whether the next run passed. "" when it
    cannot be asked; the brief is then told so instead.
    """
    repo = Path(__file__).resolve().parents[3]

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()

    try:
        branch = git("rev-parse", "--abbrev-ref", "HEAD")
        # Named explicitly: with an "upstream" remote too, gh picked the
        # wrong repository and found no runs at all.
        origin = git("remote", "get-url", "origin")
        slug = origin.removesuffix(".git").split("github.com/")[-1]
        out = subprocess.run(
            [
                "gh",
                "run",
                "list",
                "-R",
                slug,
                "-b",
                branch,
                "-L",
                "5",
                "--json",
                "conclusion,status,createdAt,displayTitle",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(repo),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        runs = json.loads(out.stdout or "[]")
    except Exception:
        logger.info("CI status unavailable", exc_info=True)
        return ""
    finished = [
        r
        for r in runs
        if r.get("status") == "completed"
        and r.get("conclusion") in ("success", "failure")
    ]
    if not branch or not finished:
        return ""
    latest = finished[0]
    when = str(latest.get("createdAt", ""))[:16].replace("T", " ")
    if latest["conclusion"] == "success":
        return (
            f"GitHub CI: the latest run on {branch} PASSED ({when} UTC). Any "
            "earlier 'run failed' email is already fixed: do not mention it."
        )
    return (
        f"GitHub CI: the latest run on {branch} FAILED ({when} UTC, "
        f'"{latest.get("displayTitle", "")}"). Mention it once.'
    )


def memory_block(previous: List[Brief], config_dir: Optional[Path] = None) -> str:
    """The rules plus the facts they need, for a brief's prompt."""
    lines = ["BRIEF MEMORY (not evidence; how to treat repeats):"]
    if previous:
        lines.append("Your earlier briefs:")
        for day, text in previous[-PREVIOUS_DAYS:]:
            short = " ".join(text.split())[:_PREVIOUS_CHARS]
            lines.append(f"- {day:%a %b} {day.day}: {short}")
    done = done_items(config_dir)
    if done:
        lines.append("The user said these are DONE; leave them out:")
        lines.extend(f"- {item['item']} (marked {item['day']})" for item in done)
    ci = ci_status()
    lines.append(
        ci
        or "GitHub CI status unknown: mention a CI failure only if its email "
        "arrived today."
    )
    lines.append(
        "Rules: new items first. An item that was in an earlier brief and is "
        "still open stays, after the new ones, one short line starting "
        "'Still open since <weekday>:'. Leave an item out when it is marked "
        "done above, or when the evidence shows the user already acted on it "
        "(a reply, submission or confirmation on that topic after the "
        "request, or an assignment on Teams' Completed tab). A deadline whose "
        "date has passed is not 'tonight'."
    )
    return "\n".join(lines)


__all__ = [
    "DONE_KEEP_DAYS",
    "PREVIOUS_DAYS",
    "ci_status",
    "digest_briefs",
    "done_items",
    "mark_done",
    "memory_block",
    "operator_briefs",
]
