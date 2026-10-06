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

import socket
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from openjarvis.core.config import DEFAULT_CONFIG_DIR
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_API_BASE = "https://api.spotify.com/v1"
_DEFAULT_TOKEN_PATH = str(DEFAULT_CONFIG_DIR / "connectors" / "spotify.json")

#: Kept beside the spec enum so the two cannot drift apart.
_ACTIONS = frozenset(
    {
        "status",
        "play",
        "pause",
        "next",
        "previous",
        "play_playlist",
        "play_liked",
        "list_playlists",
    }
)


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
    # limit=5 rather than 1: Spotify can return an *empty* first page for a
    # query that plainly matches — "binibini by zack tabudlo" yielded
    # nothing at limit=1 and two correct hits at limit=3 — so asking for a
    # single result turns a findable song into "not found". Only the top hit
    # is used either way; the extra rows exist to stop that empty-page case.
    data = _request(
        token,
        "GET",
        "search",
        params={"q": query, "type": "track", "limit": 5},
    )
    items = (data.get("tracks") or {}).get("items") or []
    return items[0] if items else {}


def _list_playlists(token: str) -> List[Dict[str, Any]]:
    """Every playlist on the account, paged out.

    Paged rather than a single call: someone with 60 playlists would find the
    last 10 permanently invisible, and "it cannot find my playlist" is
    indistinguishable from the feature being broken.
    """
    playlists: List[Dict[str, Any]] = []
    offset = 0
    while offset < 500:
        data = _request(
            token, "GET", "me/playlists", params={"limit": 50, "offset": offset}
        )
        items = data.get("items") or []
        playlists.extend(item for item in items if item)
        if len(items) < 50:
            break
        offset += 50
    return playlists


def _track_total(playlist: Dict[str, Any]) -> Optional[int]:
    """How many tracks a playlist holds, or None when the API does not say.

    Spotify returns this as ``items: {href, total}`` on this account, not the
    ``tracks: {total}`` the docs describe and this module used to read. The
    old lookup fell through to its default on every playlist, so a 63-track
    playlist reported 0 -- and "(0 tracks)" was read downstream as "the
    playlist is empty, so nothing could start", said about music that was
    already playing. Both spellings are accepted; None means unknown, which
    is deliberately not the same as zero.
    """
    for field in ("items", "tracks"):
        value = playlist.get(field)
        if isinstance(value, dict) and isinstance(value.get("total"), int):
            return value["total"]
    return None


def _describe_track(track: Dict[str, Any]) -> str:
    name = track.get("name") or "a track"
    artists = ", ".join(a.get("name", "") for a in track.get("artists") or [])
    return f"{name} by {artists}" if artists else name


def _confirm_playback(token: str, attempts: int = 4) -> Dict[str, Any]:
    """What Spotify is actually playing, once it has caught up.

    The play call returns 204 before the player has switched, so asking
    immediately reports the previous track or nothing at all. Reporting what
    is really playing is the point: the failure this replaces described
    playback as impossible while the speakers were already going.
    """
    import time

    for attempt in range(attempts):
        if attempt:
            time.sleep(0.4)
        try:
            data = _request(token, "GET", "me/player/currently-playing")
        except Exception:
            continue
        track = (data or {}).get("item") or {}
        if track:
            return track
    return {}


def _find_playlist(token: str, query: str) -> Dict[str, Any]:
    """Resolve a spoken playlist name to one of the user's playlists.

    Exact match first: someone with "Gym" and "Gym 2024" who says "Gym" means
    the one called Gym. Only then a substring, so a half-remembered name still
    lands.
    """
    wanted = (query or "").strip().lower()
    if not wanted:
        return {}
    playlists = _list_playlists(token)
    for playlist in playlists:
        if (playlist.get("name") or "").strip().lower() == wanted:
            return playlist
    for playlist in playlists:
        if wanted in (playlist.get("name") or "").lower():
            return playlist
    return {}


def _liked_track_uris(token: str, limit: int = 50) -> List[str]:
    """URIs from Liked Songs.

    Liked Songs is not a playlist and has no ``context_uri``, so it can only
    be played by handing Spotify an explicit list of tracks.
    """
    data = _request(token, "GET", "me/tracks", params={"limit": min(limit, 50)})
    items = data.get("items") or []
    return [
        (item.get("track") or {}).get("uri")
        for item in items
        if (item.get("track") or {}).get("uri")
    ]


def _set_shuffle(token: str, target: Dict[str, Any], state: bool) -> None:
    """Best-effort shuffle. Never worth failing playback over."""
    try:
        _request(
            token,
            "PUT",
            "me/player/shuffle",
            params={**target, "state": str(state).lower()},
        )
    except httpx.HTTPStatusError:
        pass


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


