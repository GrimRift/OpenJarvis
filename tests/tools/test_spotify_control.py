"""Tests for the spotify_control and open_app tools."""

from __future__ import annotations

from unittest.mock import patch

import httpx

from openjarvis.core.registry import ToolRegistry


def test_tools_registered():
    # Registered via the decorator on import. Re-registering explicitly makes
    # this independent of whether an earlier test in the session already
    # imported (or cleared) the registry.
    from openjarvis.tools.open_app import OpenAppTool
    from openjarvis.tools.spotify_control import SpotifyControlTool

    for name, cls in (
        ("open_app", OpenAppTool),
        ("spotify_control", SpotifyControlTool),
    ):
        if not ToolRegistry.contains(name):
            ToolRegistry.register_value(name, cls)
        assert ToolRegistry.contains(name)


def test_open_app_rejects_unknown_app():
    """The allowlist is the whole security boundary — nothing else may launch."""
    from openjarvis.tools.open_app import OpenAppTool

    result = OpenAppTool().execute(app="cmd")

    assert not result.success
    assert "cmd" in result.content


def test_open_app_launches_a_resolved_path_as_an_argument_list():
    """No shell, and never the caller's text — only a resolved allowlist path."""
    from openjarvis.tools import open_app
    from openjarvis.tools.open_app import OpenAppTool

    with patch.object(open_app.subprocess, "Popen") as popen:
        with patch.object(
            open_app, "_resolve_target", return_value=r"C:\fake\Spotify.exe"
        ):
            with patch.object(open_app, "is_app_running", return_value=False):
                with patch.object(open_app, "_focus_app_window", return_value=True):
                    result = OpenAppTool().execute(app="spotify")

    assert result.success
    args, kwargs = popen.call_args
    assert args[0] == [r"C:\fake\Spotify.exe"]
    assert kwargs.get("shell") is not True


def test_open_app_focuses_instead_of_launching_a_second_copy():
    """"Open X" when X is already running means show it, not start another."""
    from openjarvis.tools import open_app
    from openjarvis.tools.open_app import OpenAppTool

    with patch.object(open_app.subprocess, "Popen") as popen:
        with patch.object(
            open_app, "_resolve_target", return_value=r"C:\fake\Obsidian.exe"
        ):
            with patch.object(open_app, "is_app_running", return_value=True):
                with patch.object(
                    open_app, "_focus_app_window", return_value=True
                ) as focus:
                    result = OpenAppTool().execute(app="obsidian")

    popen.assert_not_called()
    focus.assert_called_once()
    assert result.success
    assert result.metadata.get("launched") is False


def test_open_app_reports_a_finished_outcome_not_work_in_progress():
    """An in-progress result invited the agent to retry until the loop guard hit.

    Live, "Obsidian is opening." led the model to call open_app again and
    again — roughly fourteen turns for one request — because nothing in the
    result said the work was done.
    """
    from openjarvis.tools import open_app
    from openjarvis.tools.open_app import OpenAppTool

    with patch.object(open_app.subprocess, "Popen"):
        with patch.object(
            open_app, "_resolve_target", return_value=r"C:\fake\Obsidian.exe"
        ):
            with patch.object(open_app, "is_app_running", return_value=False):
                with patch.object(open_app, "_focus_app_window", return_value=True):
                    result = OpenAppTool().execute(app="obsidian")

    assert "opening" not in result.content.lower()
    assert "now open" in result.content.lower()


def test_play_launches_app_when_process_is_not_running():
    """A lingering Connect device must not be mistaken for a live client.

    Spotify keeps a device registered server-side after the desktop client
    exits, so the Web API reports it as active (even ``is_playing``) with no
    window and no audio. Playback that trusts the device list therefore
    "succeeds" while nothing opens — the bug this guards.
    """
    from openjarvis.tools import open_app, spotify_control
    from openjarvis.tools.spotify_control import SpotifyControlTool

    phantom = "phantom-device-id"

    with patch.object(spotify_control, "_pick_device_id", return_value=phantom):
        with patch.object(open_app, "is_app_running", return_value=False):
            with patch.object(
                spotify_control, "_wake_spotify_app", return_value="real-device"
            ) as wake:
                with patch.object(spotify_control, "_request") as request:
                    with patch.object(
                        spotify_control,
                        "_find_track",
                        return_value={"uri": "spotify:track:x", "name": "Test"},
                    ):
                        SpotifyControlTool()._run_action("tok", "play", "Test Song")

    wake.assert_called_once()
    targeted = [
        call.kwargs["params"].get("device_id")
        for call in request.call_args_list
        if call.kwargs.get("params")
    ]
    assert targeted, "playback call should target a device explicitly"
    assert phantom not in targeted


def test_play_uses_existing_device_when_app_is_already_running():
    from openjarvis.tools import open_app, spotify_control
    from openjarvis.tools.spotify_control import SpotifyControlTool

    with patch.object(spotify_control, "_pick_device_id", return_value="live-dev"):
        with patch.object(open_app, "is_app_running", return_value=True):
            with patch.object(spotify_control, "_wake_spotify_app") as wake:
                with patch.object(spotify_control, "_request"):
                    message = SpotifyControlTool()._run_action("tok", "pause", "")

    wake.assert_not_called()
    assert message == "Playback paused."


