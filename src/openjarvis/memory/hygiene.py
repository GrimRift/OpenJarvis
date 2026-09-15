"""Nightly memory hygiene (M38 phase 2).

The extractor accumulates near-duplicates, contradictions and lines that
were only true on the day they were written. Each costs prompt budget on
every turn and, worse, wins recall from the fact it duplicates or
contradicts. Once a night -- and on boot if a night was missed, since Sage
is not on around the clock -- the cloud model reads the live facts and
proposes merges, removals and expiries. They are applied at once (the
user's choice), every removal is a soft delete restorable for a week, and
the run is logged so the Memory page can show what was cleaned last night.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from openjarvis.core.config import DEFAULT_CONFIG_DIR
from openjarvis.memory.store import Fact, LocalFactStore

logger = logging.getLogger(__name__)

_LOG_FILE = "memory_hygiene.json"
_KEEP_RUNS = 14

SYSTEM_PROMPT = (
    "You maintain a personal assistant's long-term memory: a list of short "
    "facts about its user, each with an id and the day it was learned. Find "
    "only three kinds of problem.\n"
    "1. DUPLICATES: facts that say the same thing. Keep the clearest, most "
    "complete wording (you may rewrite it), remove the others.\n"
    "2. CONTRADICTIONS: facts that cannot both be true. Keep the newest, "
    "remove the older.\n"
    "3. STALE: facts whose truth depended on the day they were written -- "
    "'has a class at 11 today', 'is currently debugging', 'will do X "
    "tomorrow'. Remove them; a recurring commitment stated with its actual "
    "day and time is not stale.\n"
    "Do not remove anything for any other reason. Do not invent facts. When "
    "unsure, leave both. Pinned facts must never be removed; they may only "
    "absorb a duplicate's wording.\n"
    'Reply with ONLY a JSON object: {"merges": [{"keep": id, "text": '
    'new wording or null, "remove": [ids]}], "contradictions": [{"keep": '
    'id, "remove": [ids]}], "stale": [ids]}. Empty lists are fine.'
)


@dataclass
class HygieneChange:
    kind: str  # merge | contradiction | stale
    kept_id: str = ""
    kept_text: str = ""
    removed: List[Dict[str, str]] = field(default_factory=list)  # id, text


@dataclass
class HygieneRun:
    at: float
    facts_before: int
    facts_after: int
    changes: List[HygieneChange]
    model: str = ""
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def log_path(config_dir: Optional[Path] = None) -> Path:
    return (config_dir or DEFAULT_CONFIG_DIR) / _LOG_FILE


def load_runs(config_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    try:
        raw = json.loads(log_path(config_dir).read_text(encoding="utf-8"))
        return raw if isinstance(raw, list) else []
    except Exception:
        return []


def _append_run(run: HygieneRun, config_dir: Optional[Path]) -> None:
    runs = load_runs(config_dir)
    runs.append(run.to_dict())
    path = log_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(runs[-_KEEP_RUNS:], indent=2), encoding="utf-8")


def last_run_at(config_dir: Optional[Path] = None) -> Optional[float]:
    runs = load_runs(config_dir)
    return float(runs[-1]["at"]) if runs else None


def facts_for_prompt(facts: Sequence[Fact]) -> str:
    lines = []
    for f in facts:
        flag = "PINNED\t" if f.pinned else ""
        lines.append(f"{f.id}\t{f.day or '?'}\t{flag}{f.text}")
    return "\n".join(lines)


def parse_plan(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return {"merges": [], "contradictions": [], "stale": []}
    try:
        plan = json.loads(text[start : end + 1])
    except Exception:
        return {"merges": [], "contradictions": [], "stale": []}
    if not isinstance(plan, dict):
        return {"merges": [], "contradictions": [], "stale": []}
    plan.setdefault("merges", [])
    plan.setdefault("contradictions", [])
    plan.setdefault("stale", [])
    return plan


def apply_plan(
    store: LocalFactStore, plan: Dict[str, Any], facts: Sequence[Fact]
) -> List[HygieneChange]:
    """Apply the model's plan, defensively: unknown ids and pinned removals
    are ignored, and nothing is removed twice."""
    by_id = {f.id: f for f in facts}
    removed_ids: set[str] = set()
    changes: List[HygieneChange] = []

    def _remove(fid: str, reason: str, change: HygieneChange) -> None:
        fact = by_id.get(fid)
        if fact is None or fact.pinned or fid in removed_ids:
            return
        if store.remove(fid, reason):
            removed_ids.add(fid)
            change.removed.append({"id": fid, "text": fact.text})

    for item in plan.get("merges") or []:
        if not isinstance(item, dict):
            continue
        keep = by_id.get(str(item.get("keep") or ""))
        if keep is None:
            continue
        change = HygieneChange(kind="merge", kept_id=keep.id, kept_text=keep.text)
        new_text = item.get("text")
        if (
            isinstance(new_text, str)
            and new_text.strip()
            and new_text.strip() != keep.text
        ):
            updated = store.update(keep.id, text=new_text.strip())
            if updated is not None:
                change.kept_text = updated.text
        for fid in item.get("remove") or []:
            if str(fid) != keep.id:
                _remove(str(fid), "hygiene: duplicate", change)
        if change.removed or change.kept_text != keep.text:
            changes.append(change)

    for item in plan.get("contradictions") or []:
        if not isinstance(item, dict):
            continue
        keep = by_id.get(str(item.get("keep") or ""))
        if keep is None:
            continue
        change = HygieneChange(
            kind="contradiction", kept_id=keep.id, kept_text=keep.text
        )
        for fid in item.get("remove") or []:
            if str(fid) != keep.id:
                _remove(str(fid), "hygiene: contradicted by a newer fact", change)
        if change.removed:
            changes.append(change)

    stale = HygieneChange(kind="stale")
    for fid in plan.get("stale") or []:
        _remove(str(fid), "hygiene: only true on the day it was written", stale)
    if stale.removed:
        changes.append(stale)
    return changes


def run_hygiene(
    store: LocalFactStore,
    engine: Any,
    model: str,
    *,
    config_dir: Optional[Path] = None,
    restore_window_days: int = 7,
) -> HygieneRun:
    """One clean-up pass. Never raises: a failed run is logged as such."""
    from openjarvis.core.types import Message, Role

    facts = store.list()
    before = len(facts)
    changes: List[HygieneChange] = []
    error = ""
    if facts:
        try:
            result = engine.generate(
                [
                    Message(role=Role.SYSTEM, content=SYSTEM_PROMPT),
                    Message(
                        role=Role.USER,
                        content=f"Facts (id, day, text):\n{facts_for_prompt(facts)}",
                    ),
                ],
                model=model,
                temperature=0.0,
                # A reasoning model thinks before it writes; 4,000 tokens
                # were spent entirely on thinking over 400 facts and the
                # reply came back empty, which read as "nothing to do".
                max_tokens=24000,
            )
            plan = parse_plan(str(result.get("content") or ""))
            changes = apply_plan(store, plan, facts)
        except Exception as exc:  # noqa: BLE001 — a failed clean-up is not a lost memory
            logger.warning("Memory hygiene failed: %s", exc)
            error = str(exc)
    try:
        store.purge_removed(restore_window_days * 86400)
    except Exception:
        logger.debug("Purge of old removals failed", exc_info=True)
    run = HygieneRun(
        at=time.time(),
        facts_before=before,
        facts_after=store.count(),
        changes=changes,
        model=model,
        error=error,
    )
    _append_run(run, config_dir)
    return run


__all__ = [
    "HygieneChange",
    "HygieneRun",
    "apply_plan",
    "facts_for_prompt",
    "last_run_at",
    "load_runs",
    "parse_plan",
    "run_hygiene",
]