def _any_familiar_track(token: str) -> Dict[str, Any]:
    """A track the user actually likes, for a bare "play a song".

    Sources are tried best-first. Top-tracks and saved-tracks are the
    truest answer to "songs I like listening to", but each needs its own
    OAuth scope (``user-top-read`` / ``user-library-read``) that a token
    minted before those scopes existed will not carry — both answer 403
    "Insufficient client scope" rather than an empty list, which is why the
    chain treats an HTTP error as "try the next source" rather than fatal.

    Recently-played needs only the scope this tool already has, so it is
    the source that actually works on such a token, and it is a fair proxy
    besides: it reflects real listening rather than a genre guess. Sampled
    across a wide window and de-duplicated so a track played five times in
    a row does not crowd out everything else.

    Chosen at random rather than taking the first row: picking
    deterministically means "play a song" starts the *same* track every
    time, which reads as the assistant ignoring the request rather than
    choosing something.
    """
    import random

    for path, params, extract in (
        ("me/top/tracks", {"limit": 50}, lambda d: d.get("items") or []),
        (
            "me/tracks",
            {"limit": 50},
            lambda d: [i.get("track") or {} for i in (d.get("items") or [])],
        ),
        (
            "me/player/recently-played",
            {"limit": 50},
            lambda d: [i.get("track") or {} for i in (d.get("items") or [])],
        ),
    ):
        try:
            tracks = extract(_request(token, "GET", path, params=params))
        except httpx.HTTPStatusError:
            # A missing scope (top-read / library-read are separate grants)
            # must not sink the whole fallback — try the next source.
            continue
        unique: Dict[str, Dict[str, Any]] = {}
        for track in tracks:
            uri = track.get("uri")
            if uri and uri not in unique:
                unique[uri] = track
        if unique:
            return random.choice(list(unique.values()))
    return {}


def _fetch_devices(token: str) -> List[Dict[str, Any]]:
    """Return the account's currently visible Spotify Connect devices.

    A 401 is deliberately allowed to propagate: access tokens last an hour,
    and ``call_with_refresh`` mints a new one and retries when it sees that
    status. Swallowing it here returned an empty device list instead, which
    every caller reads as "no device to play on" — so once the hour was up,
    playback failed with "Spotify didn't start" and waited 30s for an app
    that was already running.
    """
    try:
        data = _request(token, "GET", "me/player/devices")
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code == 401:
            raise
        return []
    return data.get("devices") or []


def _local_device_id(devices: List[Dict[str, Any]]) -> str:
    """Return the id of the device that is *this* machine, or "".

    The desktop client registers itself under the machine's hostname, so
    that is what identifies "here" among the account's devices.
    """
    hostname = socket.gethostname().strip().lower()
    if not hostname:
        return ""
    for device in devices:
        if (device.get("name") or "").strip().lower() == hostname:
            return device.get("id", "")
    return ""


def _pick_device_id(token: str, *, prefer_local: bool = False) -> str:
    """Choose which device to target, and always name one explicitly.

    An open-but-idle client is listed with ``is_active: False``, and the
    transport endpoints answer 404 "no active device" unless playback is
    explicitly targeted — passing a ``device_id`` transfers playback to that
    client, which is what makes "play" work when Spotify is merely open.

    ``prefer_local`` starts playback on this machine. An account commonly
    has several clients signed in (another PC, a phone, a TV) and Spotify
    marks whichever last played as active, so choosing purely by "active"
    sends "play a song" to a device in another room. Transport actions leave
    this off deliberately and follow the active device instead, so "pause"
    stops whatever is actually audible rather than a silent local client.
    """
    devices = _fetch_devices(token)
    if not devices:
        return ""
    local_id = _local_device_id(devices)
    if prefer_local and local_id:
        return local_id
    for device in devices:
        if device.get("is_active"):
            return device.get("id", "")
    return local_id or devices[0].get("id", "")


