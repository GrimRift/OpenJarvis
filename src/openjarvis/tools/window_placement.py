"""Put a window on a chosen monitor.

Hands-free placement is the whole point of "play it on my second screen": the
video is no use on the laptop panel if the user then has to reach over and
drag it. Kept separate from the media tools because the same call serves
"move Obsidian to my other monitor", which has nothing to do with playback.

Monitor numbers come from ``desktop_monitors.number_from_primary``, so
"monitor 2" means the same thing here as it does when Sage describes what is
on screen. Windows' own device order does not match how a person counts
screens and using it directly put a capture on the wrong display once
already.

Pure ctypes. Moving a window is not UI automation — nothing is clicked, no
input is synthesised, and the target application is never touched.
"""

from __future__ import annotations

import ctypes
from typing import Any, List, Optional, Tuple

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_SW_RESTORE = 9
_SW_MAXIMIZE = 3
_SWP_NOZORDER = 0x0004
_SWP_SHOWWINDOW = 0x0040


def _monitors() -> List[Any]:
    from openjarvis.tools.desktop_monitors import list_monitors, number_from_primary

    return number_from_primary(list_monitors())


def find_window(title: str) -> Tuple[int, str]:
    """Best visible window whose title contains *title*, case-insensitively.

    Returns ``(handle, actual_title)``, or ``(0, "")``. An exact match wins
    over a substring so "Opera" does not pick "Opera GX Installer" when the
    real browser is open.
    """
    from openjarvis.tools.desktop_awareness import _visible_windows

    wanted = (title or "").strip().lower()
    if not wanted:
        return 0, ""
    candidates = [
        window
        for window in _visible_windows()
        if wanted in (window.get("title") or "").lower()
    ]
    if not candidates:
        return 0, ""
    candidates.sort(
        key=lambda window: (
            (window.get("title") or "").lower() != wanted,
            not window.get("foreground"),
            len(window.get("title") or ""),
        )
    )
    best = candidates[0]
    return int(best.get("handle") or 0), best.get("title") or ""


def place_window(handle: int, monitor: int, *, maximize: bool = True) -> str:
    """Move *handle* onto *monitor*. Returns a human sentence describing it.

    Restores first: a maximized window ignores SetWindowPos and silently stays
    where it is, which reads as the move having failed for no reason.
    """
    found = _monitors()
    if not found:
        raise RuntimeError("no monitors detected")
    target = next((item for item in found if item.index == monitor), None)
    if target is None:
        available = ", ".join(str(item.index) for item in found)
        raise ValueError(f"no monitor {monitor}; available: {available}")

    user32 = ctypes.windll.user32
    user32.ShowWindow(handle, _SW_RESTORE)
    user32.SetWindowPos(
        handle,
        None,
        target.x,
        target.y,
        target.width,
        target.height,
        _SWP_NOZORDER | _SWP_SHOWWINDOW,
    )
    if maximize:
        user32.ShowWindow(handle, _SW_MAXIMIZE)
    user32.SetForegroundWindow(handle)
    role = "main" if target.is_primary else "second"
    return f"monitor {target.index} ({role})"


def place_by_title(title: str, monitor: int, *, maximize: bool = True) -> str:
    handle, actual = find_window(title)
    if not handle:
        raise LookupError(f"no open window matching {title!r}")
    where = place_window(handle, monitor, maximize=maximize)
    return f"Moved {actual!r} to {where}."


@ToolRegistry.register("move_window")
class MoveWindowTool(BaseTool):
    """Move an open window to a specific monitor."""

    tool_id = "move_window"
    is_local = True

    def __init__(self, allowed_dirs: Optional[List[str]] = None) -> None:
        super().__init__()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="move_window",
            description=(
                "Move an already-open window to a specific monitor and "
                "maximize it there. Use for 'put this on my other screen'. "
                "Monitor 1 is the user's main screen. Call list_windows first "
                "if unsure of the exact title."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "window": {
                        "type": "string",
                        "description": "Part of the window title, e.g. 'YouTube'.",
                    },
                    "monitor": {
                        "type": "integer",
                        "description": "Monitor number. 1 is the main screen.",
                    },
                    "maximize": {
                        "type": "boolean",
                        "description": "Maximize on arrival. Default true.",
                    },
                },
                "required": ["window", "monitor"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        window = str(params.get("window") or "").strip()
        monitor = params.get("monitor")
        maximize = params.get("maximize", True)
        if not window:
            return self._fail("A window title is required.")
        try:
            monitor = int(monitor)
        except (TypeError, ValueError):
            return self._fail("monitor must be a number.")
        try:
            message = place_by_title(window, monitor, maximize=bool(maximize))
        except (LookupError, ValueError, RuntimeError) as error:
            return self._fail(str(error))
        except Exception as error:  # pragma: no cover — ctypes surface
            return self._fail(f"could not move the window: {error}")
        return ToolResult(tool_name=self.tool_id, content=message, success=True)

    def _fail(self, reason: str) -> ToolResult:
        return ToolResult(tool_name=self.tool_id, content=reason, success=False)


@ToolRegistry.register("list_monitors")
class ListMonitorsTool(BaseTool):
    """Name the screens so the user and Sage count them the same way."""

    tool_id = "list_monitors"
    is_local = True

    def __init__(self, allowed_dirs: Optional[List[str]] = None) -> None:
        super().__init__()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="list_monitors",
            description=(
                "List the user's monitors with their numbers, sizes and which "
                "is the main one. Use before moving a window if unsure."
            ),
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params: Any) -> ToolResult:
        from openjarvis.tools.desktop_monitors import describe_monitors

        try:
            found = _monitors()
        except Exception as error:  # pragma: no cover — ctypes surface
            return ToolResult(
                tool_name=self.tool_id,
                content=f"could not read the monitor layout: {error}",
                success=False,
            )
        return ToolResult(
            tool_name=self.tool_id,
            content=describe_monitors(found),
            success=True,
            metadata={"count": len(found)},
        )


__all__ = ["find_window", "place_by_title", "place_window"]
