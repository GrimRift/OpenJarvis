"""Chess with python-chess rules and a small search (9 October)."""

from __future__ import annotations

import random
import time

import chess
import pytest

from openjarvis import games
from openjarvis.games import MoveError
from openjarvis.games.chess_game import ChessGame
from openjarvis.tools.play_game import PlayGameTool


@pytest.fixture(autouse=True)
def _game_file(tmp_path):
    games.use_path_for_tests(tmp_path / "current.json")
    yield
    games.use_path_for_tests(None)


def _state(fen=chess.STARTING_FEN, user="white"):
    return {
        "fen": fen,
        "user": user,
        "sage": "black" if user == "white" else "white",
        "turn": "user",
        "last": "",
    }


class TestReadingMoves:
    @pytest.mark.parametrize(
        "text,uci",
        [
            ("e4", "e2e4"),
            ("Nf3", "g1f3"),
            ("e2e4", "e2e4"),
            ("knight to f3", "g1f3"),
            ("pawn to e four", "e2e4"),
            ("e2 to e4", "e2e4"),
            ("I'll play d4", "d2d4"),
        ],
    )
    def test_written_and_spoken(self, text, uci):
        assert ChessGame().parse_move(_state(), text) == uci

    def test_castling_in_words(self):
        fen = "r3k2r/pppppppp/8/8/8/8/PPPPPPPP/R3K2R w KQkq - 0 1"
        game = ChessGame()
        assert game.parse_move(_state(fen), "castle kingside") == "e1g1"
        assert game.parse_move(_state(fen), "O-O-O") == "e1c1"
        with pytest.raises(MoveError, match="kingside or queenside"):
            game.parse_move(_state(fen), "castle")

    def test_an_illegal_move_lists_some_legal_ones(self):
        with pytest.raises(MoveError, match="isn't a legal move.*Nf3"):
            ChessGame().parse_move(_state(), "e5")

    def test_an_ambiguous_move_asks_which(self):
        fen = "4k3/8/8/8/8/8/4K3/R6R w - - 0 1"
        with pytest.raises(MoveError, match="Rad1, Rhd1"):
            ChessGame().parse_move(_state(fen), "rook to d1")


class TestSagePlays:
    def test_takes_a_free_queen(self):
        # Black to move; White's queen on d5 hangs to the knight on f6.
        fen = "rnbqkb1r/pppppppp/5n2/3Q4/8/8/PPPP1PPP/RNB1KBNR b KQkq - 0 1"
        state = _state(fen, user="white")
        state["turn"] = "sage"
        random.seed(0)
        assert ChessGame().choose_move(state, "normal") == "f6d5"

    def test_finds_mate_in_one(self):
        # White to move: Qh5xf7 is mate (scholar's mate).
        fen = "r1bqkbnr/pppp1ppp/2n5/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 0 1"
        state = _state(fen, user="black")
        state["turn"] = "sage"
        assert ChessGame().choose_move(state, "hard") == "h5f7"

    def test_thinks_within_the_time_cap(self):
        state = _state()
        state["turn"] = "sage"
        started = time.monotonic()
        ChessGame().choose_move(state, "hard")
        assert time.monotonic() - started < 6.0


class TestResults:
    def test_fools_mate_is_a_win_for_black(self):
        game = ChessGame()
        state = _state(user="black")
        for uci, by in (
            ("f2f3", "sage"),
            ("e7e5", "user"),
            ("g2g4", "sage"),
            ("d8h4", "user"),
        ):
            game.apply(state, uci, by)
        result = game.outcome(state)
        assert result.over and result.winner == "user" and result.detail == "Checkmate."
        assert game.describe_move("d8h4", state) == "queen to h4 (Qh4#)"

    def test_the_board_is_drawn_from_the_users_side(self):
        game = ChessGame()
        white = game.render(_state(user="white")).splitlines()
        black = game.render(_state(user="black")).splitlines()
        assert white[1].startswith("8 r n b q k b n r")
        assert black[1].startswith("1 R N B K Q B N R")
        assert "uppercase = White" in white[-1]


def test_a_chess_turn_through_the_tool():
    tool = PlayGameTool()
    start = tool.execute(action="start", game="chess")
    assert "New game of chess, Sir" in start.metadata["say"]
    moved = tool.execute(action="move", move="e4")
    say = moved.metadata["say"]
    assert say.startswith("You play pawn to e4 (e4). I play ")
    assert "Your move." in say
    board = chess.Board(games.load().state["fen"])
    assert board.turn == chess.WHITE and board.fullmove_number == 2
