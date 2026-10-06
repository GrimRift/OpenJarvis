"""Hold the other apps' media while the user talks to Sage, then let it go.

A video in the room is mixed into everything the mic hears: the user's own
words over it scored as unsure as the video's dialogue (6 October, 0.62-0.73
user against 0.51-0.70 Sage, against 0.84-0.86 user in a quiet room), so no
voice margin can tell them apart. The user's choices that evening:

* a confirmed "Hey Sage", or the user heard talking over Sage, PAUSES what
  is playing -- any app, Opera's YouTube and Spotify alike, whoever started
  it;
* while Sage speaks, what still plays (a video Sage just opened) is TURNED
  DOWN, not paused;
* when the exchange is over everything comes back: volumes restored, the
  paused players resumed -- unless the user asked for media in between
  ("pause the video", Spotify, a new video), when their request stands.

Pausing goes through Windows' media controls (the same session list as the
volume flyout), so it needs no app's own API; turning down reuses the
per-app volume of ``speech.ducking``. Every call is best effort and never
raises: the voice must not depend on it.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

#: Windows' GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING.
_PLAYING = 4
#: A hold the page never released (closed mid-exchange, server error) lets
#: go on its own after this long, so music is not left paused for good.
HOLD_MAX_SECONDS = 600.0

_lock = threading.RLock()
#: Media apps (Windows' app ids) this hold paused, to resume at release.
_paused: List[str] = []
#: (pid, app name, original volume) for each app this hold turned down.
_ducked: List[Tuple[int, str, float]] = []
#: Apps the user asked about during the hold (or "*" for all): release
#: does not resume them.
_kept: set = set()
#: What the user's own "pause the video" paused, for a later "resume".
_user_paused: List[str] = []
_timer: Any = None


def _run(coroutine: Any) -> Any:
    """Run a WinRT coroutine to completion on a private event loop (callers
    are worker threads; the server's own loop is never borrowed)."""
    return asyncio.run(coroutine)


async def _manager() -> Any:
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as Manager,
    )

    return await Manager.request_async()


async def _pause_playing() -> List[str]:
    manager = await _manager()
    paused: List[str] = []
    for session in manager.get_sessions():
        try:
            if session.get_playback_info().playback_status != _PLAYING:
                continue
            if await session.try_pause_async():
                paused.append(str(session.source_app_user_model_id))
        except Exception:  # noqa: BLE001 -- one stubborn app spoils nothing
            continue
    return paused


async def _play(app_ids: List[str]) -> List[str]:
    manager = await _manager()
    resumed: List[str] = []
    for session in manager.get_sessions():
        app = str(session.source_app_user_model_id)
        if app not in app_ids:
            continue
        try:
            if session.get_playback_info().playback_status == _PLAYING:
                continue
            if await session.try_play_async():
                resumed.append(app)
        except Exception:  # noqa: BLE001
            continue
    return resumed


def _arm_timer() -> None:
    global _timer
    if _timer is not None:
        _timer.cancel()
    _timer = threading.Timer(HOLD_MAX_SECONDS, release)
    _timer.daemon = True
    _timer.start()


def pause() -> List[str]:
    """Pause every app playing media now; returns the app ids paused."""
    if sys.platform != "win32":
        return []
    with _lock:
        try:
            paused = _run(_pause_playing())
        except Exception:  # noqa: BLE001
            logger.debug("Media pause unavailable", exc_info=True)
            return []
        for app in paused:
            if app not in _paused:
                _paused.append(app)
        if paused:
            logger.info("Media held for the user: paused %s", paused)
        if _paused or _ducked:
            _arm_timer()
        return paused


def duck(level: float | None = None) -> List[str]:
    """Turn down every other app still playing; returns their names. An app
    already turned down by this hold is left where it is."""
    from openjarvis.speech import ducking

    level = ducking.DEFAULT_LEVEL if level is None else level
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
                ducking._remember([(n, o) for _, n, o in _ducked])
                ducking._fade(lowered, ducking.DEFAULT_FADE_MS)
                _arm_timer()
        except Exception:  # noqa: BLE001
            logger.debug("Media duck unavailable", exc_info=True)
        return names


