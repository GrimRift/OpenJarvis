"""Chess (the user's choice, 9 October: python-chess, casual club strength).

The rules -- castling, en passant, promotion, check, mate, the draw rules --
are python-chess's. Sage's move is a small alpha-beta search over material
and piece placement: easy looks one move ahead (and slips), normal two, hard
three, each capped at a couple of seconds so a reply never stalls.

Moves are taken the way people type or say them: "e4", "Nf3", "O-O",
"e2e4", "knight to f3", "pawn e four", "bishop takes c6", "castle kingside".
"""

from __future__ import annotations

import random
import re
import time
from typing import Any, Dict, List, Optional

import chess

from openjarvis.games.base import Game, MoveError, Outcome

_PIECE_WORDS = {
    "pawn": chess.PAWN,
    "knight": chess.KNIGHT,
    "horse": chess.KNIGHT,
    "bishop": chess.BISHOP,
    "rook": chess.ROOK,
    "castle piece": chess.ROOK,
    "queen": chess.QUEEN,
    "king": chess.KING,
}
_PIECE_NAMES = {
    chess.PAWN: "pawn",
    chess.KNIGHT: "knight",
    chess.BISHOP: "bishop",
    chess.ROOK: "rook",
    chess.QUEEN: "queen",
    chess.KING: "king",
}
_NUMBER_WORDS = {
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "for": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
}
_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}
#: How far ahead each difficulty looks, and the most it may think (seconds).
DEPTHS = {"easy": 1, "normal": 2, "hard": 3}
THINK_SECONDS = 2.5
#: How often "easy" plays a random legal move instead.
EASY_SLIP = 0.3
MATE = 100_000


class ChessGame(Game):
    name = "chess"
    aliases = ("chess", "a game of chess")
    verb = "play"
    move_help = (
        "a move like 'e4', 'Nf3', 'O-O', or in words: 'knight to f3', 'castle kingside'"
    )

    def new_state(self, user_first: bool) -> Dict[str, Any]:
        return {
            "fen": chess.Board().fen(),
            "user": "white" if user_first else "black",
            "sage": "black" if user_first else "white",
            "turn": "user" if user_first else "sage",
            "last": "",
        }

    # -- moves --------------------------------------------------------------

    def parse_move(self, state: Dict[str, Any], text: str) -> str:
        board = chess.Board(state["fen"])
        move = _read_move(board, str(text or ""))
        return move.uci()

    def apply(self, state: Dict[str, Any], move: str, by: str) -> None:
        board = chess.Board(state["fen"])
        played = chess.Move.from_uci(move)
        state["last"] = _in_words(board, played)
        board.push(played)
        state["fen"] = board.fen()
        state["turn"] = "sage" if by == "user" else "user"

    def choose_move(self, state: Dict[str, Any], difficulty: str) -> str:
        board = chess.Board(state["fen"])
        legal = list(board.legal_moves)
        if difficulty == "easy" and random.random() < EASY_SLIP:
            return random.choice(legal).uci()
        return _search(board, DEPTHS.get(difficulty, 2)).uci()

    def describe_move(self, move: Any, state: Optional[Dict[str, Any]] = None) -> str:
        # Written by apply(), which still had the board the move was made on.
        return (state or {}).get("last") or str(move)

    # -- state --------------------------------------------------------------

    def render(self, state: Dict[str, Any]) -> str:
        """The board from the user's side, uppercase = White, '.' = empty."""
        board = chess.Board(state["fen"])
        white_below = state.get("user", "white") == "white"
        ranks = range(7, -1, -1) if white_below else range(8)
        files = range(8) if white_below else range(7, -1, -1)
        letters = "  " + " ".join(chess.FILE_NAMES[f] for f in files)
        rows = [letters]
        for rank in ranks:
            cells = []
            for file in files:
                piece = board.piece_at(chess.square(file, rank))
                cells.append(piece.symbol() if piece else ".")
            rows.append(f"{rank + 1} {' '.join(cells)} {rank + 1}")
        rows.append(letters)
        rows.append("(uppercase = White, lowercase = Black)")
        return "\n".join(rows)

    def outcome(self, state: Dict[str, Any]) -> Outcome:
        board = chess.Board(state["fen"])
        if board.is_checkmate():
            # The side to move is mated; the other side won.
            winner_colour = "black" if board.turn == chess.WHITE else "white"
            winner = "user" if state.get("user") == winner_colour else "sage"
            return Outcome(True, winner, "Checkmate.")
        for test, detail in (
            (board.is_stalemate, "Stalemate: no legal move, not in check."),
            (board.is_insufficient_material, "Not enough pieces left to mate."),
            (board.is_seventyfive_moves, "Seventy-five moves without a capture."),
            (board.is_fivefold_repetition, "The same position five times."),
            (board.can_claim_threefold_repetition, "The same position three times."),
            (
                board.can_claim_fifty_moves,
                "Fifty moves without a capture or pawn move.",
            ),
        ):
            if test():
                return Outcome(True, "", detail)
        return Outcome()

    def status_note(self, state: Dict[str, Any]) -> str:
        board = chess.Board(state["fen"])
        return "Check!" if board.is_check() and not board.is_checkmate() else ""


