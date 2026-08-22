"""Spotify playback control via the Web API.

Deliberately API-driven rather than clicking inside the desktop app: the
Spotify client syncs its playback state from the account, so a Web API call
makes an already-open desktop window visibly change track in real time
without any UI automation.

Read-only history sync lives in ``connectors/spotify.py``; this is the
write half and needs two extra scopes (see ``connectors/oauth.py``):
``user-modify-playback-state`` for the transport actions and
``user-read-playback-state`` to look up which device to target.

Playback control is Premium-only — Spotify returns 403 for free accounts.
Note it also returns 403 with "Restriction violated" for a command that
simply does not apply right now (pausing what is already paused), which is
not a permission problem; ``execute`` separates the two.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from openjarvis.core.config import DEFAULT_CONFIG_DIR
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_API_BASE = "https://api.spotify.com/v1"
_DEFAULT_TOKEN_PATH = str(DEFAULT_CONFIG_DIR / "connectors" / "spotify.json")


def _request(
    token: str,
    method: str,
    endpoint: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Call the Spotify Web API and return the parsed body (empty dict if none)."""
    resp = httpx.request(
        method,
        f"{_API_BASE}/{endpoint}",
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        json=json_body,
        timeout=30.0,
    )
    resp.raise_for_status()
    # Only the GET endpoints (/search, /devices, /recently-played) return a
    # body. The transport endpoints answer 204 No Content — but also 202
    # Accepted, and occasionally 200 with an empty or non-JSON body, so
    # decode defensively rather than trusting the status code alone.
    if not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError:
        return {}


def _find_track(token: str, query: str) -> Dict[str, Any]:
    """Resolve a free-text song name to the top matching track."""
    data = _request(
        token,
        "GET",
        "search",
        params={"q": query, "type": "track", "limit": 1},
    )
    items = (data.get("tracks") or {}).get("items") or []
    return items[0] if items else {}


def _most_recent_track(token: str) -> Dict[str, Any]:
    """Return the last track the user played, or {} if there is no history."""
    data = _request(
        token,
        "GET",
        "me/player/recently-played",
        params={"limit": 1},
    )
    items = data.get("items") or []
    return (items[0].get("track") or {}) if items else {}


def _pick_device_id(token: str) -> str:
    """Return a device id to target, preferring the active one.

    An open-but-idle Spotify client is listed as a device with
    ``is_active: False``, and the transport endpoints answer 404 "no active
    device" unless playback is explicitly targeted at it. Passing an
    explicit ``device_id`` transfers playback to that client, which is what
    makes "play a song" work when Spotify is merely open rather than
    already playing.
    """
    try:
        data = _request(token, "GET", "me/player/devices")
    except httpx.HTTPStatusError:
        return ""
    devices = data.get("devices") or []
    if not devices:
        return ""
    for device in devices:
        if device.get("is_active"):
            return device.get("id", "")
    return devices[0].get("id", "")


def _wake_spotify_app(token: str, timeout_seconds: float = 20.0) -> str:
    """Launch the desktop app and wait for it to register; return its id.

    A bare "play a song" should just work, so rather than telling the user
    to open Spotify first, open it for them. The client takes a few seconds
    after launch to appear in the devices list, hence polling rather than a
    single fixed sleep.
    """
    import time

    from openjarvis.tools.open_app import OpenAppTool

    if not OpenAppTool().execute(app="spotify").success:
        return ""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        time.sleep(2.0)
        device_id = _pick_device_id(token)
        if device_id:
            return device_id
    return ""


