"""Lower every other application's volume while Sage speaks, then restore it.

A greeting or reminder spoken over a film at full volume is either lost or
a shout. Windows keeps a volume per audio session -- what the Volume Mixer
shows -- so the fix is the car-radio one: the film comes down to a fraction
while Sage talks and goes back up when it stops. Nothing is muted and
nothing is paused; the user's own volume settings are never written, only
each session's level, and each is put back to exactly what it was.

Only the server-side voice uses this (moments, reminders, the briefing).
Replies spoken by the web UI cannot: a browser is one audio session, so
lowering the YouTube tab would lower Sage's own reply in the next tab.

Optional dependency (``pycaw``); without it, or off Windows, ``ducked`` is a
no-op so speech is never blocked by the thing meant to make it audible.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Tuple

logger = logging.getLogger(__name__)

DEFAULT_LEVEL = 0.35
DEFAULT_FADE_MS = 300
_FADE_STEPS = 6
# Sage's own players run as child processes with their own sessions; ducking
# them would duck the voice this exists to make audible.
_OWN_PLAYERS = {"ffplay.exe", "powershell.exe", "pwsh.exe"}
#: The desktop app speaks replies through its WebView2 children; their
#: sessions belong to Sage, not to "other apps" (``speech.media_hold`` turns
#: media down while the app speaks).
_OWN_APP = "sage-desktop.exe"
_STATE_ACTIVE = 1
#: A session whose process has gone. Windows still lists it for a moment, and
#: a restore that "fixed" it counted as done while the app's new session,
#: started at the lowered level, was left there (16 October).
_STATE_EXPIRED = 2
# The levels a duck in progress will put back, on disk: a process killed
# mid-sentence (Stop Sage during a moment, a test run interrupted) never
# reached its restore, and each such kill left the browser at 35% of the
# time before -- Opera was found at 4%, three unrestored ducks deep.
_STATE_FILE = "ducking_state.json"


def _is_sage_app(process: Any) -> bool:
    try:
        if process.name().lower() == _OWN_APP:
            return True
        return any(p.name().lower() == _OWN_APP for p in process.parents())
    except Exception:
        return False


def _sessions(active_only: bool = True) -> List[Tuple[str, Any]]:
    """(name, session) for every other app currently playing audio (or,
    with ``active_only`` off, every other app with a session at all)."""
    if sys.platform != "win32":
        return []
    try:
        import comtypes

        try:
            comtypes.CoInitialize()
        except Exception:
            pass
        from pycaw.pycaw import AudioUtilities
    except Exception:
        return []
    found: List[Tuple[str, Any]] = []
    own = os.getpid()
    for session in AudioUtilities.GetAllSessions():
        process = session.Process
        if process is None or process.pid == own:
            continue
        try:
            name = process.name()
        except Exception:
            continue
        if name.lower() in _OWN_PLAYERS or _is_sage_app(process):
            continue
        if session.State == _STATE_EXPIRED:
            continue
        if active_only and session.State != _STATE_ACTIVE:
            continue
        found.append((name, session))
    return found


def media_peak() -> float:
    """The loudest other app right now, 0-1; 0 when nothing is playing.

    The wake word reads this at a detection: while a video or music is
    audible, only the phrase spelt out confirms, not merely its shape --
    a lyric or a line of dialogue ("you see?") has the shape too.
    """
    try:
        from pycaw.pycaw import IAudioMeterInformation
    except Exception:
        return 0.0
    loudest = 0.0
    for _name, session in _sessions():
        try:
            meter = session._ctl.QueryInterface(IAudioMeterInformation)
            loudest = max(loudest, float(meter.GetPeakValue()))
        except Exception:
            continue
    return loudest


def _set(session: Any, level: float) -> None:
    session.SimpleAudioVolume.SetMasterVolume(max(0.0, min(1.0, level)), None)


def _fade(targets: List[Tuple[Any, float, float]], fade_ms: int) -> None:
    """Move each session from its first level to its second in small steps."""
    if not targets:
        return
    steps = max(1, _FADE_STEPS)
    pause = (fade_ms / 1000.0) / steps
    for step in range(1, steps + 1):
        for session, start, end in targets:
            try:
                _set(session, start + (end - start) * step / steps)
            except Exception:
                pass
        if pause > 0 and step < steps:
            time.sleep(pause)


def _state_path():
    from openjarvis.core.paths import get_config_dir

    return get_config_dir() / _STATE_FILE


# -- one record of every app's own volume, shared by every holder ----------
#
# Two things turn other apps down: the spoken announcements (``ducked``, to
# 35%) and the media hold while the user talks (``speech.media_hold``, to
# 10%). Each used to remember "the level before" on its own, so when one
# started while the other was in force it saved the already-lowered level as
# the app's own, and Opera was left at 10% (16 October) or 4% (three ducks
# deep). Now there is one record: an app's level is taken once, when the
# first holder turns it down, held at its own level times the lowest holder's
# share, and put back when the last holder lets go.

_reg_lock = threading.RLock()
#: key -> [app name, its own level]; key = "pid:<n>", or "name:<app>" when
#: the session has no process.
_held: Dict[str, List[Any]] = {}
#: holder -> the share of its own volume each app keeps for it.
_holders: Dict[str, float] = {}
#: How near a level must be to where a hold put it to count as still ours.
_NEAR = 0.02
#: A record of an app whose audio went away mid-hold is kept this long for
#: its return: Windows starts that app's next session at the level it last
#: had, so the next video in Opera came back at 10%.
_PENDING_SECONDS = 24 * 3600.0
_PENDING_WATCH_SECONDS = 600.0
_PENDING_POLL_SECONDS = 5.0
_pending_watch: Any = None


def _key(name: str, session: Any) -> str:
    process = getattr(session, "Process", None)
    pid = getattr(process, "pid", None)
    return f"pid:{pid}" if pid is not None else f"name:{name}"


def _level(session: Any) -> float:
    return float(session.SimpleAudioVolume.GetMasterVolume())


def _share() -> float:
    return min(_holders.values()) if _holders else 1.0


def _entries(share: float) -> List[Dict[str, Any]]:
    now = time.time()
    return [
        {
            "key": key,
            "name": name,
            "original": original,
            "low": original * share,
            "at": now,
        }
        for key, (name, original) in _held.items()
    ]


def _remember(entries: List[Dict[str, Any]]) -> None:
    import json

    try:
        _state_path().write_text(json.dumps({"entries": entries}), encoding="utf-8")
    except Exception:
        logger.debug("could not record ducking state", exc_info=True)


def _forget() -> None:
    try:
        path = _state_path()
        if path.exists():
            path.unlink()
    except Exception:
        logger.debug("could not clear ducking state", exc_info=True)


def _load_entries() -> List[Dict[str, Any]]:
    import json

    try:
        path = _state_path()
        if not path.exists():
            return []
        recorded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(recorded, dict) and isinstance(recorded.get("entries"), list):
        return [e for e in recorded["entries"] if isinstance(e, dict)]
    if isinstance(recorded, dict):  # the old {name: level} file
        return [
            {"key": f"name:{name}", "name": name, "original": level, "low": None}
            for name, level in recorded.items()
            if isinstance(level, (int, float))
        ]
    return []


def hold(holder: str, level: float, fade_ms: int = DEFAULT_FADE_MS) -> List[str]:
    """Turn every other app playing now down to *level* of its own volume
    for *holder*; returns the apps newly turned down. With several holders
    the lowest share wins; an app is never taken lower twice."""
    with _reg_lock:
        if not _holders:
            restore_leftover()
        before = _share()
        _holders[holder] = level
        share = _share()
        moves: List[Tuple[Any, float, float]] = []
        names: List[str] = []
        for name, session in _sessions():
            key = _key(name, session)
            try:
                current = _level(session)
            except Exception:
                continue
            if key in _held:
                original = float(_held[key][1])
                # The user turned it themselves since: theirs now.
                if abs(current - original * before) > _NEAR:
                    continue
            else:
                if current <= 0:
                    continue
                original = current
                _held[key] = [name, original]
                names.append(name)
            target = original * share
            if abs(current - target) > 1e-9:
                moves.append((session, current, target))
        if _held:
            _remember(_entries(share))
        _fade(moves, fade_ms)
        for session, _, target in moves:
            try:
                _set(session, target)
            except Exception:
                pass
        return names


def unhold(holder: str, fade_ms: int = DEFAULT_FADE_MS) -> List[str]:
    """Let go for *holder*. The last holder out puts every app back to its
    own level; returns the apps put back."""
    with _reg_lock:
        if holder not in _holders:
            return []
        before = _share()
        del _holders[holder]
        present = {_key(name, s): s for name, s in _sessions(active_only=False)}
        if _holders:
            # Another holder still wants them down, perhaps less far.
            share = _share()
            moves = []
            for key, (_name, original) in _held.items():
                session = present.get(key)
                if session is None:
                    continue
                try:
                    current = _level(session)
                except Exception:
                    continue
                if abs(current - original * before) <= _NEAR:
                    moves.append((session, current, original * share))
            _fade(moves, fade_ms)
            _remember(_entries(share))
            return []
        back: List[Tuple[Any, float, float]] = []
        restored: List[str] = []
        gone: List[Dict[str, Any]] = []
        for entry in _entries(before):
            session = present.get(entry["key"])
            if session is None:
                gone.append(entry)
                continue
            try:
                back.append((session, _level(session), float(entry["original"])))
                restored.append(entry["name"])
            except Exception:
                continue
        _fade(back, fade_ms)
        for session, _, original in back:
            try:
                _set(session, original)
            except Exception:
                pass
        _held.clear()
        if gone:
            # Its audio stopped while it was down; put it back when it returns.
            _remember(gone)
            _watch_pending()
        else:
            _forget()
        return restored


def restore_leftover() -> List[str]:
    """Put back the levels a hold that never finished had lowered.

    Run at server start, before the first hold, and for a while after a
    hold whose app's audio had gone away. Only an app still *below* its
    recorded level (the same session), or one back at exactly the level the
    hold left it at (a new session of that app), is touched: one the user
    has since turned up or down themselves is theirs.
    """
    with _reg_lock:
        entries = _load_entries()
        if not entries:
            return []
        now = time.time()
        sessions = _sessions(active_only=False)
        restored: List[str] = []
        left: List[Dict[str, Any]] = []
        for entry in entries:
            try:
                original = float(entry.get("original"))
            except (TypeError, ValueError):
                continue
            name = entry.get("name")
            key = entry.get("key")
            low = entry.get("low")
            fixed = seen = False
            for sname, session in sessions:
                try:
                    current = _level(session)
                except Exception:
                    continue
                skey = _key(sname, session)
                if skey == key:
                    seen = True
                    if 0 < current < original - 1e-6:
                        _set(session, original)
                        fixed = True
                elif (
                    sname == name
                    and isinstance(low, (int, float))
                    and skey not in _held
                    and abs(current - float(low)) <= _NEAR
                ):
                    _set(session, original)
                    fixed = True
            if fixed:
                restored.append(str(name))
            elif not seen and now - float(entry.get("at") or now) < _PENDING_SECONDS:
                left.append(entry)
        if left:
            _remember(left)
        else:
            _forget()
    if restored:
        logger.info("restored volume after an interrupted duck: %s", restored)
    return restored


def _watch_pending() -> None:
    """Look for a returning app at the hold's level for a few minutes."""
    global _pending_watch
    if _pending_watch is not None and _pending_watch.is_alive():
        return

    def watch() -> None:
        end = time.time() + _PENDING_WATCH_SECONDS
        while time.time() < end:
            time.sleep(_PENDING_POLL_SECONDS)
            with _reg_lock:
                if _holders:
                    continue
                restore_leftover()
                if not _load_entries():
                    return

    _pending_watch = threading.Thread(target=watch, name="duck-pending", daemon=True)
    _pending_watch.start()


