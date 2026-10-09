"""Games Sage can play, and the game in progress.

One game at a time (Sage has one user). It is kept on disk, so a restart in
the middle of a game does not lose the board. Adding a game is a class in
this package and one line in ``GAMES``.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from openjarvis.games.base import DIFFICULTIES, Game, MoveError, Outcome, Session
from openjarvis.games.tictactoe import TicTacToe

_ALL: list = [TicTacToe()]
try:
    from openjarvis.games.chess_game import ChessGame

    _ALL.append(ChessGame())
except ImportError:  # python-chess not installed: chess is simply not offered
    pass

GAMES: Dict[str, Game] = {game.name: game for game in _ALL}

_lock = threading.Lock()
_path_override: Optional[Path] = None


def find_game(name: str) -> Optional[Game]:
    """The game called *name* ("tic tac toe", "tictactoe", ...), or None."""
    key = " ".join(str(name or "").lower().replace("-", " ").split())
    for game in GAMES.values():
        names = {
            game.name.replace("-", " "),
            *(a.replace("-", " ") for a in game.aliases),
        }
        if key in names or key.replace(" ", "") in {n.replace(" ", "") for n in names}:
            return game
    return None


def _path() -> Path:
    if _path_override is not None:
        return _path_override
    from openjarvis.core.config import DEFAULT_CONFIG_DIR

    return Path(DEFAULT_CONFIG_DIR) / "games" / "current.json"


def load() -> Optional[Session]:
    with _lock:
        try:
            return Session.from_json(json.loads(_path().read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError):
            return None


def save(session: Optional[Session]) -> None:
    with _lock:
        path = _path()
        if session is None:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(session.to_json()), encoding="utf-8")


#: A game left this long is set aside for routing: short messages get the
#: memory search again. The board stays saved; "let's continue the game"
#: brings the tool back.
IDLE_SECONDS = 30 * 60


def in_progress() -> bool:
    """Whether a game is waiting for the user's move (for tool routing)."""
    try:
        if time.time() - _path().stat().st_mtime > IDLE_SECONDS:
            return False
    except OSError:
        return False
    session = load()
    if session is None or session.game not in GAMES:
        return False
    return not GAMES[session.game].outcome(session.state).over


def use_path_for_tests(path: Optional[Path]) -> None:
    global _path_override
    _path_override = path


__all__ = [
    "DIFFICULTIES",
    "GAMES",
    "Game",
    "MoveError",
    "Outcome",
    "Session",
    "find_game",
    "in_progress",
    "load",
    "save",
]
