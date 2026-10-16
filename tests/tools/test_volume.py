"""volume: Spotify while it plays, the PC's master volume otherwise.

"Turn it up", "set the volume to 40%" and "turn it down a bit" all failed in
the 16 October wide test (a04, m09, f03), and f03 paused Spotify instead.
"""

from __future__ import annotations

import httpx
import pytest

from openjarvis.tools import volume
from openjarvis.tools.volume import STEP, VolumeTool


class _FakeMaster:
    def __init__(self, level=40, muted=False):
        self._level = level
        self._muted = muted
        self.sets = []

    def level(self):
        return self._level

    def muted(self):
        return self._muted

    def set_level(self, level):
        self.sets.append(level)
        self._level = level

    def set_mute(self, muted):
        self._muted = muted


@pytest.fixture
def spotify(tmp_path, monkeypatch):
    """A connected Spotify whose player state each test sets."""
    token = tmp_path / "spotify.json"
    token.write_text("{}")
    state = {"player": {}, "puts": []}

    def request(_token, method, endpoint, *, params=None, json_body=None):
        if method == "GET" and endpoint == "me/player":
            return state["player"]
        if method == "PUT" and endpoint == "me/player/volume":
            state["puts"].append(params)
            return {}
        raise AssertionError(f"unexpected {method} {endpoint}")

    from openjarvis.connectors import spotify_auth

    monkeypatch.setattr(volume, "_request", request)
    monkeypatch.setattr(spotify_auth, "call_with_refresh", lambda fn, _path: fn("t"))
    state["token"] = str(token)
    return state


def _playing(level=60, playing=True):
    return {
        "is_playing": playing,
        "device": {"id": "d1", "name": "DESKTOP", "volume_percent": level},
    }


def _tool(spotify, master):
    return VolumeTool(token_path=spotify["token"], master_factory=lambda: master)


def test_turn_it_up_while_spotify_plays_moves_spotify_a_step(spotify):
    spotify["player"] = _playing(60)
    master = _FakeMaster(40)
    result = _tool(spotify, master).execute(action="up")
    assert result.success, result.content
    assert spotify["puts"] == [{"volume_percent": 60 + STEP, "device_id": "d1"}]
    assert master.sets == []
    assert result.metadata == {"target": "spotify", "level": 70}


def test_with_nothing_on_spotify_auto_means_the_pc(spotify):
    spotify["player"] = _playing(60, playing=False)
    master = _FakeMaster(40)
    result = _tool(spotify, master).execute(action="down")
    assert result.success
    assert master.sets == [40 - STEP]
    assert spotify["puts"] == []


def test_set_takes_a_level_and_clamps_it(spotify):
    spotify["player"] = _playing(60)
    tool = _tool(spotify, _FakeMaster())
    tool.execute(action="set", percent=40)
    tool.execute(action="set", percent=140)
    assert [p["volume_percent"] for p in spotify["puts"]] == [40, 100]


def test_set_without_a_level_is_refused(spotify):
    assert not _tool(spotify, _FakeMaster()).execute(action="set").success


def test_naming_the_pc_wins_even_while_spotify_plays(spotify):
    spotify["player"] = _playing(60)
    master = _FakeMaster(30)
    _tool(spotify, master).execute(action="up", percent=25, target="pc")
    assert master.sets == [55]
    assert spotify["puts"] == []


def test_naming_spotify_when_it_has_no_device_says_so(spotify):
    spotify["player"] = {}
    master = _FakeMaster()
    result = _tool(spotify, master).execute(action="up", target="spotify")
    assert not result.success
    assert "isn't playing" in result.content
    assert master.sets == []


def test_turning_up_a_muted_pc_unmutes_it(spotify):
    master = _FakeMaster(20, muted=True)
    _tool(spotify, master).execute(action="up", target="pc")
    assert master.sets == [30]
    assert master.muted() is False


def test_a_spotify_error_falls_back_to_the_pc_on_auto(spotify, monkeypatch):
    def broken(*_a, **_k):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(volume, "_request", broken)
    master = _FakeMaster(50)
    result = _tool(spotify, master).execute(action="down")
    assert result.success
    assert master.sets == [40]


def test_status_reports_without_changing_anything(spotify):
    spotify["player"] = _playing(35)
    result = _tool(spotify, _FakeMaster()).execute(action="status")
    assert "35%" in result.content
    assert spotify["puts"] == []


def test_the_description_steers_away_from_pausing():
    description = VolumeTool().spec.description
    assert "turn it down a bit" in description
    assert "Never pause" in description
    # Live probe, 16 October: a bare "Stop." muted the PC (x11), and "play
    # Ben&Ben and set the volume to 40%" set the PC, not Spotify (m09).
    assert "a bare 'stop' or 'pause' is media_pause, never mute" in description
    assert "also plays something on Spotify" in description
