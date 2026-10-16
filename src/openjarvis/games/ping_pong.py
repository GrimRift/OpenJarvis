"""Ping pong: a real-time paddle game the app plays; this keeps the score.

The rallies run in the app's game panel (the user's choice, 16 October: a
top-down paddle game, mouse moves the paddle). Each point is reported here
(``/v1/games/pong/point``), so the score, the serve and the result are code,
and "what's the score" or "pause" by voice reads and changes the same game.

Two modes (the user's words: "best of 11 points or a unli point set"):

* ``eleven``: first to 11, win by 2 (table tennis rules); the serve changes
  every 2 points, every point from 10-10.
* ``unlimited``: no end until the user stops; the best rally and the best
  score are kept across games and Sage says when one is beaten.

Sage talks at the big moments only (the user's choice): game point, deuce,
a long rally, a record, the end. Each point's line is ``""`` otherwise.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from openjarvis.games.base import DIFFICULTIES, Game, MoveError, Outcome

MODES = ("eleven", "unlimited")
TARGET = 11
#: A rally this long (hits by both) gets a word from Sage.
LONG_RALLY = 12

_UNLIMITED_WORDS = re.compile(
    r"\b(unli\w*|unlimited|endless|no\s+limit|forever|infinite)\b", re.IGNORECASE
)

_records_lock = threading.Lock()
_records_override: Optional[Path] = None


def asked_mode(text: str) -> str:
    """ "unlimited" if *text* asks for the endless game, else ""."""
    return "unlimited" if _UNLIMITED_WORDS.search(str(text or "")) else ""


class PingPong(Game):
    name = "ping pong"
    aliases = ("ping pong", "pingpong", "ping-pong", "pong", "table tennis")
    verb = "play"
    move_help = "'pause', 'resume' or 'what's the score'"
    #: Played in the app, not move by move: the tool takes commands instead.
    realtime = True
    difficulties = (*DIFFICULTIES, "adapt")

    def new_state(self, user_first: bool, mode: str = "eleven") -> Dict[str, Any]:
        return {
            "mode": mode if mode in MODES else "eleven",
            "user": "you",
            "sage": "Sage",
            "user_score": 0,
            "sage_score": 0,
            "server": "user",
            "turn": "user",
            "paused": False,
            "stopped": False,
            "best_rally": 0,
        }

    # -- points -------------------------------------------------------------

    def point(self, state: Dict[str, Any], winner: str, rally: int) -> str:
        """Score one point; the line Sage says about it, or ""."""
        if self.outcome(state).over:
            raise MoveError("That game is finished.")
        if winner not in ("user", "sage"):
            raise MoveError("A point goes to the user or to Sage.")
        rally = max(0, int(rally or 0))
        state[f"{winner}_score"] += 1
        state["best_rally"] = max(int(state.get("best_rally") or 0), rally)
        you, me = state["user_score"], state["sage_score"]
        played = you + me
        deuce = state["mode"] == "eleven" and you >= TARGET - 1 and me >= TARGET - 1
        if deuce or played % 2 == 0:
            state["server"] = "sage" if state["server"] == "user" else "user"

        lines = []
        record = _records().get(self.name, {})
        if rally > int(record.get("best_rally") or 0) and rally >= LONG_RALLY:
            lines.append(f"A {rally}-hit rally, Sir: your best yet.")
        elif rally >= LONG_RALLY:
            lines.append(f"{rally} hits, what a rally.")
        best = int(record.get("best_score") or 0)
        passed = winner == "user" and best and you == best + 1
        if state["mode"] == "unlimited" and passed:
            # Said once, as the old best is passed, not on every point after.
            lines.append(f"{you} points: a new best, Sir.")
        result = self.outcome(state)
        if result.over:
            lines.append(self._ending(state, result))
        elif state["mode"] == "eleven":
            lead = abs(you - me)
            leader = "user" if you > me else "sage"
            if deuce and you == me:
                lines.append(f"Deuce, {you} all.")
            elif max(you, me) >= TARGET - 1 and lead == 1:
                lines.append(
                    "Game point to you, Sir."
                    if leader == "user"
                    else "Game point to me."
                )
        _keep_records(self.name, state, rally)
        return " ".join(lines)

    def _ending(self, state: Dict[str, Any], result: Outcome) -> str:
        score = f"{state['user_score']}-{state['sage_score']}"
        if result.winner == "user":
            return f"Game to you, {score}. Well played, Sir."
        return f"Game to me, {score}."

    # -- commands (by voice or chat) -------------------------------------------

    def command(self, state: Dict[str, Any], text: str) -> str:
        """"pause", "resume" or "score": what Sage says back."""
        low = str(text or "").lower()
        if re.search(r"\b(pause|hold on|wait|time\s*out)\b", low):
            state["paused"] = True
            return "Paused, Sir. Say resume when you're ready."
        if re.search(r"\b(resume|continue|go on|unpause|carry on|play on)\b", low):
            state["paused"] = False
            return "Resuming. Your serve." if state["server"] == "user" else "Resuming."
        if re.search(r"\b(score|winning|points?)\b", low):
            return self.score_line(state)
        raise MoveError(
            "In ping pong I can pause, resume or tell you the score, Sir."
        )

    def score_line(self, state: Dict[str, Any]) -> str:
        you, me = state["user_score"], state["sage_score"]
        if you == me:
            return f"{you} all, Sir."
        lead = "You lead" if you > me else "I lead"
        return f"{lead} {max(you, me)}-{min(you, me)}, Sir."

    def final_words(self, state: Dict[str, Any]) -> str:
        """Said when the user stops: the score and the best rally."""
        line = f"Final score {state['user_score']}-{state['sage_score']}."
        if state.get("best_rally"):
            line += f" Longest rally: {state['best_rally']} hits."
        return line

    # -- the Game interface --------------------------------------------------

    def parse_move(self, state: Dict[str, Any], text: str) -> Any:
        raise MoveError("Ping pong is played in the game panel, Sir.")

    def apply(self, state: Dict[str, Any], move: Any, by: str) -> None:
        raise MoveError("Ping pong is played in the game panel, Sir.")

    def choose_move(self, state: Dict[str, Any], difficulty: str) -> Any:
        raise MoveError("Ping pong is played in the game panel, Sir.")

    def describe_move(self, move: Any, state: Optional[Dict[str, Any]] = None) -> str:
        return ""

    def render(self, state: Dict[str, Any]) -> str:
        mode = "first to 11" if state["mode"] == "eleven" else "unlimited"
        return (
            f"You {state['user_score']} - {state['sage_score']} Sage ({mode},"
            f" {'your' if state['server'] == 'user' else 'my'} serve)"
        )

    def outcome(self, state: Dict[str, Any]) -> Outcome:
        you, me = state["user_score"], state["sage_score"]
        if state["mode"] == "eleven" and max(you, me) >= TARGET and abs(you - me) >= 2:
            winner = "user" if you > me else "sage"
            return Outcome(True, winner, f"{max(you, me)}-{min(you, me)}.")
        if state.get("stopped"):
            winner = "user" if you > me else "sage" if me > you else ""
            return Outcome(True, winner, self.final_words(state))
        return Outcome()

    def snapshot(self, state: Dict[str, Any]) -> Dict[str, Any]:
        record = _records().get(self.name, {})
        return {
            "mode": state["mode"],
            "user_score": state["user_score"],
            "sage_score": state["sage_score"],
            "server": state["server"],
            "paused": bool(state.get("paused")),
            "best_rally": int(state.get("best_rally") or 0),
            "record_rally": int(record.get("best_rally") or 0),
            "record_score": int(record.get("best_score") or 0),
        }


# -- records (best rally, best unlimited score) ---------------------------------


def _records_path() -> Path:
    if _records_override is not None:
        return _records_override
    from openjarvis.core.config import DEFAULT_CONFIG_DIR

    return Path(DEFAULT_CONFIG_DIR) / "games" / "records.json"


def _records() -> Dict[str, Dict[str, Any]]:
    with _records_lock:
        try:
            data = json.loads(_records_path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}


def _keep_records(game: str, state: Dict[str, Any], rally: int) -> None:
    records = _records()
    mine = dict(records.get(game) or {})
    changed = False
    if rally > int(mine.get("best_rally") or 0):
        mine["best_rally"] = rally
        changed = True
    if state["mode"] == "unlimited" and state["user_score"] > int(
        mine.get("best_score") or 0
    ):
        mine["best_score"] = state["user_score"]
        changed = True
    if not changed:
        return
    records[game] = mine
    with _records_lock:
        path = _records_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(records), encoding="utf-8")


def use_records_path_for_tests(path: Optional[Path]) -> None:
    global _records_override
    _records_override = path


__all__ = ["MODES", "PingPong", "asked_mode", "use_records_path_for_tests"]
