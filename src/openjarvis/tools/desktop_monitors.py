"""Which physical screens exist, and which one is the main one.

Window coordinates are meaningless without this. On a two-monitor setup the
laptop panel commonly sits at a *negative* x offset, so "the window at x=-1400"
means nothing until you know a second screen lives to the left of the primary.

Pure ctypes against Windows' own API — no dependency, and it works before any
UI Automation is loaded.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from typing import List

#: MONITORINFOF_PRIMARY — Windows' own idea of the main display, which is
#: whichever one the user set as primary, not whichever is largest.
_PRIMARY_FLAG = 0x00000001


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", ctypes.c_wchar * 32),
    ]


class _DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", ctypes.c_wchar * 32),
        ("DeviceString", ctypes.c_wchar * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", ctypes.c_wchar * 128),
        ("DeviceKey", ctypes.c_wchar * 128),
    ]


@dataclass(frozen=True)
class Monitor:
    index: int
    device: str
    name: str
    width: int
    height: int
    x: int
    y: int
    is_primary: bool

    @property
    def area(self) -> int:
        return self.width * self.height

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height

    def describe(self, largest: bool) -> str:
        # "main" and "second" are the words the user actually says; "primary"
        # is Windows' term and does not survive being spoken aloud.
        role = "main" if self.is_primary else "second"
        extra = ", largest" if largest and not self.is_primary else ""
        label = f" — {self.name}" if self.name else ""
        return (
            f"Monitor {self.index} ({role}{extra}){label}: "
            f"{self.width}x{self.height} at ({self.x}, {self.y})"
        )


def _friendly_name(device: str) -> str:
    """The monitor's own description, when Windows will give one."""
    info = _DISPLAY_DEVICEW()
    info.cb = ctypes.sizeof(_DISPLAY_DEVICEW)
    try:
        if ctypes.windll.user32.EnumDisplayDevicesW(device, 0, ctypes.byref(info), 0):
            return info.DeviceString.strip()
    except Exception:
        pass
    return ""


def list_monitors() -> List[Monitor]:
    """Every attached display, primary first."""
    user32 = ctypes.windll.user32
    try:
        # Without this, every coordinate comes back in scaled units and a
        # 150%-scaled laptop panel reports a size it does not have.
        user32.SetProcessDPIAware()
    except Exception:
        pass

    found: List[Monitor] = []
    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_bool,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(_RECT),
        ctypes.c_double,
    )

    def _collect(handle, _hdc, _rect, _data):
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            rect = info.rcMonitor
            found.append(
                Monitor(
                    index=len(found) + 1,
                    device=info.szDevice,
                    name=_friendly_name(info.szDevice),
                    width=rect.right - rect.left,
                    height=rect.bottom - rect.top,
                    x=rect.left,
                    y=rect.top,
                    is_primary=bool(info.dwFlags & _PRIMARY_FLAG),
                )
            )
        return True

    user32.EnumDisplayMonitors(None, None, callback_type(_collect), 0)
    return number_from_primary(found)


def number_from_primary(found: List[Monitor]) -> List[Monitor]:
    """Renumber so the main screen is Monitor 1.

    Windows enumerates by device order (DISPLAY1, DISPLAY2), which has nothing
    to do with how a person counts their screens. On this user's machine the
    laptop panel is DISPLAY1 while the external they call "main" is the
    primary — so device numbering made "look at my second monitor" capture the
    main one and describe it confidently. A wrong answer that looks like a
    working feature is worse than an error.
    """
    ordered = sorted(found, key=lambda m: (not m.is_primary, m.index))
    return [
        Monitor(**{**monitor.__dict__, "index": position})
        for position, monitor in enumerate(ordered, start=1)
    ]


def monitor_for(monitors: List[Monitor], x: int, y: int) -> Monitor | None:
    """Which screen a point sits on."""
    for monitor in monitors:
        if monitor.contains(x, y):
            return monitor
    return None


def describe_monitors(monitors: List[Monitor]) -> str:
    if not monitors:
        return "No displays detected."
    largest = max(monitors, key=lambda m: m.area)
    lines = [m.describe(largest=m is largest) for m in monitors]
    return "\n".join(lines)


__all__ = [
    "Monitor",
    "describe_monitors",
    "list_monitors",
    "monitor_for",
    "number_from_primary",
]