def _wake_spotify_app(token: str, timeout_seconds: float = 30.0) -> str:
    """Launch the desktop app and wait until it is really ready; return its id.

    A bare "play a song" should just work, so rather than telling the user
    to open Spotify first, open it for them. The client takes a few seconds
    after launch to appear in the devices list, hence polling rather than a
    single fixed sleep.

    The poll waits for *this machine's* entry specifically, not merely for
    some device to exist. Both weaker checks fail here: the process alone
    can be up before Connect registration completes, and any-device-will-do
    matches a stale registration or another computer on the account, which
    would hand back an id pointing somewhere else entirely — the app opens
    on screen while the music plays in another room.

    Falls back to any device once the process is up and the wait is spent,
    so an unexpected hostname mismatch degrades to playing *something*
    rather than refusing outright.
    """
    import time

    from openjarvis.tools.open_app import OpenAppTool, is_app_running

    if not OpenAppTool().execute(app="spotify").success:
        return ""

    deadline = time.monotonic() + timeout_seconds
    running = False
    while time.monotonic() < deadline:
        # 1s rather than a lazier interval: the client usually registers a
        # few seconds after launch, and this wait sits directly in front of
        # the user on every "play a song" that starts from a closed app.
        time.sleep(1.0)
        if not is_app_running("spotify"):
            continue
        running = True
        local_id = _local_device_id(_fetch_devices(token))
        if local_id:
            return local_id
    return _pick_device_id(token) if running else ""


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
                "Spotify playback. Pick the action from what the user asked "
                "for in this message:\n"
                "- 'play a song' / 'play music' / 'play something' / "
                "'play' → action='play' with NO query. It resumes what was "
                "paused, or starts something on its own if nothing was. "
                "Never answer these by reading status or by asking which "
                "song — just play.\n"
                "- 'play <song>' / 'play <song> by <artist>' → action='play' "
                "with query set to what they named.\n"
                "- 'what's playing?' / 'what song is this?' → "
                "action='status'. This one only reads and changes nothing.\n"
                "- 'pause' / 'next' / 'skip' / 'previous' / 'go back' → that "
                "action.\n"
                "'pause', 'next' and 'previous' are audible in the room, so "
                "only use them when asked. Never start playback merely to "
                "find out what is playing — that is what 'status' is for. If "
                "the user only said hello, or asked about something other "
                "than music, call nothing at all. 'play' opens the Spotify "
                "app by itself when closed, so do NOT call open_app first.\n"
                "- 'play my <name> playlist' / 'put on my <name>' → "
                "action='play_playlist' with query set to the playlist name.\n"
                "- 'play my liked songs' / 'play my library' / 'play my "
                "saved songs' → action='play_liked'.\n"
                "- 'what playlists do I have?' → action='list_playlists'. "
                "This one only reads and plays nothing."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Playback action to perform.",
                        "enum": [
                            "status",
                            "play",
                            "pause",
                            "next",
                            "previous",
                            "play_playlist",
                            "play_liked",
                            "list_playlists",
                        ],
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "Song to play, e.g. 'Bohemian Rhapsody' or "
                            "'Anti-Hero by Taylor Swift'. Only used with "
                            "action='play'; omit to resume what was paused. "
                            "With action='play_playlist' this is the "
                            "playlist's name instead."
                        ),
                    },
                    "shuffle": {
                        "type": "boolean",
                        "description": (
                            "Shuffle for play_playlist and play_liked. "
                            "Default true. Set false if the user asked for "
                            "the playlist in order."
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

    @staticmethod
    def _play_body(track: Dict[str, Any]) -> Dict[str, Any]:
        """Playback body that starts *track* but leaves somewhere to skip to.

        ``{"uris": [one_uri]}`` makes Spotify's queue exactly one track long,
        so a later "next song" succeeds at the API level and then silently
        does nothing -- there is no next track to move to. Starting the
        track's album as the context instead (seeking to the track itself)
        gives the player a real queue, which is what makes next/previous
        behave the way someone asking for "the next song" expects.
        """
        album_uri = (track.get("album") or {}).get("uri")
        if album_uri:
            return {"context_uri": album_uri, "offset": {"uri": track["uri"]}}
        return {"uris": [track["uri"]]}

    def _run_action(
        self, token: str, action: str, query: str, shuffle: bool = True
    ) -> str:
        """Perform *action* and return the message to show the user."""
        # Answered before anything is launched or targeted, because it must
        # not change what the user is hearing. It exists to remove the
        # incentive to call "play" as a way of looking: with no read-only
        # option, a greeting like "hi" would be answered by starting music
        # and reporting the track that began, which is exactly what happened
        # in practice.
        if action == "status":
            data = _request(token, "GET", "me/player")
            track = data.get("item") or {}
            if not track:
                return "Nothing is playing on Spotify right now."
            playing = data.get("is_playing")
            name = track.get("name", "a track")
            artists = ", ".join(a.get("name", "") for a in track.get("artists") or [])
            suffix = f" by {artists}" if artists else ""
            state = "Playing" if playing else "Paused"
            return f"{state}: {name}{suffix}"

        # Reads and plays nothing, so it is answered here for the same reason
        # status is: it must not start music to answer a question about music.
        if action == "list_playlists":
            playlists = _list_playlists(token)
            if not playlists:
                return "No playlists on this account."
            names = [
                f"{item.get('name', 'Untitled')}"
                + (
                    f" ({_track_total(item)} tracks)"
                    if _track_total(item) is not None
                    else ""
                )
                for item in playlists
            ]
            return f"{len(names)} playlist(s): " + ", ".join(names)

        # Every action needs somewhere to play. Opening the app on demand is
        # what makes a bare "play a song" work without the user having
        # launched Spotify first, and it also puts the window on screen so
        # they can watch it — the point of doing this via the API rather
        # than by clicking inside the app.
        #
        # Liveness is decided by the local process, never by the device
        # list. Spotify keeps a Connect device registered server-side after
        # the client exits, so a closed app is still reported as an active
        # device that accepts commands and reports is_playing — playback
        # "succeeds" with no window and no sound. Gating the launch on the
        # device list therefore means never launching at all.
        from openjarvis.tools.open_app import is_app_running

        # "play" means "start music here", so it targets this machine even
        # when another of the account's devices is the active one. The
        # transport actions deliberately do not, so "pause" stops whatever
        # is actually audible instead of a silent local client.
        prefer_local = action in {"play", "play_playlist", "play_liked"}

        if is_app_running("spotify"):
            device_id = _pick_device_id(
                token, prefer_local=prefer_local
            ) or _wake_spotify_app(token)
        else:
            device_id = _wake_spotify_app(token)
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

        if action == "play_playlist":
            if not query:
                return "__NO_PLAYLIST_NAME__"
            playlist = _find_playlist(token, query)
            if not playlist:
                return "__NO_PLAYLIST__"
            _set_shuffle(token, target, shuffle)
            _request(
                token,
                "PUT",
                "me/player/play",
                params=target,
                json_body={"context_uri": playlist["uri"]},
            )
            how = "shuffled" if shuffle else "in order"
            total = _track_total(playlist)
            size = f", {total} tracks" if total is not None else ""
            playing = _confirm_playback(token)
            if playing:
                return (
                    f"Playing {_describe_track(playing)} from your playlist "
                    f"{playlist['name']!r} ({how}{size})."
                )
            # Never claim the playlist is empty here. Spotify accepted the
            # call; what is unknown is only whether the player has caught up.
            return (
                f"Started your playlist {playlist['name']!r} ({how}{size}). "
                "Spotify has not reported a track yet."
            )

        if action == "play_liked":
            uris = _liked_track_uris(token)
            if not uris:
                return "__NO_LIKED__"
            _set_shuffle(token, target, shuffle)
            _request(
                token,
                "PUT",
                "me/player/play",
                params=target,
                json_body={"uris": uris},
            )
            how = "shuffled" if shuffle else "in order"
            return f"Playing your Liked Songs ({len(uris)} tracks), {how}."

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
                json_body=self._play_body(track),
            )
            return self._describe(track)

        # Bare "play something": resume whatever was paused. A freshly
        # launched client has nothing to resume (Spotify answers 403
        # "Restriction violated" rather than an error worth surfacing).
        try:
            _request(token, "PUT", "me/player/play", params=target)
            return "Playback resumed."
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status not in (403, 404):
                raise

        # With nothing to resume, prefer a track the user actually likes over
        # replaying whatever happened to be last. Most-recent is deliberately
        # the *lower* priority of the two: it is a single deterministic track,
        # so leading with it made every bare "play a song" start the same
        # song, including songs that were only played once to test something.
        track = _any_familiar_track(token) or _most_recent_track(token)
        if not track:
            return "__NO_HISTORY__"
        _request(
            token,
            "PUT",
            "me/player/play",
            params=target,
            json_body=self._play_body(track),
        )
        return self._describe(track)

    def execute(self, **params: Any) -> ToolResult:
        from openjarvis.connectors.spotify_auth import (
            SpotifyAuthError,
            call_with_refresh,
        )

        action = str(params.get("action", "")).strip().lower()
        query = str(params.get("query", "") or "").strip()

        shuffle = bool(params.get("shuffle", True))

        if action not in _ACTIONS:
            return ToolResult(
                tool_name="spotify_control",
                content=(
                    f"Unknown action {action!r}. "
                    f"Use one of: {', '.join(sorted(_ACTIONS))}."
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
                lambda token: self._run_action(token, action, query, shuffle),
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
                    # Reported as success: the user asked for a state and
                    # that state already holds. Calling it a failure made
                    # the agent retry a pause that had already worked —
                    # three calls for one "pause song", the last of them
                    # stopped by the loop guard.
                    return ToolResult(
                        tool_name="spotify_control",
                        content=(
                            f"Already {action}d — nothing to change."
                            if action in {"pause"}
                            else f"Cannot {action} right now; there is no "
                            "track to move to."
                        ),
                        success=action == "pause",
                        metadata={"action": action, "no_op": True},
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
            "__NO_PLAYLIST_NAME__": (
                "Which playlist? Name it, or ask me to list them."
            ),
            "__NO_PLAYLIST__": (
                f"No playlist of yours matches {query!r}. If it is a "
                "private playlist and this keeps happening, the Spotify "
                "connection needs re-authorising: run "
                "scripts/reauth-spotify.py."
            ),
            "__NO_LIKED__": (
                "No Liked Songs on this account to play."
            ),
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