# -- reading a move ----------------------------------------------------------


def _read_move(board: chess.Board, text: str) -> chess.Move:
    raw = text.strip()
    low = raw.lower()
    # Written notation first: "e4", "Nf3", "O-O", "exd5", "e8=Q", "e2e4".
    named_piece = any(re.search(rf"\b{word}\b", low) for word in _PIECE_WORDS)
    for token in re.findall(r"[A-Za-z0-9=+#\-]+", raw):
        token = token.rstrip("+#")
        if named_piece and re.fullmatch(r"[a-h][1-8]", token, re.IGNORECASE):
            # "knight to f3": f3 is where the knight goes, not the pawn move f3.
            continue
        for parse in (board.parse_san, _parse_uci(board)):
            try:
                move = parse(token)
            except (ValueError, AssertionError):
                continue
            if move in board.legal_moves:
                return move
    if re.search(r"\bcastl\w*|\bo-o\b", low):
        long_side = bool(re.search(r"\b(queen\s*side|long|queenside)\b", low))
        short_side = bool(re.search(r"\b(king\s*side|short|kingside)\b", low))
        options = [m for m in board.legal_moves if board.is_castling(m)]
        if long_side:
            options = [m for m in options if board.is_queenside_castling(m)]
        elif short_side:
            options = [m for m in options if board.is_kingside_castling(m)]
        if len(options) == 1:
            return options[0]
        raise MoveError(
            "Castling isn't possible here."
            if not options
            else "Castle which way, kingside or queenside?"
        )
    spoken = re.sub(
        r"\b([a-h])\s*(one|two|three|four|for|five|six|seven|eight)\b",
        lambda m: m.group(1) + _NUMBER_WORDS[m.group(2)],
        low,
    )
    squares = re.findall(r"\b([a-h])\s*([1-8])\b", spoken)
    squares = [chess.parse_square(f + r) for f, r in squares]
    if len(squares) >= 2:
        move = chess.Move(squares[-2], squares[-1])
        for candidate in board.legal_moves:
            if (
                candidate.from_square == move.from_square
                and candidate.to_square == move.to_square
            ):
                return _prefer_queen(board, candidate)
    if squares:
        target = squares[-1]
        piece = next(
            (
                kind
                for word, kind in _PIECE_WORDS.items()
                if re.search(rf"\b{word}\b", spoken)
            ),
            None,
        )
        options = [
            m
            for m in board.legal_moves
            if m.to_square == target
            and (piece is None or board.piece_type_at(m.from_square) == piece)
        ]
        options = _without_minor_promotions(options)
        if len(options) == 1:
            return options[0]
        if len(options) > 1:
            names = ", ".join(sorted({board.san(m) for m in options}))
            raise MoveError(f"More than one piece can go there: {names}. Which one?")
    sample = ", ".join(sorted(board.san(m) for m in board.legal_moves)[:12])
    raise MoveError(
        f"That isn't a legal move here. Say a move like 'e4' or 'knight to f3'."
        f" Some legal moves: {sample}."
    )


def _parse_uci(board: chess.Board):
    def parse(token: str) -> chess.Move:
        return chess.Move.from_uci(token.lower())

    return parse


def _without_minor_promotions(moves: List[chess.Move]) -> List[chess.Move]:
    """A pawn reaching the last rank becomes a queen unless the user says so."""
    queens = [m for m in moves if m.promotion in (None, chess.QUEEN)]
    return queens or moves


def _prefer_queen(board: chess.Board, move: chess.Move) -> chess.Move:
    if move.promotion and move.promotion != chess.QUEEN:
        queen = chess.Move(move.from_square, move.to_square, chess.QUEEN)
        if queen in board.legal_moves:
            return queen
    return move