def test_track_search_asks_for_more_than_one_result():
    """limit=1 can return an empty page for a query that plainly matches.

    "binibini by zack tabudlo" yielded nothing at limit=1 and two correct
    hits at limit=3, so asking for a single row turns a findable song into
    "not found". Only the top hit is ever used.
    """
    from openjarvis.tools import spotify_control

    with patch.object(spotify_control, "_request", return_value={}) as request:
        spotify_control._find_track("tok", "binibini by zack tabudlo")

    assert request.call_args.kwargs["params"]["limit"] > 1


def _two_computers():
    """An account signed in on this machine and on another one."""
    return [
        {"name": "DESKTOP-OTHER", "id": "remote-id", "is_active": True},
        {"name": "MYBOX", "id": "local-id", "is_active": False},
    ]


def test_play_targets_this_machine_even_when_another_device_is_active():
    """Otherwise "play a song" starts music on a PC in another room."""
    from openjarvis.tools import spotify_control

    with patch.object(spotify_control.socket, "gethostname", return_value="MyBox"):
        with patch.object(
            spotify_control, "_fetch_devices", return_value=_two_computers()
        ):
            chosen = spotify_control._pick_device_id("tok", prefer_local=True)

    assert chosen == "local-id"


def test_transport_actions_follow_the_active_device():
    """"Pause" should stop what is audible, not a silent local client."""
    from openjarvis.tools import spotify_control

    with patch.object(spotify_control.socket, "gethostname", return_value="MyBox"):
        with patch.object(
            spotify_control, "_fetch_devices", return_value=_two_computers()
        ):
            chosen = spotify_control._pick_device_id("tok", prefer_local=False)

    assert chosen == "remote-id"


def test_local_device_match_is_case_insensitive():
    """Spotify uppercases the hostname it registers under."""
    from openjarvis.tools import spotify_control

    with patch.object(spotify_control.socket, "gethostname", return_value="Grim"):
        matched = spotify_control._local_device_id(
            [{"name": "GRIM", "id": "local-id", "is_active": False}]
        )

    assert matched == "local-id"


def test_pausing_what_is_already_paused_is_a_success():
    """Spotify overloads 403; only one of its meanings warrants re-auth.

    Pausing an already-paused track answers 403 "Restriction violated", but
    the state the user asked for holds. Reporting that as a failure made the
    agent retry a pause that had already worked — three calls for one
    request, the last refused by the loop guard.
    """
    import httpx

    from openjarvis.tools import open_app, spotify_control
    from openjarvis.tools.spotify_control import SpotifyControlTool

    response = httpx.Response(
        403,
        text='{"error":{"message":"Player command failed: Restriction violated"}}',
        request=httpx.Request("PUT", "https://api.spotify.com/v1/me/player/pause"),
    )
    error = httpx.HTTPStatusError("403", request=response.request, response=response)

    tool = SpotifyControlTool()
    with patch.object(spotify_control, "_pick_device_id", return_value="dev"):
        with patch.object(open_app, "is_app_running", return_value=True):
            with patch.object(spotify_control, "_request", side_effect=error):
                with patch.object(
                    spotify_control.Path, "exists", return_value=True
                ):
                    with patch(
                        "openjarvis.connectors.spotify_auth.call_with_refresh",
                        side_effect=lambda fn, _path: fn("tok"),
                    ):
                        result = tool.execute(action="pause")

    assert result.success
    assert "Premium" not in result.content
    assert "reauth" not in result.content.lower()


def test_bare_play_falls_back_to_library_when_there_is_no_history():
    """"Play a song" must put music on, not come back asking which one.

    Resume 403s when nothing was paused and recently-played can be empty on
    a fresh client, which previously left a bare "play" with nothing to do.
    """
    from openjarvis.tools import open_app, spotify_control
    from openjarvis.tools.spotify_control import SpotifyControlTool

    liked = {
        "uri": "spotify:track:abc",
        "name": "Familiar",
        "artists": [{"name": "Someone"}],
    }

    def fake_request(token, method, path, params=None, json_body=None):
        if path == "me/player/play" and json_body is None:
            raise httpx.HTTPStatusError(
                "no active stream",
                request=httpx.Request("PUT", "https://api.spotify.com/v1/me/player/play"),
                response=httpx.Response(403, text="Restriction violated"),
            )
        if path == "me/player/recently-played":
            return {"items": []}
        if path == "me/top/tracks":
            return {"items": [liked]}
        return {}

    with patch.object(spotify_control, "_pick_device_id", return_value="dev"):
        with patch.object(open_app, "is_app_running", return_value=True):
            with patch.object(spotify_control, "_request", side_effect=fake_request):
                message = SpotifyControlTool()._run_action("tok", "play", "")

    assert message == "Now playing: Familiar by Someone"


def test_library_fallback_survives_a_missing_scope():
    """top-read and library-read are separate grants; one 403 must not end it."""
    from openjarvis.tools import spotify_control

    saved = {"uri": "spotify:track:xyz", "name": "Saved One", "artists": []}

    def fake_request(token, method, path, params=None, json_body=None):
        if path == "me/top/tracks":
            raise httpx.HTTPStatusError(
                "insufficient scope",
                request=httpx.Request("GET", "https://api.spotify.com/v1/me/top/tracks"),
                response=httpx.Response(403, text="Insufficient client scope"),
            )
        if path == "me/tracks":
            return {"items": [{"track": saved}]}
        return {}

    with patch.object(spotify_control, "_request", side_effect=fake_request):
        track = spotify_control._any_familiar_track("tok")

    assert track["uri"] == "spotify:track:xyz"
