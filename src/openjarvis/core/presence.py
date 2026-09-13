"""Presence: whether anyone is at the desk.

Sage's unprompted life is two scheduled moments a day; everything else waits
to be asked. Before it can speak first without talking to an empty room, it
has to know whether someone is there. This module answers that one question
and nothing else. It changes nothing visible on its own -- it is the substrate
the later M36 phases consult before deciding to say anything.

Evidence, in order of trust:

- **Idle time**, from ``GetLastInputInfo``: seconds since the last keyboard
  or mouse input anywhere on the machine. Cheap, needs no dependency, and is
  the whole answer for v1.
- **The foreground window title**, recorded as context rather than judged.
  "Busy" is deliberately not modelled yet; the user chose presence as the sole
  gate.

The camera is not used. That is deferred by explicit decision, not rejected.

Settings live in ``OPENJARVIS_DATA/presence.json`` rather than in
``config.toml``. The server has no safe way to rewrite the hand-commented
TOML -- one malformed line there breaks every credential -- and a sidecar
file can be re-read on every poll, so the master switch takes effect
immediately and needs no restart.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from openjarvis.core.config import DEFAULT_CONFIG_DIR

STATE_PRESENT = "present"
STATE_AWAY = "away"
STATE_DISABLED = "disabled"
STATE_UNKNOWN = "unknown"

_SETTINGS_FILE = "presence.json"


# -- Settings ----------------------------------------------------------------


@dataclass
class PresenceSettings:
    """The master switch and the thresholds behind it.

    ``enabled`` is off by default: with it off, Sage behaves exactly as it did
    before M36. Everything in the milestone hangs beneath this one switch.
    """

    enabled: bool = False
    # Seconds without input before the desk is considered empty. Five
    # minutes: long enough to read something without touching the mouse,
    # short enough that "welcome back" is not said to someone who never left.
    idle_threshold_seconds: int = 300
    poll_interval_seconds: int = 15
    # Episodes: the nightly diary entry (M36 phase 2). Under the master switch
    # like everything else here; the model is pinned the way the digest pins
    # its own, because a plain scheduled run would otherwise land on the local
    # default, which has invented actions it never took.
    episodes_enabled: bool = True
    episodes_model: str = "gpt-5.6-luna"
    episodes_engine: str = "cloud"
    # Local hour the entry is written. Late enough to cover the evening,
    # early enough that a midnight conversation lands in tomorrow's entry.
    episodes_hour_local: int = 23

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def settings_path(config_dir: Optional[Path] = None) -> Path:
    return (config_dir or DEFAULT_CONFIG_DIR) / _SETTINGS_FILE


def load_settings(config_dir: Optional[Path] = None) -> PresenceSettings:
    """Read settings, tolerating a missing or damaged file as defaults."""
    try:
        raw = json.loads(settings_path(config_dir).read_text(encoding="utf-8"))
    except Exception:
        return PresenceSettings()
    if not isinstance(raw, dict):
        return PresenceSettings()
    settings = PresenceSettings()
    for key in ("enabled", "episodes_enabled"):
        if isinstance(raw.get(key), bool):
            setattr(settings, key, raw[key])
    for key in ("idle_threshold_seconds", "poll_interval_seconds"):
        value = raw.get(key)
        if isinstance(value, (int, float)) and value > 0:
            setattr(settings, key, int(value))
    for key in ("episodes_model", "episodes_engine"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            setattr(settings, key, value.strip())
    hour = raw.get("episodes_hour_local")
    if isinstance(hour, int) and 0 <= hour <= 23:
        settings.episodes_hour_local = hour
    return settings


def save_settings(
    settings: PresenceSettings, config_dir: Optional[Path] = None
) -> None:
    path = settings_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings.to_dict(), indent=2), encoding="utf-8")


# -- Sensors -----------------------------------------------------------------


def idle_seconds() -> Optional[float]:
    """Seconds since the last keyboard or mouse input, or None off Windows."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class _LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        # Both are millisecond tick counts that wrap every ~49 days; the
        # unsigned subtraction handles the wrap.
        now = ctypes.windll.kernel32.GetTickCount()
        return ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return None


def foreground_title() -> Optional[str]:
    """Title of the window in front, or None if it cannot be read."""
    try:
        from openjarvis.tools.desktop_awareness import _visible_windows

        for window in _visible_windows():
            if window.get("foreground"):
                return str(window.get("title") or "") or None
    except Exception:
        return None
    return None


# -- Decision ----------------------------------------------------------------


