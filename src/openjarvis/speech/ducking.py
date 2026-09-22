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
import time
from contextlib import contextmanager
from typing import Any, Iterator, List, Tuple

logger = logging.getLogger(__name__)

DEFAULT_LEVEL = 0.35
DEFAULT_FADE_MS = 300
_FADE_STEPS = 6
# Sage's own players run as child processes with their own sessions; ducking
# them would duck the voice this exists to make audible.
_OWN_PLAYERS = {"ffplay.exe", "powershell.exe", "pwsh.exe"}
_STATE_ACTIVE = 1
# The levels a duck in progress will put back, on disk: a process killed
# mid-sentence (Stop Sage during a moment, a test run interrupted) never
# reached its restore, and each such kill left the browser at 35% of the
# time before -- Opera was found at 4%, three unrestored ducks deep.
_STATE_FILE = "ducking_state.json"


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
        if name.lower() in _OWN_PLAYERS:
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


def _remember(names_and_levels: List[Tuple[str, float]]) -> None:
    import json

    try:
        _state_path().write_text(json.dumps(dict(names_and_levels)), encoding="utf-8")
    except Exception:
        logger.debug("could not record ducking state", exc_info=True)


def _forget() -> None:
    try:
        path = _state_path()
        if path.exists():
            path.unlink()
    except Exception:
        logger.debug("could not clear ducking state", exc_info=True)


def restore_leftover() -> List[str]:
    """Put back the levels a duck that never finished had lowered.

    Run at server start and before every duck. Only a session still
    *below* its recorded level is touched: one the user has since turned
    up or down themselves is theirs.
    """
    import json

    try:
        path = _state_path()
        if not path.exists():
            return []
        recorded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    restored: List[str] = []
    if isinstance(recorded, dict):
        for name, session in _sessions(active_only=False):
            original = recorded.get(name)
            if not isinstance(original, (int, float)):
                continue
            try:
                current = float(session.SimpleAudioVolume.GetMasterVolume())
                if 0 < current < float(original) - 1e-6:
                    _set(session, float(original))
                    restored.append(name)
            except Exception:
                continue
    _forget()
    if restored:
        logger.info("restored volume after an interrupted duck: %s", restored)
    return restored


@contextmanager
def ducked(
    level: float = DEFAULT_LEVEL, fade_ms: int = DEFAULT_FADE_MS
) -> Iterator[List[str]]:
    """Hold every other app at *level* of its own volume for the block.

    Yields the names of the apps that were lowered, for the record. Any
    failure leaves the apps as they were and the block still runs: the
    voice must never depend on the ducking.
    """
    lowered: List[Tuple[Any, float, float]] = []
    names: List[str] = []
    try:
        restore_leftover()
        for name, session in _sessions():
            try:
                original = float(session.SimpleAudioVolume.GetMasterVolume())
            except Exception:
                continue
            if original <= 0:
                continue
            lowered.append((session, original, original * level))
            names.append(name)
        _remember([(name, orig) for name, (_, orig, _) in zip(names, lowered)])
        _fade(lowered, fade_ms)
    except Exception:
        logger.debug("Audio ducking unavailable", exc_info=True)
    try:
        yield names
    finally:
        try:
            _fade([(s, low, orig) for s, orig, low in lowered], fade_ms)
            for session, original, _ in lowered:
                try:
                    _set(session, original)
                except Exception:
                    pass
            _forget()
        except Exception:
            logger.debug("Audio un-ducking failed", exc_info=True)


__all__ = [
    "DEFAULT_FADE_MS",
    "DEFAULT_LEVEL",
    "ducked",
    "media_peak",
    "restore_leftover",
]
