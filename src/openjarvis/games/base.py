"""What every game Sage can play provides.

Sage used to play from memory: on 9 October a tic-tac-toe game searched the
user's notes before every move ("current tic tac toe board"), took 5-18 s a
move, played a square it never mentioned, lost track of the board and
restarted with the pieces moved. The game itself now lives here: the board,
the rules, Sage's own move and the result are code; the model only talks.

A game is a small class with JSON-serialisable state, so a game survives a
server restart and any board game fits the same tool (``tools/play_game.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

DIFFICULTIES = ("easy", "normal", "hard")


class MoveError(ValueError):
    """A move the game will not accept; the message says why, for the user."""


@dataclass
class Outcome:
    """How the game stands after a move."""

    over: bool = False
    #: "user", "sage" or "" (a draw, or not over).
    winner: str = ""
    #: One line for the model to say, e.g. "Three in a row on the diagonal."
    detail: str = ""


class Game:
    """One kind of game. Subclasses set ``name``/``aliases`` and implement
    the methods below; state is a plain dict so it can be saved as JSON."""

    name = ""
    aliases: tuple = ()
    #: How a move is written, for the tool description and error messages.
    move_help = ""

    def new_state(self, user_first: bool) -> Dict[str, Any]:
        raise NotImplementedError

    def parse_move(self, state: Dict[str, Any], text: str) -> Any:
        """The user's move from what they typed or said; MoveError if not legal."""
        raise NotImplementedError

    def apply(self, state: Dict[str, Any], move: Any, by: str) -> None:
        raise NotImplementedError

    def choose_move(self, state: Dict[str, Any], difficulty: str) -> Any:
        raise NotImplementedError

    def describe_move(self, move: Any) -> str:
        """The move in words, as Sage says it ("the top-left corner (1)")."""
        raise NotImplementedError

    def render(self, state: Dict[str, Any]) -> str:
        raise NotImplementedError

    def outcome(self, state: Dict[str, Any]) -> Outcome:
        raise NotImplementedError

    def legal_moves_text(self, state: Dict[str, Any]) -> str:
        """The moves open to the user, briefly ("2, 4, 6, 7, 8")."""
        return ""


@dataclass
class Session:
    """The game being played: which game, its state, and how hard."""

    game: str
    state: Dict[str, Any]
    difficulty: str = "normal"
    user_symbol: str = ""
    moves: Optional[List[str]] = None

    def to_json(self) -> Dict[str, Any]:
        return {
            "game": self.game,
            "state": self.state,
            "difficulty": self.difficulty,
            "user_symbol": self.user_symbol,
            "moves": self.moves or [],
        }

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "Session":
        return cls(
            game=str(data["game"]),
            state=dict(data["state"]),
            difficulty=str(data.get("difficulty") or "normal"),
            user_symbol=str(data.get("user_symbol") or ""),
            moves=list(data.get("moves") or []),
        )


__all__ = ["DIFFICULTIES", "Game", "MoveError", "Outcome", "Session"]
