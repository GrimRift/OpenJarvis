"""Tests for the spotify_control and open_app tools."""

from __future__ import annotations

from unittest.mock import patch

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
            result = OpenAppTool().execute(app="spotify")

    assert result.success
    args, kwargs = popen.call_args
    assert args[0] == [r"C:\fake\Spotify.exe"]
    assert kwargs.get("shell") is not True


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


def test_restriction_violated_is_not_reported_as_a_permission_problem():
    """Spotify overloads 403; only one of its meanings warrants re-auth."""
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

    assert not result.success
    assert "Premium" not in result.content
    assert "reauth" not in result.content.lower()
