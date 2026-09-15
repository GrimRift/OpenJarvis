"""Whether this is a bad moment to start a conversation (M37).

M36 chose not to model "busy": presence was enough for a greeting. Starting
a conversation is different -- it is the thing a considerate person does
not do to someone in a call or watching a film. The signals, all heuristic
and all chosen by the user:

- **Sage is mid-turn** -- a reply being spoken, the microphone transmitting.
- **A full-screen app in front** -- the foreground window covers its
  monitor: a film, a game. The same signal Windows uses for its own
  automatic Do Not Disturb.
- **A call in the browser** -- the foreground title names Meet or a
  Messenger call.
- **Teams with the microphone open** -- Windows records which apps have
  the microphone; Teams counts only while it is actually in a call.

Recent keyboard or mouse input does *not* count, by the user's decision:
Sage may speak into a working lull. The quiet commands are the backstop
for everything these miss.

Each sensor is injectable so the policy is tested without a desktop.
"""

from __future__ import annotations

import logging
import sys
from typing import Callable, Dict, List, Optional, Sequence

from openjarvis.core.activity import Activity

logger = logging.getLogger(__name__)

DEFAULT_CALL_TITLES = ("Meet", "Messenger call", "Messenger Call")
DEFAULT_CALL_MIC_APPS = ("MSTeams", "Teams")


def foreground_window() -> Optional[Dict[str, object]]:
    """The window in front, with its rect, or None."""
    try:
        from openjarvis.tools.desktop_awareness import _visible_windows

        for window in _visible_windows():
            if window.get("foreground"):
                return window
    except Exception:
        return None
    return None


def monitor_rect_for(window: Dict[str, object]) -> Optional[tuple[int, int]]:
    """(width, height) of the monitor the window is on, or None."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class _RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        class _MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_ulong),
                ("rcMonitor", _RECT),
                ("rcWork", _RECT),
                ("dwFlags", ctypes.c_ulong),
            ]

        user32 = ctypes.windll.user32
        handle = int(window.get("handle") or 0)
        monitor = user32.MonitorFromWindow(handle, 2)  # MONITOR_DEFAULTTONEAREST
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        rect = info.rcMonitor
        return (rect.right - rect.left, rect.bottom - rect.top)
    except Exception:
        return None


def is_fullscreen(
    window: Optional[Dict[str, object]], monitor: Optional[tuple[int, int]]
) -> bool:
    """Pure: a window that covers its whole monitor. A maximised window
    does not (its frame sits outside the work area, its size is larger than
    the monitor); a borderless full-screen one matches exactly."""
    if not window or not monitor:
        return False
    width = int(window.get("width") or 0)
    height = int(window.get("height") or 0)
    return width == monitor[0] and height == monitor[1]


def apps_using_microphone() -> List[str]:
    """Names of apps Windows says have the microphone open right now.

    Windows keeps a consent store per app with ``LastUsedTimeStop``; zero
    means the app has started using the microphone and not stopped.
    """
    if sys.platform != "win32":
        return []
    found: List[str] = []
    try:
        import winreg

        base = (
            r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
            r"\ConsentStore\microphone"
        )
        for sub in ("", r"\NonPackaged"):
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + sub)
            except OSError:
                continue
            with key:
                index = 0
                while True:
                    try:
                        name = winreg.EnumKey(key, index)
                    except OSError:
                        break
                    index += 1
                    if name == "NonPackaged":
                        continue
                    try:
                        with winreg.OpenKey(key, name) as app:
                            stop, _ = winreg.QueryValueEx(app, "LastUsedTimeStop")
                            if int(stop) == 0:
                                found.append(name)
                    except OSError:
                        continue
    except Exception:
        logger.debug("Microphone consent store unreadable", exc_info=True)
    return found


def busy_reasons(
    activity: Activity,
    *,
    call_titles: Sequence[str] = DEFAULT_CALL_TITLES,
    call_mic_apps: Sequence[str] = DEFAULT_CALL_MIC_APPS,
    foreground: Callable[[], Optional[Dict[str, object]]] = foreground_window,
    monitor: Callable[
        [Dict[str, object]], Optional[tuple[int, int]]
    ] = monitor_rect_for,
    mic_apps: Callable[[], List[str]] = apps_using_microphone,
) -> List[str]:
    """Every reason this is a bad moment; empty means it is not."""
    reasons: List[str] = []
    if activity.sage_mid_turn:
        reasons.append("Sage is mid-turn")
    window = foreground()
    if window:
        title = str(window.get("title") or "")
        if any(phrase.lower() in title.lower() for phrase in call_titles if phrase):
            reasons.append(f"a call in front: {title[:40]}")
        if is_fullscreen(window, monitor(window)):
            reasons.append(f"full-screen: {title[:40]}")
    using = mic_apps()
    for app in using:
        if any(name.lower() in app.lower() for name in call_mic_apps if name):
            reasons.append(f"{app} has the microphone")
            break
    return reasons


__all__ = [
    "DEFAULT_CALL_MIC_APPS",
    "DEFAULT_CALL_TITLES",
    "apps_using_microphone",
    "busy_reasons",
    "foreground_window",
    "is_fullscreen",
    "monitor_rect_for",
]