def _in_words(board: chess.Board, move: chess.Move) -> str:
    """'knight to f3 (Nf3)', 'bishop takes e5 (Bxe5)', 'castles kingside (O-O)'."""
    san = board.san(move)
    if board.is_kingside_castling(move):
        return f"castles kingside ({san})"
    if board.is_queenside_castling(move):
        return f"castles queenside ({san})"
    piece = _PIECE_NAMES[board.piece_type_at(move.from_square) or chess.PAWN]
    action = "takes" if board.is_capture(move) else "to"
    words = f"{piece} {action} {chess.square_name(move.to_square)}"
    if move.promotion:
        words += f", promoting to a {_PIECE_NAMES[move.promotion]}"
    return f"{words} ({san})"


# -- choosing a move ---------------------------------------------------------

# Small bonuses for well-placed pieces (from White's side; mirrored for Black):
# central knights and pawns, an advanced pawn. Enough to play sensibly.
_CENTER = [
    0,
    1,
    2,
    3,
    3,
    2,
    1,
    0,
    1,
    2,
    3,
    4,
    4,
    3,
    2,
    1,
    2,
    3,
    5,
    6,
    6,
    5,
    3,
    2,
    3,
    4,
    6,
    8,
    8,
    6,
    4,
    3,
    3,
    4,
    6,
    8,
    8,
    6,
    4,
    3,
    2,
    3,
    5,
    6,
    6,
    5,
    3,
    2,
    1,
    2,
    3,
    4,
    4,
    3,
    2,
    1,
    0,
    1,
    2,
    3,
    3,
    2,
    1,
    0,
]


def _evaluate(board: chess.Board) -> int:
    """Score for the side to move (positive = good for it)."""
    score = 0
    for square, piece in board.piece_map().items():
        value = _VALUES[piece.piece_type]
        if piece.piece_type in (chess.KNIGHT, chess.BISHOP, chess.PAWN):
            value += _CENTER[square] * (4 if piece.piece_type == chess.KNIGHT else 2)
        if piece.piece_type == chess.PAWN:
            rank = chess.square_rank(square)
            value += (rank if piece.color == chess.WHITE else 7 - rank) * 4
        score += value if piece.color == chess.WHITE else -value
    return score if board.turn == chess.WHITE else -score


def _ordered(board: chess.Board) -> List[chess.Move]:
    """Captures first (most valuable victim, cheapest attacker), then checks."""

    def key(move: chess.Move) -> int:
        if board.is_capture(move):
            victim = board.piece_type_at(move.to_square) or chess.PAWN
            attacker = board.piece_type_at(move.from_square) or chess.PAWN
            return -(10 * _VALUES[victim] - _VALUES[attacker]) - 10_000
        return -1 if board.gives_check(move) else 0

    return sorted(board.legal_moves, key=key)


def _search(board: chess.Board, depth: int) -> chess.Move:
    """Iterative deepening alpha-beta up to *depth*, stopped by THINK_SECONDS."""
    deadline = time.monotonic() + THINK_SECONDS
    best = random.choice(list(board.legal_moves))

    def negamax(depth_left: int, alpha: int, beta: int, ply: int) -> int:
        if board.is_checkmate():
            return -MATE + ply
        if board.is_stalemate() or board.is_insufficient_material():
            return 0
        if depth_left == 0:
            return _quiet(alpha, beta, 2)
        for move in _ordered(board):
            board.push(move)
            score = -negamax(depth_left - 1, -beta, -alpha, ply + 1)
            board.pop()
            if score >= beta:
                return beta
            alpha = max(alpha, score)
            if time.monotonic() > deadline:
                break
        return alpha

    def _quiet(alpha: int, beta: int, left: int) -> int:
        # Follow captures a little way, so a hanging piece is not missed.
        stand = _evaluate(board)
        if stand >= beta or left == 0:
            return stand
        alpha = max(alpha, stand)
        for move in _ordered(board):
            if not board.is_capture(move):
                continue
            board.push(move)
            score = -_quiet(-beta, -alpha, left - 1)
            board.pop()
            if score >= beta:
                return beta
            alpha = max(alpha, score)
        return alpha

    for current in range(1, depth + 1):
        scored = []
        finished = True
        for move in _ordered(board):
            board.push(move)
            scored.append((-negamax(current - 1, -MATE, MATE, 1), move))
            board.pop()
            if time.monotonic() > deadline:
                finished = False
                break
        if not scored or (not finished and current > 1):
            # A deeper look cut short knows less than the shallower one done.
            break
        top = max(score for score, _ in scored)
        # Among moves within a tenth of a pawn of the best, any one: games vary.
        best = random.choice([m for s, m in scored if s >= top - 10])
        if not finished:
            break
    return best


__all__ = ["ChessGame"]
