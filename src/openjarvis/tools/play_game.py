"""Play a game with the user: the board and Sage's moves are code, not memory.

See ``openjarvis.games``. On 9 October a tic-tac-toe game searched the user's
notes before every move (5-18 s a move), lost the board and restarted with
the pieces moved. With this tool a move is one call (~2-4 s a turn) and the
board shown is always the real one.
"""

from __future__ import annotations

import re
from typing import Any, List, Optional

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
from openjarvis.tools._stubs import BaseTool, ToolSpec

ACTIONS = ("start", "move", "show", "difficulty", "end")

_ASKED_DIFFICULTY = re.compile(
    r"\b(easy|easier|simple|hard|harder|unbeatable|perfect|expert|normal|medium)\b",
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
    return _DIFFICULTY_WORDS[found.group(1).lower()] if found else ""


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
                " 'difficulty'; 'stop' / 'I quit' -> 'end'. Difficulty is"
                " normal unless the user asks for easy or hard."
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
                    "difficulty": {"type": "string", "enum": list(DIFFICULTIES)},
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
                if wanted not in DIFFICULTIES:
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
            if action == "end":
                save(None)
                return self._say(
                    "The game is over; the board is cleared.",
                    "Game over, Sir. I've cleared the board.",
                )
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
        if difficulty not in DIFFICULTIES:
            difficulty = "normal"
        user_first = params.get("user_first")
        user_first = True if user_first is None else bool(user_first)
        state = game.new_state(user_first)
        session = Session(game=game.name, state=state, difficulty=difficulty, moves=[])
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
        game.apply(state, move, "user")
        session.moves = [*(session.moves or []), f"user:{move}"]
        lines = [f"The user played: {game.describe_move(move, state)}."]
        spoken = f"You {game.verb} {game.describe_move(move, state)}."
        result = game.outcome(state)
        if not result.over:
            reply = game.choose_move(state, session.difficulty)
            game.apply(state, reply, "sage")
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
            },
        )

    def _say(self, text: str, spoken: str, ok: bool = True) -> ToolResult:
        return ToolResult(
            tool_name="play_game",
            content=text,
            success=ok,
            metadata={"say": spoken},
        )


__all__ = ["PlayGameTool"]
