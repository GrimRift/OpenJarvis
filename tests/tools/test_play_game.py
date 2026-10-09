"""Games Sage plays: the board and Sage's moves are code (9 October).

The tic-tac-toe chat that prompted this: memory searched before every move
(5-18 s a move), a move played and never mentioned, the board lost, and a
"restart" with the pieces moved."""

from __future__ import annotations

import random

import pytest

from openjarvis import games
from openjarvis.games import MoveError
from openjarvis.games.tictactoe import TicTacToe, _best_move
from openjarvis.security import page_access
from openjarvis.tools.play_game import PlayGameTool


@pytest.fixture(autouse=True)
def _game_file(tmp_path):
    games.use_path_for_tests(tmp_path / "current.json")
    yield
    games.use_path_for_tests(None)


def _play(**params):
    return PlayGameTool().execute(**params)


class TestMovesAsTheUserSaysThem:
    @pytest.mark.parametrize(
        "text,square",
        [
            ("5", 4),
            ("five", 4),
            ("center", 4),
            ("the middle", 4),
            ("top left", 0),
            ("top-left corner", 0),
            ("upper right", 2),
            ("bottom right", 8),
            ("bottom", 7),
            ("left", 3),
            ("middle right", 5),
            ("I'll take 9", 8),
            ("lima", 4),
            ("gitna", 4),
        ],
    )
    def test_numbers_and_places(self, text, square):
        game = TicTacToe()
        assert game.parse_move(game.new_state(True), text) == square

    def test_a_taken_square_is_refused_with_the_open_ones(self):
        game = TicTacToe()
        state = game.new_state(True)
        game.apply(state, 4, "user")
        with pytest.raises(MoveError, match="already yours.*Open: 1, 2, 3, 4, 6"):
            game.parse_move(state, "5")

    def test_nonsense_is_refused(self):
        game = TicTacToe()
        with pytest.raises(MoveError, match="didn't catch a square"):
            game.parse_move(game.new_state(True), "banana")


class TestSagePlaysWell:
    def test_hard_never_loses(self):
        """Every game against every user line: hard wins or draws."""
        game = TicTacToe()

        def explore(state):
            result = game.outcome(state)
            if result.over:
                assert result.winner != "user"
                return
            for square in [i for i, v in enumerate(state["board"]) if not v]:
                branch = {**state, "board": list(state["board"])}
                game.apply(branch, square, "user")
                if not game.outcome(branch).over:
                    game.apply(branch, game.choose_move(branch, "hard"), "sage")
                explore(branch)

        random.seed(1)
        explore(game.new_state(True))

    def test_hard_takes_a_win(self):
        board = ["O", "O", "", "X", "X", "", "", "", ""]
        assert _best_move(board, "O", "X") == 2

    def test_normal_never_misses_a_block(self):
        game = TicTacToe()
        state = game.new_state(True)
        state["board"] = ["X", "X", "", "", "O", "", "", "", ""]
        random.seed(3)
        assert all(game.choose_move(state, "normal") == 2 for _ in range(50))


class TestTheTool:
    def test_a_whole_game_keeps_the_real_board(self):
        random.seed(7)
        start = _play(action="start", game="tic tac toe")
        assert "New game of tic-tac-toe (normal)" in start.content
        assert "1 | 2 | 3" in start.content
        moved = _play(action="move", move="center")
        assert "The user played: the center (5)" in moved.content
        assert "You (Sage) played:" in moved.content
        assert "| X |" in moved.content  # the user's X is on the board
        # The next call reads the same board back from disk.
        shown = _play(action="show")
        assert shown.metadata["board"] == moved.metadata["board"]

    def test_an_illegal_move_leaves_the_board_alone(self):
        _play(action="start", game="tic-tac-toe")
        before = _play(action="move", move="5").metadata["board"]
        refused = _play(action="move", move="5")
        assert refused.success is False and "already yours" in refused.content
        assert refused.metadata["board"] == before

    def test_easy_or_hard_from_the_users_words(self):
        with page_access.scope("let's play tic tac toe, go easy on me"):
            result = _play(action="start", game="tic tac toe")
        assert "(easy)" in result.content
        with page_access.scope("play hard now"):
            changed = _play(action="difficulty")
        assert "Difficulty is now hard" in changed.content

    def test_sage_can_go_first(self):
        result = _play(action="start", game="tic tac toe", user_first=False)
        assert "You (Sage) played:" in result.content
        assert "User is O, you are X" in result.content

    def test_the_end_is_announced(self):
        _play(action="start", game="tic tac toe", difficulty="hard")
        result = None
        for square in ("1", "2", "3", "4", "5", "6", "7", "8", "9"):
            result = _play(action="move", move=square)
            if result.metadata.get("over"):
                break
        assert result is not None and result.metadata["over"] is True
        assert "GAME OVER" in result.content
        assert result.metadata["winner"] != "user"

    def test_an_unknown_game_says_what_it_can_play(self):
        result = _play(action="start", game="chess")
        assert result.success is False
        assert "Games I can play: tic-tac-toe" in result.content

    def test_no_game_no_move(self):
        result = _play(action="move", move="5")
        assert result.success is False and "No game is in progress" in result.content


class TestRouting:
    def _kept(self, text, prior=()):
        from openjarvis.agents.tool_routing import route_tools, routing_text

        schemas = [
            {"type": "function", "function": {"name": n, "parameters": {}}}
            for n in ("play_game", "retrieval", "web_search", "calculator")
        ]
        return {
            s["function"]["name"]
            for s in route_tools(schemas, routing_text(text, prior))
        }

    def test_offered_when_a_game_is_asked_for(self):
        assert "play_game" in self._kept("lets play tik tak toe")

    def test_a_bare_move_mid_game_gets_the_game_and_no_memory_search(self):
        _play(action="start", game="tic tac toe")
        kept = self._kept("5")
        assert "play_game" in kept
        assert "retrieval" not in kept

    def test_not_offered_for_other_talk(self):
        assert "play_game" not in self._kept("what's the capital of Japan?")