def _reset() -> None:
    """Forget every holder and record (tests)."""
    with _reg_lock:
        _held.clear()
        _holders.clear()


#: Ducks already held by this thread. The chime plays inside the spoken
#: line's duck, and a second duck there would start by restoring the
#: "leftover" volumes -- the outer duck's own -- bringing the film back up
#: under the voice for a moment before lowering it again.
_depth = threading.local()
_counter = 0


@contextmanager
def ducked(
    level: float = DEFAULT_LEVEL, fade_ms: int = DEFAULT_FADE_MS
) -> Iterator[List[str]]:
    """Hold every other app at *level* of its own volume for the block.

    Yields the names of the apps that were lowered, for the record. Any
    failure leaves the apps as they were and the block still runs: the
    voice must never depend on the ducking. Inside another duck on the same
    thread it does nothing: the apps are already down.
    """
    if getattr(_depth, "n", 0) > 0:
        _depth.n += 1
        try:
            yield []
        finally:
            _depth.n -= 1
        return
    _depth.n = 1
    try:
        with _duck(level, fade_ms) as names:
            yield names
    finally:
        _depth.n = 0


@contextmanager
def _duck(level: float, fade_ms: int) -> Iterator[List[str]]:
    global _counter
    with _reg_lock:
        _counter += 1
        holder = f"speak-{_counter}"
    names: List[str] = []
    try:
        names = hold(holder, level, fade_ms)
    except Exception:
        logger.debug("Audio ducking unavailable", exc_info=True)
    try:
        yield names
    finally:
        try:
            unhold(holder, fade_ms)
        except Exception:
            logger.debug("Audio un-ducking failed", exc_info=True)


__all__ = [
    "DEFAULT_FADE_MS",
    "DEFAULT_LEVEL",
    "ducked",
    "hold",
    "media_peak",
    "restore_leftover",
    "unhold",
]
