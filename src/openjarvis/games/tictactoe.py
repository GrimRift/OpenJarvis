"""Tic-tac-toe, played properly.

Squares are numbered as on a phone keypad's top three rows:

    1 | 2 | 3
    4 | 5 | 6
    7 | 8 | 9

The user may say a number ("5", "five") or a place ("center", "top left",
"bottom right corner") -- typed or spoken (the user's choice, 9 October).
Sage's move is minimax: perfect on hard, an occasional slip on normal, mostly
random on easy (the user's choice: normal unless they ask).
"""

from __future__ import annotations

import random
import re
from typing import Any, Dict, List, Optional

from openjarvis.games.base import Game, MoveError, Outcome

LINES = (
    (0, 1, 2),
    (3, 4, 5),
    (6, 7, 8),
    (0, 3, 6),
    (1, 4, 7),
    (2, 5, 8),
    (0, 4, 8),
    (2, 4, 6),
)
_LINE_NAMES = {
    (0, 1, 2): "the top row",
    (3, 4, 5): "the middle row",
    (6, 7, 8): "the bottom row",
    (0, 3, 6): "the left column",
    (1, 4, 7): "the middle column",
    (2, 5, 8): "the right column",
    (0, 4, 8): "the diagonal",
    (2, 4, 6): "the diagonal",
}
SQUARE_NAMES = (
    "the top-left corner",
    "the top middle",
    "the top-right corner",
    "the middle left",
    "the center",
    "the middle right",
    "the bottom-left corner",
    "the bottom middle",
    "the bottom-right corner",
)
_WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    # Tagalog, as the user sometimes speaks it.
    "isa": 1,
    "dalawa": 2,
    "tatlo": 3,
    "apat": 4,
    "lima": 5,
    "anim": 6,
    "pito": 7,
    "walo": 8,
    "siyam": 9,
}
#: How often "normal" plays a random square instead of the best one.
NORMAL_SLIP = 0.25


def _place(text: str) -> Optional[int]:
    """A square from words like "top left" or "center"; 0-based, or None."""
    t = text.lower()
    vertical = (
        "top"
        if re.search(r"\b(top|upper|itaas)\b", t)
        else "bottom"
        if re.search(r"\b(bottom|lower|baba)\b", t)
        else ""
    )
    horizontal = (
        "left"
        if re.search(r"\b(left|kaliwa)\b", t)
        else "right"
        if re.search(r"\b(right|kanan)\b", t)
        else ""
    )
    middle = bool(re.search(r"\b(center|centre|middle|gitna|mid)\b", t))
    if not (vertical or horizontal or middle):
        return None
    row = {"top": 0, "": 1, "bottom": 2}[vertical]
    col = {"left": 0, "": 1, "right": 2}[horizontal]
    if not middle and not vertical and not horizontal:
        return None
    return row * 3 + col


class TicTacToe(Game):
    name = "tic-tac-toe"
    aliases = (
        "tictactoe",
        "tic tac toe",
        "tik tak toe",
        "tic-tac-toe",
        "xo",
        "x and o",
    )
    move_help = (
        "a square 1-9 (1 top-left ... 9 bottom-right) or words like 'center', "
        "'top left'"
    )

    def new_state(self, user_first: bool) -> Dict[str, Any]:
        return {
            "board": [""] * 9,
            "user": "X" if user_first else "O",
            "sage": "O" if user_first else "X",
            "turn": "user" if user_first else "sage",
        }

    # -- moves --------------------------------------------------------------

    def parse_move(self, state: Dict[str, Any], text: str) -> int:
        raw = str(text or "").strip().lower()
        square: Optional[int] = None
        digits = re.findall(r"\b([1-9])\b", raw)
        if digits:
            square = int(digits[-1]) - 1
        else:
            for word, number in _WORD_NUMBERS.items():
                if re.search(rf"\b{word}\b", raw):
                    square = number - 1
                    break
            if square is None:
                square = _place(raw)
        if square is None:
            raise MoveError(f"I didn't catch a square there. Say {self.move_help}.")
        if state["board"][square]:
            taken = "mine" if state["board"][square] == state["sage"] else "yours"
            raise MoveError(
                f"Square {square + 1} ({SQUARE_NAMES[square]}) is already {taken}."
                f" Open: {self.legal_moves_text(state)}."
            )
        return square

    def apply(self, state: Dict[str, Any], move: int, by: str) -> None:
        state["board"][move] = state[by]
        state["turn"] = "sage" if by == "user" else "user"

    def choose_move(self, state: Dict[str, Any], difficulty: str) -> int:
        board = list(state["board"])
        empty = [i for i, v in enumerate(board) if not v]
        me, you = state["sage"], state["user"]
        if difficulty == "easy":
            # Takes a win that is right there half the time; otherwise random.
            win = _winning_square(board, me)
            if win is not None and random.random() < 0.5:
                return win
            return random.choice(empty)
        if difficulty == "normal" and random.random() < NORMAL_SLIP:
            # The slip is never a missed win or a missed block -- that would
            # read as broken, not as beatable.
            win = _winning_square(board, me)
            if win is not None:
                return win
            block = _winning_square(board, you)
            if block is not None:
                return block
            return random.choice(empty)
        return _best_move(board, me, you)

    def describe_move(self, move: int, state: Optional[Dict[str, Any]] = None) -> str:
        return f"{SQUARE_NAMES[move]} ({move + 1})"

    # -- state --------------------------------------------------------------

    def render(self, state: Dict[str, Any]) -> str:
        cells = [v or str(i + 1) for i, v in enumerate(state["board"])]
        rows = [" | ".join(cells[r * 3 : r * 3 + 3]) for r in range(3)]
        return "\n---------\n".join(rows)

    def outcome(self, state: Dict[str, Any]) -> Outcome:
        board = state["board"]
        for line in LINES:
            a, b, c = (board[i] for i in line)
            if a and a == b == c:
                winner = "user" if a == state["user"] else "sage"
                return Outcome(True, winner, f"Three in a row: {_LINE_NAMES[line]}.")
        if all(board):
            return Outcome(True, "", "The board is full: a draw.")
        return Outcome()

    def legal_moves_text(self, state: Dict[str, Any]) -> str:
        return ", ".join(str(i + 1) for i, v in enumerate(state["board"]) if not v)


def _winner(board: List[str]) -> str:
    for a, b, c in LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return ""


def _winning_square(board: List[str], who: str) -> Optional[int]:
    for line in LINES:
        marks = [board[i] for i in line]
        if marks.count(who) == 2 and marks.count("") == 1:
            return line[marks.index("")]
    return None


def _best_move(board: List[str], me: str, you: str) -> int:
    """Perfect play (minimax); among equally good squares, a random one, so
    games do not repeat move for move."""

    def score(b: List[str], turn: str, depth: int) -> int:
        w = _winner(b)
        if w == me:
            return 10 - depth
        if w == you:
            return depth - 10
        if all(b):
            return 0
        results = []
        for i in range(9):
            if not b[i]:
                b[i] = turn
                results.append(score(b, you if turn == me else me, depth + 1))
                b[i] = ""
        return max(results) if turn == me else min(results)

    scored = []
    for i in range(9):
        if not board[i]:
            board[i] = me
            scored.append((score(board, you, 1), i))
            board[i] = ""
    best = max(s for s, _ in scored)
    return random.choice([i for s, i in scored if s == best])


__all__ = ["TicTacToe", "SQUARE_NAMES"]
