"""Turn the other apps' media down while the user talks to Sage, then back.

A video in the room is mixed into everything the mic hears: the user's own
words over it scored as unsure as the video's dialogue (6 October, 0.62-0.73
user against 0.51-0.70 Sage, against 0.84-0.86 user in a quiet room), so no
voice margin can tell them apart. The user's choices that evening:

* a confirmed "Hey Sage", the user heard talking over Sage, or Sage starting
  to speak TURNS DOWN what is playing -- any app, Opera's YouTube and
  Spotify alike, whoever started it. Turned down, not paused (the user
  first chose pausing, then asked for the volume instead);
* when the exchange is over the volumes go back to exactly what they were.

Separately, the user can ask to pause or resume a player ("pause the
video"): ``pause_for_user`` / ``resume_for_user``, through Windows' media
controls (the same list as the volume flyout), so no app's own API is
needed. Every call is best effort and never raises: the voice must not
depend on it.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

#: Share of its own volume an app keeps while held. Lower than the 35% the
#: server-spoken announcements use: here the point is a clean mic, not only
#: an audible voice.
LEVEL = 0.25
#: A hold the page never released (closed mid-exchange, server error) lets
#: go on its own after this long, so music is not left quiet for good.
HOLD_MAX_SECONDS = 600.0
#: Windows' GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING.
_PLAYING = 4

_lock = threading.RLock()
#: (pid, app name, original volume) for each app this hold turned down.
_ducked: List[Tuple[int, str, float]] = []
#: What the user's own "pause the video" paused, for a later "resume".
_user_paused: List[str] = []
_timer: Any = None


def _arm_timer() -> None:
    global _timer
    if _timer is not None:
        _timer.cancel()
    _timer = threading.Timer(HOLD_MAX_SECONDS, release)
    _timer.daemon = True
    _timer.start()


def duck(level: float = LEVEL) -> List[str]:
    """Turn down every other app playing now; returns their names. An app
    already turned down by this hold stays where it is."""
    from openjarvis.speech import ducking

    with _lock:
        lowered: List[Tuple[Any, float, float]] = []
        names: List[str] = []
        try:
            held = {pid for pid, _, _ in _ducked}
            for name, session in ducking._sessions():
                pid = int(session.Process.pid)
                if pid in held:
                    continue
                original = float(session.SimpleAudioVolume.GetMasterVolume())
                if original <= 0:
                    continue
                lowered.append((session, original, original * level))
                _ducked.append((pid, name, original))
                names.append(name)
            if lowered:
                # On disk too: a server killed mid-hold puts them back at
                # its next start (ducking.restore_leftover).
                ducking._remember([(n, o) for _, n, o in _ducked])
                ducking._fade(lowered, ducking.DEFAULT_FADE_MS)
                logger.info("Media turned down for the user: %s", names)
            if _ducked:
                _arm_timer()
        except Exception:  # noqa: BLE001
            logger.debug("Media duck unavailable", exc_info=True)
        return names


def release() -> List[str]:
    """End the hold: every app turned down goes back to its own volume."""
    from openjarvis.speech import ducking

    global _timer
    with _lock:
        if _timer is not None:
            _timer.cancel()
            _timer = None
        restored: List[str] = []
        if _ducked:
            try:
                by_pid = {pid: original for pid, _, original in _ducked}
                back: List[Tuple[Any, float, float]] = []
                for name, session in ducking._sessions(active_only=False):
                    pid = int(session.Process.pid)
                    if pid not in by_pid:
                        continue
                    current = float(session.SimpleAudioVolume.GetMasterVolume())
                    back.append((session, current, by_pid[pid]))
                    restored.append(name)
                ducking._fade(back, ducking.DEFAULT_FADE_MS)
                ducking._forget()
            except Exception:  # noqa: BLE001
                logger.debug("Media un-duck failed", exc_info=True)
            if restored:
                logger.info("Media volume back: %s", restored)
        _ducked.clear()
        return restored


def state() -> Dict[str, Any]:
    with _lock:
        return {"ducked": [name for _, name, _ in _ducked]}


# ------------------------------------------------- the user's own pause


def _run(coroutine: Any) -> Any:
    """Run a WinRT coroutine to completion on a private event loop (callers
    are worker threads; the server's own loop is never borrowed)."""
    return asyncio.run(coroutine)


async def _manager() -> Any:
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as Manager,
    )

    return await Manager.request_async()


def kind_of(app_id: str) -> str:
    """"music" for Spotify, "video" for a browser, "other" otherwise."""
    app = app_id.lower()
    if "spotify" in app:
        return "music"
    if any(b in app for b in ("opera", "chrome", "edge", "firefox", "brave")):
        return "video"
    return "other"


def _wanted(app_id: str, what: str) -> bool:
    return what == "all" or kind_of(app_id) == what


async def _pause(what: str) -> List[str]:
    manager = await _manager()
    paused: List[str] = []
    for session in manager.get_sessions():
        app = str(session.source_app_user_model_id)
        if not _wanted(app, what):
            continue
        try:
            if session.get_playback_info().playback_status != _PLAYING:
                continue
            if await session.try_pause_async():
                paused.append(app)
        except Exception:  # noqa: BLE001 -- one stubborn app spoils nothing
            continue
    return paused


async def _play(what: str, only: List[str]) -> List[str]:
    manager = await _manager()
    resumed: List[str] = []
    for session in manager.get_sessions():
        app = str(session.source_app_user_model_id)
        if not _wanted(app, what) or (only and app not in only):
            continue
        try:
            if session.get_playback_info().playback_status == _PLAYING:
                continue
            if await session.try_play_async():
                resumed.append(app)
        except Exception:  # noqa: BLE001
            continue
    return resumed


def pause_for_user(what: str = "all") -> List[str]:
    """The user's "pause the video / the music": returns the apps paused."""
    if sys.platform != "win32":
        return []
    with _lock:
        try:
            paused = _run(_pause(what))
        except Exception:  # noqa: BLE001
            logger.debug("Media pause unavailable", exc_info=True)
            return []
        for app in paused:
            if app not in _user_paused:
                _user_paused.append(app)
        return paused


def resume_for_user(what: str = "all") -> List[str]:
    """The user's "resume / play it again": what their pause stopped, or,
    failing that, whatever of that kind is paused now."""
    if sys.platform != "win32":
        return []
    with _lock:
        only = [a for a in _user_paused if _wanted(a, what)]
        try:
            resumed = _run(_play(what, only))
        except Exception:  # noqa: BLE001
            logger.debug("Media resume unavailable", exc_info=True)
            return []
        for app in resumed:
            if app in _user_paused:
                _user_paused.remove(app)
        return resumed


__all__ = [
    "HOLD_MAX_SECONDS",
    "LEVEL",
    "duck",
    "kind_of",
    "pause_for_user",
    "release",
    "resume_for_user",
    "state",
]
