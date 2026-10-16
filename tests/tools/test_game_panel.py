"""The app's game panel (16 October): click to move, undo, colour, ping pong.

The panel sends a click as a chat message ("Nc3", "5", "Undo"); those go to
play_game without a model round, and every result carries what the panel
draws (`metadata["game_panel"]`)."""

from __future__ import annotations

import asyncio
import json

import pytest

from openjarvis import games
from openjarvis.games import ping_pong, quick_command
from openjarvis.games.ping_pong import PingPong
from openjarvis.security import page_access
from openjarvis.tools.play_game import PlayGameTool


@pytest.fixture(autouse=True)
def _game_files(tmp_path):
    games.use_path_for_tests(tmp_path / "current.json")
    ping_pong.use_records_path_for_tests(tmp_path / "records.json")
    yield
    games.use_path_for_tests(None)
    ping_pong.use_records_path_for_tests(None)


def _play(**params):
    return PlayGameTool().execute(**params)


def _start(text, **params):
    with page_access.scope(text):
        return _play(action="start", **params)


class TestChessPanel:
    def test_the_panel_gets_the_position_and_the_users_moves(self):
        panel = _start("let's play chess", game="chess").metadata["game_panel"]
        assert panel["game"] == "chess"
        assert panel["user"] == "white" and panel["turn"] == "user"
        assert panel["fen"].startswith("rnbqkbnr/pppppppp")
        assert sorted(panel["legal"]["b1"]) == ["a3", "c3"]
        assert panel["last"] == [] and panel["check"] == ""
        assert panel["id"]

    def test_after_a_move_the_last_move_is_sages_reply(self):
        _start("let's play chess", game="chess")
        panel = _play(action="move", move="e4").metadata["game_panel"]
        assert len(panel["last"]) == 2
        assert panel["last"][0][1] in "78"  # Black's piece moved from its side
        assert panel["can_undo"] is True

    def test_a_checked_king_is_named(self):
        _start("let's play chess", game="chess")
        session = games.load()
        # Black just played Bb4+ (the mockup position).
        session.state["fen"] = (
            "r1bqk2r/pppp1ppp/2n2n2/8/1bBPP3/5N2/PP3PPP/RNBQK2R w KQkq - 1 7"
        )
        games.save(session)
        panel = _play(action="show").metadata["game_panel"]
        assert panel["check"] == "e1"
        assert sorted(panel["legal"]["b1"]) == ["c3", "d2"]

    def test_black_from_the_panel(self):
        panel = _start(
            "New game of chess as black", game="chess", colour="black"
        ).metadata["game_panel"]
        assert panel["user"] == "black" and panel["turn"] == "user"
        assert len(panel["last"]) == 2  # Sage, White, has moved


class TestCaptureFlag:
    """A clicked move is spoken only when a piece is taken, or at the end."""

    def test_a_quiet_move_is_not_a_capture(self):
        _start("let's play chess", game="chess")
        panel = _play(action="move", move="a3").metadata["game_panel"]
        assert panel["capture"] is (
            "takes" in panel["line"]
        )  # Sage's reply may itself take

    def test_taking_a_piece_is_flagged(self):
        _start("let's play chess", game="chess")
        session = games.load()
        # White to move, Black's knight hanging on d5 to the pawn on e4.
        session.state["fen"] = (
            "rnbqkb1r/pppppppp/8/3n4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 3"
        )
        games.save(session)
        panel = _play(action="move", move="exd5").metadata["game_panel"]
        assert panel["capture"] is True

    def test_other_actions_are_not_captures(self):
        _start("let's play chess", game="chess")
        _play(action="move", move="e4")
        panel = _play(action="difficulty", difficulty="hard").metadata[
            "game_panel"
        ]
        assert panel["capture"] is False


class TestUndo:
    def test_undo_takes_back_the_users_move_and_sages_reply(self):
        _start("let's play chess", game="chess")
        before = games.load().state["fen"]
        _play(action="move", move="e4")
        result = _play(action="undo")
        assert result.success
        assert games.load().state["fen"] == before
        assert games.load().moves == []
        assert result.metadata["game_panel"]["can_undo"] is False

    def test_nothing_to_undo(self):
        _start("let's play tic tac toe", game="tic-tac-toe")
        assert not _play(action="undo").success

    def test_tic_tac_toe_undo(self):
        _start("let's play tic tac toe", game="tic-tac-toe")
        _play(action="move", move="5")
        _play(action="undo")
        assert games.load().state["board"] == [""] * 9


class TestQuickCommands:
    def test_bare_moves_mid_game(self):
        _start("let's play chess", game="chess")
        assert quick_command("Nc3") == {"action": "move", "move": "Nc3"}
        assert quick_command("e2e4.") == {"action": "move", "move": "e2e4"}
        assert quick_command("O-O") == {"action": "move", "move": "O-O"}
        assert quick_command("Undo") == {"action": "undo"}
        assert quick_command("Play hard") == {
            "action": "difficulty",
            "difficulty": "hard",
        }

    @pytest.mark.parametrize(
        "text",
        ["what about e4?", "knight to f3", "stop", "5", "tell me a joke"],
    )
    def test_anything_looser_goes_to_the_model(self, text):
        _start("let's play chess", game="chess")
        assert quick_command(text) is None

    def test_nothing_without_a_game(self):
        assert quick_command("e4") is None
        assert quick_command("5") is None

    def test_a_square_in_tic_tac_toe(self):
        _start("let's play tic tac toe", game="tic-tac-toe")
        assert quick_command("5") == {"action": "move", "move": "5"}
        assert quick_command("e4") is None

    def test_a_new_game_from_the_panel(self):
        assert quick_command("New game of chess as black") == {
            "action": "start",
            "game": "chess",
            "colour": "black",
        }
        assert quick_command("New game of ping pong, unlimited") == {
            "action": "start",
            "game": "ping pong",
            "mode": "unlimited",
        }

    def test_bare_stop_ends_only_ping_pong(self):
        _start("let's play ping pong", game="ping pong")
        assert quick_command("stop") == {"action": "end"}
        assert quick_command("pause") == {"action": "move", "move": "pause"}
        assert quick_command("what's the score?") == {
            "action": "move",
            "move": "what's the score",
        }


