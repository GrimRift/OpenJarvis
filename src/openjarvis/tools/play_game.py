"""Play a game with the user: the board and Sage's moves are code, not memory.

See ``openjarvis.games``. On 9 October a tic-tac-toe game searched the user's
notes before every move (5-18 s a move), lost the board and restarted with
the pieces moved. With this tool a move is one call (~2-4 s a turn) and the
board shown is always the real one.
"""

from __future__ import annotations

import copy
import random
import re
import uuid
from typing import Any, Dict, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.games import (
    DIFFICULTIES,
    GAMES,
    MoveError,
    Session,
    asks_to_play,
    find_game,
    load,
    save,
)
from openjarvis.games.ping_pong import asked_mode
from openjarvis.tools._stubs import BaseTool, ToolSpec

ACTIONS = ("start", "move", "show", "difficulty", "undo", "end")
#: Positions kept for "undo"; a long chess game is ~100 of the user's moves.
HISTORY_LIMIT = 300

_ASKED_DIFFICULTY = re.compile(
    r"\b(easy|easier|simple|hard|harder|unbeatable|perfect|expert|normal|medium"
    r"|adapt\w*)\b",
    re.IGNORECASE,
)
_DIFFICULTY_WORDS = {
    "easy": "easy",
    "easier": "easy",
    "simple": "easy",
    "hard": "hard",
    "harder": "hard",
    "unbeatable": "hard",
    "perfect": "hard",
    "expert": "hard",
    "normal": "normal",
    "medium": "normal",
    "adapt": "adapt",
    "adaptive": "adapt",
}

_HOW_TO_REPLY = (
    "Reply in one or two short sentences: say your move in words, then show"
    " the board EXACTLY as given, in a ```text block. Do not search memory or"
    " notes for the game -- this tool holds it."
)


def _asked_difficulty() -> str:
    """ "easy"/"hard"/"normal" if the user's message asks for one, else ""."""
    from openjarvis.security import page_access

    found = _ASKED_DIFFICULTY.search(page_access.turn_text() or "")
    if not found:
        return ""
    return _DIFFICULTY_WORDS.get(found.group(1).lower(), "adapt")


def _levels(game) -> tuple:
    return tuple(getattr(game, "difficulties", DIFFICULTIES))