def decide_state(idle: Optional[float], threshold_seconds: int) -> str:
    """Pure: idle time in, state out. Unknown idle time is not 'away'.

    A sensor failure must not read as an empty desk, or a moment could fire
    -- or be suppressed -- on the strength of nothing.
    """
    if idle is None:
        return STATE_UNKNOWN
    return STATE_AWAY if idle >= threshold_seconds else STATE_PRESENT


# -- Monitor -----------------------------------------------------------------


@dataclass
class PresenceSnapshot:
    state: str
    idle_seconds: Optional[float]
    foreground: Optional[str]
    since: Optional[float]
    last_present_at: Optional[float]
    last_away_at: Optional[float]
    checked_at: Optional[float]
    threshold_seconds: int
    reason: str = ""
    # The most recent completed absence. `since` moves to the return time the
    # moment someone comes back, so without this the length of the absence
    # is lost -- and "welcome back" needs to know whether it was eight
    # minutes or eight hours.
    last_absence_started_at: Optional[float] = None
    last_absence_ended_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PresenceMonitor:
    """Polls the sensors and keeps the state plus when it last changed.

    Settings are re-read on every poll so the master switch is live. The
    sensors and clock are injectable so the transitions can be tested without
    a desk to walk away from.
    """

    config_dir: Optional[Path] = None
    idle_sensor: Callable[[], Optional[float]] = idle_seconds
    foreground_sensor: Callable[[], Optional[str]] = foreground_title
    clock: Callable[[], float] = time.time

    _state: str = field(default=STATE_UNKNOWN, init=False)
    _since: Optional[float] = field(default=None, init=False)
    _last_present_at: Optional[float] = field(default=None, init=False)
    _last_away_at: Optional[float] = field(default=None, init=False)
    _idle: Optional[float] = field(default=None, init=False)
    _foreground: Optional[str] = field(default=None, init=False)
    _checked_at: Optional[float] = field(default=None, init=False)
    _reason: str = field(default="", init=False)
    _last_absence: Optional[tuple[float, float]] = field(default=None, init=False)
    _thread: Optional[threading.Thread] = field(default=None, init=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def settings(self) -> PresenceSettings:
        return load_settings(self.config_dir)

    def poll(self) -> PresenceSnapshot:
        """Take one reading and update the state. Safe to call directly."""
        settings = self.settings()
        now = self.clock()
        with self._lock:
            if not settings.enabled:
                self._transition(STATE_DISABLED, now, "presence is switched off")
                self._idle = None
                self._foreground = None
                self._checked_at = now
                return self._snapshot(settings)

            idle = self.idle_sensor()
            state = decide_state(idle, settings.idle_threshold_seconds)
            if state == STATE_UNKNOWN:
                reason = "idle time could not be read"
            elif state == STATE_AWAY:
                reason = f"no input for {int(idle or 0)}s"
            else:
                reason = f"input {int(idle or 0)}s ago"
            self._idle = idle
            self._foreground = self.foreground_sensor()
            self._checked_at = now
            self._transition(state, now, reason)
            return self._snapshot(settings)

    def _transition(self, state: str, now: float, reason: str) -> None:
        if state != self._state:
            if self._state == STATE_AWAY and state == STATE_PRESENT and self._since:
                self._last_absence = (self._since, now)
            self._state = state
            self._since = now
        self._reason = reason
        if state == STATE_PRESENT:
            self._last_present_at = now
        elif state == STATE_AWAY:
            self._last_away_at = now

    def _snapshot(self, settings: PresenceSettings) -> PresenceSnapshot:
        absence = self._last_absence
        return PresenceSnapshot(
            state=self._state,
            idle_seconds=self._idle,
            foreground=self._foreground,
            since=self._since,
            last_present_at=self._last_present_at,
            last_away_at=self._last_away_at,
            checked_at=self._checked_at,
            threshold_seconds=settings.idle_threshold_seconds,
            reason=self._reason,
            last_absence_started_at=absence[0] if absence else None,
            last_absence_ended_at=absence[1] if absence else None,
        )

    def snapshot(self) -> PresenceSnapshot:
        with self._lock:
            return self._snapshot(self.settings())

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="presence-monitor", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception:
                # A sensor hiccup must not kill the loop; the next poll
                # reports unknown rather than the thread quietly dying.
                pass
            interval = max(1, self.settings().poll_interval_seconds)
            self._stop.wait(interval)


__all__ = [
    "PresenceMonitor",
    "PresenceSettings",
    "PresenceSnapshot",
    "STATE_AWAY",
    "STATE_DISABLED",
    "STATE_PRESENT",
    "STATE_UNKNOWN",
    "decide_state",
    "foreground_title",
    "idle_seconds",
    "load_settings",
    "save_settings",
]