class TestPingPong:
    def test_starts_from_the_users_words(self):
        assert games.asks_to_play("let's play ping pong")
        assert games.asks_to_play("table tennis game?")
        panel = _start(
            "let's play ping pong, unli", game="ping pong"
        ).metadata["game_panel"]
        assert panel["mode"] == "unlimited"
        assert panel["user_score"] == 0 and panel["server"] == "user"

    @pytest.mark.parametrize(
        "text",
        ["let's play ping pong, unlimited", "table tennis?", "pong game"],
    )
    def test_routed_to_the_game_with_no_game_on(self, text):
        from openjarvis.agents.tool_routing import selected_tool_names

        assert "play_game" in selected_tool_names(text)

    def test_adapt_is_a_ping_pong_level_only(self):
        panel = _start("play ping pong, adaptive", game="ping pong").metadata[
            "game_panel"
        ]
        assert panel["difficulty"] == "adapt"
        panel = _start("let's play chess adaptive", game="chess").metadata[
            "game_panel"
        ]
        assert panel["difficulty"] == "normal"

    def test_first_to_eleven_win_by_two(self):
        game = PingPong()
        state = game.new_state(True)
        for _ in range(10):
            game.point(state, "user", 3)
            game.point(state, "sage", 3)
        assert state["user_score"] == state["sage_score"] == 10
        assert game.point(state, "user", 3) == "Game point to you, Sir."
        assert not game.outcome(state).over  # 11-10 is not enough
        assert game.point(state, "sage", 3) == "Deuce, 11 all."
        game.point(state, "user", 3)
        line = game.point(state, "user", 3)
        assert game.outcome(state).over and game.outcome(state).winner == "user"
        assert line.startswith("Game to you, 13-11")

    def test_the_serve_changes_every_two_then_every_one_at_deuce(self):
        game = PingPong()
        state = game.new_state(True)
        servers = []
        for _ in range(4):
            game.point(state, "user", 1)
            servers.append(state["server"])
        assert servers == ["user", "sage", "sage", "user"]
        state.update(user_score=10, sage_score=9)
        game.point(state, "sage", 1)  # 10-10
        first = state["server"]
        game.point(state, "user", 1)
        assert state["server"] != first

    def test_unlimited_never_ends_until_stopped(self):
        game = PingPong()
        state = game.new_state(True, "unlimited")
        for _ in range(30):
            game.point(state, "user", 1)
        assert not game.outcome(state).over
        state["stopped"] = True
        assert game.outcome(state).over

    def test_records_are_kept_and_beaten_once(self):
        game = PingPong()
        state = game.new_state(True, "unlimited")
        assert "your best yet" in game.point(state, "user", 15)
        assert "your best yet" not in game.point(state, "user", 15)
        game.point(state, "user", 1)
        again = game.new_state(True, "unlimited")
        lines = [game.point(again, "user", 1) for _ in range(4)]
        assert lines[-1] == "4 points: a new best, Sir."
        assert lines.count("4 points: a new best, Sir.") == 1

    def test_voice_commands_and_the_end(self):
        _start("let's play ping pong", game="ping pong")
        assert "Paused" in _play(action="move", move="pause").metadata["say"]
        assert games.load().state["paused"] is True
        PlayGameTool().point("user", 4)
        say = _play(action="move", move="what's the score").metadata["say"]
        assert say.startswith("You lead 1-0")
        end = _play(action="end")
        assert "Final score 1-0" in end.metadata["say"]
        assert end.metadata["game_panel"]["over"] is True
        assert games.load() is None

    def test_a_point_from_the_panel(self):
        _start("let's play ping pong", game="ping pong")
        answer = PlayGameTool().point("sage", 2)
        assert answer["game_panel"]["sage_score"] == 1
        assert answer["line"] == ""


class TestFastPathStream:
    def test_a_move_streams_the_tool_events_and_the_reply(self):
        from openjarvis.server import routes

        _start("let's play chess", game="chess")
        params = routes._game_quick_command("e4")
        result = routes._run_game_command(params)

        async def collect():
            return [
                part
                async for part in routes._game_reply_stream("m", params, result)
            ]

        parts = asyncio.run(collect())
        text = "".join(parts)
        assert "event: tool_call_start" in text and "event: tool_call_end" in text
        end = next(p for p in parts if p.startswith("event: tool_call_end"))
        payload = json.loads(end.split("data: ", 1)[1])
        assert payload["tool"] == "play_game"
        assert payload["metadata"]["game_panel"]["game"] == "chess"
        assert "You play pawn to e4" in text
        assert parts[-1] == "data: [DONE]\n\n"