def keep(app_ids: List[str] | None = None) -> None:
    """The user asked for media themselves: what is paused stays paused --
    every app, or only *app_ids*."""
    with _lock:
        _kept.update(app_ids if app_ids else ["*"])


def kind_of(app_id: str) -> str:
    """"music" for Spotify, "video" for a browser, "other" otherwise."""
    app = app_id.lower()
    if "spotify" in app:
        return "music"
    if any(b in app for b in ("opera", "chrome", "edge", "firefox", "brave")):
        return "video"
    return "other"


async def _media_apps() -> List[str]:
    manager = await _manager()
    return [str(s.source_app_user_model_id) for s in manager.get_sessions()]


def pause_for_user(what: str = "all") -> List[str]:
    """The user's "pause the video / the music": pause it (or confirm the
    hold already has) and keep it paused past the hold."""
    if sys.platform != "win32":
        return []
    with _lock:
        pause()
        try:
            apps = _run(_media_apps())
        except Exception:  # noqa: BLE001
            apps = list(_paused)
        chosen = [a for a in apps if what == "all" or kind_of(a) == what]
        keep(chosen or ["*"])
        for app in chosen:
            if app not in _user_paused:
                _user_paused.append(app)
        return chosen


def resume_for_user(what: str = "all") -> List[str]:
    """The user's "resume / play it again": what their pause stopped, or,
    failing that, whatever of that kind is paused now."""
    if sys.platform != "win32":
        return []
    with _lock:
        try:
            apps = list(_user_paused) or _run(_media_apps())
            chosen = [a for a in apps if what == "all" or kind_of(a) == what]
            resumed = _run(_play(chosen))
        except Exception:  # noqa: BLE001
            logger.debug("Media resume unavailable", exc_info=True)
            return []
        for app in resumed:
            if app in _user_paused:
                _user_paused.remove(app)
            if app in _paused:
                _paused.remove(app)
        return resumed


def release() -> Dict[str, List[str]]:
    """End the hold: volumes back, paused players resumed (unless kept)."""
    from openjarvis.speech import ducking

    global _timer
    with _lock:
        if _timer is not None:
            _timer.cancel()
            _timer = None
        restored: List[str] = []
        if _ducked:
            try:
                by_pid = {pid: (name, original) for pid, name, original in _ducked}
                back: List[Tuple[Any, float, float]] = []
                for name, session in ducking._sessions(active_only=False):
                    pid = int(session.Process.pid)
                    if pid not in by_pid:
                        continue
                    current = float(session.SimpleAudioVolume.GetMasterVolume())
                    back.append((session, current, by_pid[pid][1]))
                    restored.append(name)
                ducking._fade(back, ducking.DEFAULT_FADE_MS)
                ducking._forget()
            except Exception:  # noqa: BLE001
                logger.debug("Media un-duck failed", exc_info=True)
        resumed: List[str] = []
        to_resume = [] if "*" in _kept else [a for a in _paused if a not in _kept]
        if to_resume and sys.platform == "win32":
            try:
                resumed = _run(_play(to_resume))
            except Exception:  # noqa: BLE001
                logger.debug("Media resume failed", exc_info=True)
        if resumed or restored:
            logger.info(
                "Media hold released: resumed %s, volume back %s", resumed, restored
            )
        _paused.clear()
        _ducked.clear()
        _kept.clear()
        return {"resumed": resumed, "restored": restored}


def state() -> Dict[str, Any]:
    with _lock:
        return {
            "paused": list(_paused),
            "ducked": [name for _, name, _ in _ducked],
            "kept": sorted(_kept),
        }


__all__ = [
    "HOLD_MAX_SECONDS",
    "duck",
    "keep",
    "kind_of",
    "pause",
    "pause_for_user",
    "release",
    "resume_for_user",
    "state",
]