@ToolRegistry.register("spotify_control")
class SpotifyControlTool(BaseTool):
    """Control Spotify playback: play, pause, skip, or play a specific song."""

    tool_id = "spotify_control"
    is_local = False

    def __init__(self, token_path: str = _DEFAULT_TOKEN_PATH) -> None:
        self._token_path = str(token_path)

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="spotify_control",
            description=(
                "Control Spotify playback on the user's active device. "
                "Actions: 'play' (resume, or start a specific song when "
                "'query' is given), 'pause', 'next', 'previous'. Requires "
                "Spotify to be open on some device. Use open_app first if "
                "the user wants to see the Spotify window."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Playback action to perform.",
                        "enum": ["play", "pause", "next", "previous"],
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "Song to play, e.g. 'Bohemian Rhapsody' or "
                            "'Anti-Hero by Taylor Swift'. Only used with "
                            "action='play'; omit to resume what was paused."
                        ),
                    },
                },
                "required": ["action"],
            },
            category="media",
            timeout_seconds=30.0,
        )

    @staticmethod
    def _describe(track: Dict[str, Any]) -> str:
        name = track.get("name", "the track")
        artists = ", ".join(a.get("name", "") for a in track.get("artists") or [])
        return f"Now playing: {name}" + (f" by {artists}" if artists else "")

    def _run_action(self, token: str, action: str, query: str) -> str:
        """Perform *action* and return the message to show the user."""
        # Every action needs somewhere to play. Opening the app on demand is
        # what makes a bare "play a song" work without the user having
        # launched Spotify first, and it also puts the window on screen so
        # they can watch it — the point of doing this via the API rather
        # than by clicking inside the app.
        device_id = _pick_device_id(token) or _wake_spotify_app(token)
        if not device_id:
            return "__NO_DEVICE__"
        target = {"device_id": device_id}

        if action == "pause":
            _request(token, "PUT", "me/player/pause", params=target)
            return "Playback paused."
        if action == "next":
            _request(token, "POST", "me/player/next", params=target)
            return "Skipped to the next track."
        if action == "previous":
            _request(token, "POST", "me/player/previous", params=target)
            return "Went back to the previous track."

        # action == "play" with a named song
        if query:
            track = _find_track(token, query)
            if not track:
                return "__NO_MATCH__"
            _request(
                token,
                "PUT",
                "me/player/play",
                params=target,
                json_body={"uris": [track["uri"]]},
            )
            return self._describe(track)

        # Bare "play something": resume whatever was paused. A freshly
        # launched client has nothing to resume (Spotify answers 403
        # "Restriction violated" rather than an error worth surfacing), so
        # fall back to replaying the most recent track from history.
        try:
            _request(token, "PUT", "me/player/play", params=target)
            return "Playback resumed."
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status not in (403, 404):
                raise

        track = _most_recent_track(token)
        if not track:
            return "__NO_HISTORY__"
        _request(
            token,
            "PUT",
            "me/player/play",
            params=target,
            json_body={"uris": [track["uri"]]},
        )
        return self._describe(track)

    def execute(self, **params: Any) -> ToolResult:
        from openjarvis.connectors.spotify_auth import (
            SpotifyAuthError,
            call_with_refresh,
        )

        action = str(params.get("action", "")).strip().lower()
        query = str(params.get("query", "") or "").strip()

        if action not in {"play", "pause", "next", "previous"}:
            return ToolResult(
                tool_name="spotify_control",
                content=(
                    f"Unknown action {action!r}. "
                    "Use play, pause, next, or previous."
                ),
                success=False,
            )

        if not Path(self._token_path).exists():
            return ToolResult(
                tool_name="spotify_control",
                content=(
                    "Spotify is not connected. Run: jarvis connect spotify"
                ),
                success=False,
            )

        try:
            message = call_with_refresh(
                lambda token: self._run_action(token, action, query),
                self._token_path,
            )
        except SpotifyAuthError as exc:
            return ToolResult(
                tool_name="spotify_control",
                content=f"Spotify authentication failed: {exc}",
                success=False,
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            # These three are the everyday failures and each has a specific
            # fix, so they get named rather than surfacing a bare status code.
            if status == 403:
                # Spotify overloads 403: a genuine permission problem (free
                # account, or a token minted before the playback scopes were
                # added) and a merely inapplicable command (pausing what is
                # already paused, "previous" with nothing before it) both
                # land here. The latter says "Restriction violated" in the
                # body and is not an error worth alarming the user about.
                body = exc.response.text if exc.response is not None else ""
                if "Restriction violated" in body:
                    detail = (
                        f"Spotify would not {action} right now — it is "
                        "probably already in that state, or there is no "
                        "track to move to."
                    )
                else:
                    detail = (
                        "Spotify refused playback control (403). This usually "
                        "means the account is not Premium, or the saved token "
                        "predates the playback permission — re-run: "
                        "python scripts/reauth-spotify.py"
                    )
            elif status == 404:
                detail = (
                    "No active Spotify device found. Open Spotify and play "
                    "something once so it registers as the active device."
                )
            elif status == 429:
                detail = "Spotify rate-limited the request; try again shortly."
            else:
                detail = f"Spotify API error ({status})."
            return ToolResult(
                tool_name="spotify_control",
                content=detail,
                success=False,
            )
        except Exception as exc:
            return ToolResult(
                tool_name="spotify_control",
                content=f"Spotify request failed: {exc}",
                success=False,
            )

        failures = {
            "__NO_DEVICE__": (
                "Could not get Spotify ready to play. Open the Spotify app "
                "and play something once, then try again."
            ),
            "__NO_MATCH__": f"No track found matching {query!r}.",
            "__NO_HISTORY__": (
                "Nothing to resume and no recent listening history to fall "
                "back on. Name a song to play instead."
            ),
        }
        if message in failures:
            return ToolResult(
                tool_name="spotify_control",
                content=failures[message],
                success=False,
            )

        return ToolResult(
            tool_name="spotify_control",
            content=message,
            success=True,
            metadata={"action": action, "query": query},
        )


__all__ = ["SpotifyControlTool"]
