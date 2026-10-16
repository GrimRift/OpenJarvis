"""Games Sage can play, and the game in progress.

One game at a time (Sage has one user). It is kept on disk, so a restart in
the middle of a game does not lose the board. Adding a game is a class in
this package and one line in ``GAMES``.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from openjarvis.games.base import DIFFICULTIES, Game, MoveError, Outcome, Session
from openjarvis.games.ping_pong import PingPong
from openjarvis.games.tictactoe import TicTacToe

_ALL: list = [TicTacToe()]
try:
    from openjarvis.games.chess_game import ChessGame

    _ALL.append(ChessGame())
except ImportError:  # python-chess not installed: chess is simply not offered
    pass
_ALL.append(PingPong())

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


_GAME_NAME = (
    r"(?:chess|tic[\s-]?tac[\s-]?toe|tik[\s-]?tak[\s-]?toe|tictactoe|xo"
    r"|x\s+and\s+o|ping[\s-]?pong|pong|table\s+tennis)"
)
_PLAY_WORD = (
    r"(?:play\w*|game|games|match|round|again|start|vs|versus|against"
    r"|with\s+me|laro|maglaro)"
)
#: The user asking for a game, not mentioning games. 10 October: "games on
#: sale that you would recommend" started a chess game over the answer.
ASKS_TO_PLAY = re.compile(
    rf"\b{_GAME_NAME}\b(?:\W+\w+){{0,4}}?\W+{_PLAY_WORD}\b"
    rf"|\b{_PLAY_WORD}\b(?:\W+\w+){{0,4}}?\W+{_GAME_NAME}\b"
    r"|\bplay\w*\b(?:\W+\w+){0,4}?\W+(?:game|round|match)\b"
    r"|\banother\s+(?:game|round|match)\b"
    r"|\brematch\b"
    # The game's own ending says 'Say "play again"' (16 October: refused).
    r"|\bplay\w*\s+(?:it\s+|that\s+)?again\b",
    re.IGNORECASE,
)
_ONLY_A_GAME_NAME = re.compile(
    rf"^\W*(?:\w+\W+){{0,2}}?{_GAME_NAME}\W*$", re.IGNORECASE
)


def asks_to_play(text: str) -> bool:
    """Whether *text* asks for a game ("let's play chess", "tic tac toe?")."""
    text = str(text or "")
    return bool(ASKS_TO_PLAY.search(text) or _ONLY_A_GAME_NAME.search(text))


# -- messages that need no model round ------------------------------------------
#
# The game panel sends what a click did as an ordinary chat message ("Nc3",
# "5", "Undo"), so the conversation, the voice and the board stay one game.
# Read here, whole-message only, it goes straight to the tool: a model round
# added 1-2 s a move and could misread a bare "Nc3". Anything looser ("what
# about e4?") still goes to the model.

_CHESS_MOVE = re.compile(
    r"^(?:O-O(?:-O)?|0-0(?:-0)?|[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=?[QRBNqrbn])?"
    r"|[a-h][1-8][a-h][1-8][qrbn]?)[+#]?$"
)
_SQUARE = re.compile(r"^[1-9]$")
_UNDO = re.compile(r"^(?:undo|take\s+(?:that|it|my\s+move)\s+back|takeback)$", re.I)
_END = re.compile(r"^(?:end\s+(?:the\s+)?game|stop\s+the\s+game|i\s+quit)$", re.I)
#: A bare "stop" ends only ping pong: mid-chess it is more likely meant for
#: the music or for Sage's voice.
_STOP = re.compile(r"^(?:stop|quit)$", re.I)
_LEVEL = re.compile(r"^(?:play|go|difficulty:?)\s+(easy|normal|hard|adapt\w*)$", re.I)
_NEW_GAME = re.compile(
    rf"^new\s+game\s+of\s+({_GAME_NAME})"
    r"(?:\s+as\s+(white|black|random|x|o))?"
    r"(?:,?\s+(first\s+to\s+11|unlimited))?$",
    re.I,
)
_PONG_COMMAND = re.compile(
    r"^(?:pause|resume|continue|unpause|what'?s\s+the\s+score|score)$", re.I
)


def quick_command(text: str) -> Optional[Dict[str, object]]:
    """play_game's arguments for a panel click or a bare move, else None.

    Only while a game is on (``in_progress``), except a "new game of ..."
    from the panel, which starts one."""
    said = " ".join(str(text or "").split()).strip().rstrip(".!?").strip()
    if not said or len(said) > 60:
        return None
    started = _NEW_GAME.match(said)
    if started:
        colour = (started.group(2) or "").lower()
        params: Dict[str, object] = {"action": "start", "game": started.group(1)}
        if colour:
            params["colour"] = colour
        if started.group(3):
            params["mode"] = (
                "unlimited" if "unlimited" in started.group(3).lower() else "eleven"
            )
        return params
    if not in_progress():
        return None
    session = load()
    if session is None:
        return None
    game = GAMES[session.game]
    realtime = bool(getattr(game, "realtime", False))
    if _END.match(said) or (realtime and _STOP.match(said)):
        return {"action": "end"}
    level = _LEVEL.match(said)
    if level:
        wanted = level.group(1).lower()
        wanted = "adapt" if wanted.startswith("adapt") else wanted
        return {"action": "difficulty", "difficulty": wanted}
    if realtime:
        return {"action": "move", "move": said} if _PONG_COMMAND.match(said) else None
    if _UNDO.match(said):
        return {"action": "undo"}
    if game.name == "chess" and _CHESS_MOVE.match(said):
        return {"action": "move", "move": said}
    if game.name == "tic-tac-toe" and _SQUARE.match(said):
        return {"action": "move", "move": said}
    return None


def use_path_for_tests(path: Optional[Path]) -> None:
    global _path_override
    _path_override = path


__all__ = [
    "ASKS_TO_PLAY",
    "DIFFICULTIES",
    "GAMES",
    "Game",
    "MoveError",
    "Outcome",
    "Session",
    "asks_to_play",
    "find_game",
    "in_progress",
    "load",
    "quick_command",
    "save",
]