@ToolRegistry.register("play_game")
class PlayGameTool(BaseTool):
    """Start a game, make the user's move and Sage's reply, show the board."""

    tool_id = "play_game"
    is_local = True

    def __init__(self, allowed_dirs: Optional[List[str]] = None) -> None:
        super().__init__()

    @property
    def spec(self) -> ToolSpec:
        games = ", ".join(GAMES)
        return ToolSpec(
            name="play_game",
            description=(
                f"Play a game with the user ({games}). The tool keeps the"
                " board, checks moves, plays your move and judges the result;"
                " never keep a game in your head or search memory for it."
                " 'let's play tic tac toe' -> action='start'. While a game is"
                " on, EVERY square or move the user says ('5', 'center', 'top"
                " left', 'e4', 'knight to f3', 'castle') -> action='move'"
                " with their words in `move`. The tool writes the reply; do"
                " not restate or change the moves it reports."
                " 'show the board' -> 'show'; 'play hard' / 'go easy' ->"
                " 'difficulty'; 'take that back' -> 'undo'; 'stop' / 'I quit'"
                " -> 'end'. Difficulty is normal unless the user asks for easy"
                " or hard. Ping pong is played in the app's panel: 'start'"
                " opens it (mode 'eleven' = first to 11, 'unlimited' = no"
                " end); mid-game 'pause' / 'resume' / 'what's the score' ->"
                " action='move' with those words."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(ACTIONS)},
                    "game": {
                        "type": "string",
                        "description": f"For 'start': which game ({games}).",
                    },
                    "move": {
                        "type": "string",
                        "description": "For 'move': the user's move, as they said it.",
                    },
                    "difficulty": {
                        "type": "string",
                        "enum": [*DIFFICULTIES, "adapt"],
                    },
                    "colour": {
                        "type": "string",
                        "enum": ["white", "black", "random", "x", "o"],
                        "description": "For 'start': the side the user plays.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["eleven", "unlimited"],
                        "description": "For 'start' of ping pong.",
                    },
                    "user_first": {
                        "type": "boolean",
                        "description": (
                            "For 'start': false if the user wants Sage to go first."
                        ),
                    },
                },
                "required": ["action"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        action = str(params.get("action") or "").lower()
        # Set by a move that took a piece; the app speaks a clicked move only
        # then, or at the end (the user's choice, 16 October).
        self._captured = False
        try:
            if action == "start":
                return self._start(params)
            session = load()
            if session is None or session.game not in GAMES:
                return self._say(
                    "No game is in progress. Start one with action='start'.",
                    "There's no game going, Sir. Say \"let's play tic-tac-toe\""
                    " to start one.",
                    ok=False,
                )
            if action == "move":
                return self._move(session, str(params.get("move") or ""))
            if action == "difficulty":
                wanted = str(params.get("difficulty") or _asked_difficulty() or "")
                if wanted not in _levels(GAMES[session.game]):
                    return self._board(
                        session,
                        "Say easy, normal or hard.",
                        "Easy, normal or hard, Sir?",
                        ok=False,
                    )
                session.difficulty = wanted
                save(session)
                return self._board(
                    session,
                    f"Difficulty is now {wanted}.",
                    f"Playing {wanted} from now on, Sir.",
                )
            if action == "undo":
                return self._undo(session)
            if action == "end":
                return self._end(session)
            return self._board(session, "", "Here's the board, Sir.")
        except MoveError as error:
            # Raised before anything is saved: the board on disk is unchanged.
            session = load()
            if session is None:
                return self._say(str(error), f"{error}", ok=False)
            return self._board(
                session, f"Not a legal move: {error}", f"{error}", ok=False
            )

    # -- actions ------------------------------------------------------------

    def _start(self, params: dict) -> ToolResult:
        from openjarvis.security import page_access

        asked = page_access.turn_text()
        if asked and not asks_to_play(asked):
            # 10 October: "games on sale that you would recommend" started a
            # chess game, and its board replaced the answer.
            return ToolResult(
                tool_name="play_game",
                content=(
                    "Not started: the user did not ask to play a game. Answer"
                    " their message instead."
                ),
                success=False,
            )
        name = str(params.get("game") or "tic-tac-toe")
        game = find_game(name)
        if game is None:
            known = ", ".join(GAMES)
            return self._say(
                f"I can't play {name} yet. Games I can play: {known}.",
                f"I can't play {name} yet, Sir. I can play {known}.",
                ok=False,
            )
        difficulty = str(params.get("difficulty") or _asked_difficulty() or "normal")
        if difficulty not in _levels(game):
            difficulty = "normal"
        session_id = uuid.uuid4().hex[:12]
        if getattr(game, "realtime", False):
            mode = str(params.get("mode") or asked_mode(asked or "") or "eleven")
            state = game.new_state(True, mode)
            session = Session(
                game=game.name,
                state=state,
                difficulty=difficulty,
                moves=[],
                id=session_id,
            )
            rules = (
                "first to 11, win by two"
                if state["mode"] == "eleven"
                else "no limit, stop when you like"
            )
            save(session)
            return self._board(
                session,
                f"New game of {game.name} ({state['mode']}, {difficulty}) is open"
                " in the app's game panel.",
                f"Ping pong, Sir: {rules}, on {difficulty}. Mouse moves your"
                " paddle; click to serve.",
            )
        user_first = params.get("user_first")
        colour = str(params.get("colour") or "").lower()
        if colour == "random":
            user_first = random.random() < 0.5
        elif colour in ("white", "x"):
            user_first = True
        elif colour in ("black", "o"):
            user_first = False
        user_first = True if user_first is None else bool(user_first)
        state = game.new_state(user_first)
        session = Session(
            game=game.name, state=state, difficulty=difficulty, moves=[], id=session_id
        )
        lines = [f"New game of {game.name} ({difficulty})."]
        spoken = f"New game of {game.name}, Sir, on {difficulty}."
        if user_first:
            spoken += f" You're {state.get('user', '')} and go first."
        else:
            move = game.choose_move(state, difficulty)
            game.apply(state, move, "sage")
            session.moves.append(f"sage:{move}")
            lines.append(f"You (Sage) played: {game.describe_move(move, state)}.")
            spoken += (
                f" I'll go first: I {game.verb} {game.describe_move(move, state)}."
                " Your move."
            )
        save(session)
        return self._board(session, " ".join(lines), spoken)

    def _move(self, session: Session, text: str) -> ToolResult:
        game = GAMES[session.game]
        state = session.state
        if getattr(game, "realtime", False) and not game.outcome(state).over:
            spoken = game.command(state, text)
            save(session)
            return self._board(session, "", spoken)
        if game.outcome(state).over:
            return self._board(
                session,
                "That game is finished. Start a new one with action='start'.",
                'That game\'s finished, Sir. Say "play again" for a new one.',
            )
        if state.get("turn") != "user":
            return self._board(
                session, "It is not the user's turn.", "It's my move, Sir.", ok=False
            )
        move = game.parse_move(state, text)
        before = copy.deepcopy(state)
        game.apply(state, move, "user")
        self._captured = bool(state.get("last_capture"))
        session.history = [*(session.history or []), before][-HISTORY_LIMIT:]
        session.moves = [*(session.moves or []), f"user:{move}"]
        lines = [f"The user played: {game.describe_move(move, state)}."]
        spoken = f"You {game.verb} {game.describe_move(move, state)}."
        result = game.outcome(state)
        if not result.over:
            reply = game.choose_move(state, session.difficulty)
            game.apply(state, reply, "sage")
            self._captured = self._captured or bool(state.get("last_capture"))
            session.moves.append(f"sage:{reply}")
            lines.append(f"You (Sage) played: {game.describe_move(reply, state)}.")
            spoken += f" I {game.verb} {game.describe_move(reply, state)}."
            result = game.outcome(state)
        if result.over:
            ending = {
                "user": "You win, Sir! Well played.",
                "sage": "I win this one, Sir.",
                "": "It's a draw, Sir.",
            }[result.winner]
            spoken += f' {result.detail} {ending} Say "play again" for another.'
        else:
            check = game.status_note(state)
            spoken += f" {check} Your move." if check else " Your move."
        save(session)
        return self._board(session, " ".join(lines), spoken)

    def _undo(self, session: Session) -> ToolResult:
        """Back to before the user's last move (Sage's reply goes too)."""
        if not session.history:
            return self._board(
                session,
                "Nothing to take back.",
                "There's nothing to take back, Sir.",
                ok=False,
            )
        session.state = session.history.pop()
        moves = list(session.moves or [])
        while moves and not moves[-1].startswith("user:"):
            moves.pop()
        session.moves = moves[:-1]
        save(session)
        return self._board(
            session,
            "The user's last move (and your reply) were taken back.",
            "Taken back, Sir. Your move.",
        )

    def _end(self, session: Session) -> ToolResult:
        game = GAMES[session.game]
        if getattr(game, "realtime", False):
            session.state["stopped"] = True
            words = game.final_words(session.state)
            save(None)
            return self._board(session, f"Stopped. {words}", f"Good game, Sir. {words}")
        save(None)
        return self._say(
            "The game is over; the board is cleared.",
            "Game over, Sir. I've cleared the board.",
            ended=session,
        )

    def point(self, winner: str, rally: int) -> Dict[str, Any]:
        """A ping pong point from the app's panel: the panel data and Sage's line.

        Not a model tool action: the panel calls it (server ``/v1/games``)."""
        session = load()
        if session is None or not getattr(GAMES.get(session.game), "realtime", False):
            raise MoveError("No ping pong game is on.")
        game = GAMES[session.game]
        line = game.point(session.state, winner, rally)
        save(session)
        return {"game_panel": self._panel(session, line), "line": line}

    def current(self) -> Optional[Dict[str, Any]]:
        """The game on now, as the panel shows it, or None."""
        session = load()
        if session is None or session.game not in GAMES:
            return None
        return self._panel(session, "")

    # -- output -------------------------------------------------------------

    def _board(
        self, session: Session, note: str, spoken: str, ok: bool = True
    ) -> ToolResult:
        """The result for the model, and `say`: the reply itself.

        The reply is written here, not by the model: on 9 October it said "I
        take the bottom-right corner" over a board showing its O top-right,
        and once wrote its own tool-call text into the chat. A turn whose
        only tool is this one sends `say` as the answer (server/routes.py).
        """
        game = GAMES[session.game]
        state = session.state
        result = game.outcome(state)
        board = game.render(state)
        if result.over:
            who = {"user": "The user wins", "sage": "You (Sage) win", "": "Draw"}
            status = f"GAME OVER -- {who[result.winner]}. {result.detail}"
        else:
            open_moves = game.legal_moves_text(state)
            status = (
                "The user's move." + (f" Open: {open_moves}." if open_moves else "")
                if state.get("turn") == "user"
                else "Your (Sage's) move."
            )
        content = "\n".join(
            part
            for part in (
                f"Game: {game.name}, {session.difficulty}. User is"
                f" {state.get('user', '')}, you are {state.get('sage', '')}.",
                note,
                "Board:",
                "```text",
                board,
                "```",
                status,
                _HOW_TO_REPLY,
            )
            if part
        )
        return ToolResult(
            tool_name="play_game",
            content=content,
            success=ok,
            metadata={
                "game": game.name,
                "board": board,
                "over": result.over,
                "winner": result.winner,
                "say": f"{spoken}\n\n```text\n{board}\n```",
                "game_panel": self._panel(session, spoken),
            },
        )

    def _panel(self, session: Session, spoken: str) -> Dict[str, Any]:
        """What the app's game panel shows (``components/Games``)."""
        game = GAMES[session.game]
        state = session.state
        result = game.outcome(state)
        return {
            "id": session.id,
            "game": game.name,
            "difficulty": session.difficulty,
            "levels": list(_levels(game)),
            "user": state.get("user", ""),
            "sage": state.get("sage", ""),
            "turn": state.get("turn", ""),
            "over": result.over,
            "winner": result.winner,
            "detail": result.detail,
            "note": game.status_note(state) if not result.over else "",
            "line": spoken,
            "can_undo": bool(session.history) and not result.over,
            "capture": bool(getattr(self, "_captured", False)),
            **game.snapshot(state),
        }

    def _say(
        self, text: str, spoken: str, ok: bool = True, ended: Optional[Session] = None
    ) -> ToolResult:
        metadata: Dict[str, Any] = {"say": spoken}
        if ended is not None:
            # The panel shows the game as ended, not as still waiting for a move.
            panel = self._panel(ended, spoken)
            panel.update(over=True, ended=True, can_undo=False, legal={})
            metadata["game_panel"] = panel
        return ToolResult(
            tool_name="play_game",
            content=text,
            success=ok,
            metadata=metadata,
        )


__all__ = ["PlayGameTool"]
