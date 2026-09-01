"""Desktop awareness — read-only. M32's first layer.

What is open, what is in front, and what is inside a chosen window. No clicks,
no keystrokes, no continuous capture: this layer ships and is proven before any
action is added, which is the sequence M32 was scoped with.

Two things it reports that a naive version would not:

* **Which monitor.** Window coordinates are meaningless on a two-screen setup —
  a laptop panel commonly sits at a negative x offset, so "x=-1400" says
  nothing until you know a second screen lives to the left of the primary.
* **Redaction.** Window titles alone leak secrets. The very first enumeration
  run on this machine returned a Notepad window whose *title was a credential*.
  Nobody has to open the file for that to reach the model.

Everything a window reports — its title, its control labels — is text somebody
else wrote. It reaches the model as a tool result, so it passes through the
executor's injection labelling like any other untrusted content.
"""

from __future__ import annotations

import ctypes
from typing import Any, Dict, List

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.security.screen_redaction import redact_title
from openjarvis.tools._stubs import BaseTool, ToolSpec
from openjarvis.tools.desktop_monitors import (
    describe_monitors,
    list_monitors,
    monitor_for,
)

#: A browser window's tree runs to thousands of nodes. These bound what reaches
#: the model: enough to understand a window, not enough to bury the question.
MAX_ELEMENTS = 120
MAX_DEPTH = 5

#: Windows that exist for the shell rather than for the user.
_SHELL_TITLES = frozenset(
    {"Program Manager", "Windows Input Experience", "Windows Shell Experience Host"}
)


def _visible_windows() -> List[Dict[str, Any]]:
    """Top-level windows with a title, in z-order."""
    user32 = ctypes.windll.user32
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    foreground = user32.GetForegroundWindow()
    windows: List[Dict[str, Any]] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _collect(handle, _param):
        if not user32.IsWindowVisible(handle):
            return True
        length = user32.GetWindowTextLengthW(handle)
        if not length:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, buffer, length + 1)
        title = buffer.value
        if title in _SHELL_TITLES:
            return True
        rect = _RECT()
        user32.GetWindowRect(handle, ctypes.byref(rect))
        windows.append(
            {
                "title": title,
                "handle": int(handle) if handle else 0,
                "x": rect.left,
                "y": rect.top,
                "width": rect.right - rect.left,
                "height": rect.bottom - rect.top,
                "foreground": bool(handle == foreground),
            }
        )
        return True

    user32.EnumWindows(callback_type(_collect), 0)
    return windows


@ToolRegistry.register("list_windows")
class ListWindowsTool(BaseTool):
    """Report open windows, which is in front, and which screen each is on."""

    tool_id = "list_windows"
    is_local = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="list_windows",
            description=(
                "List the visible windows on the user's desktop, which one is "
                "in the foreground, and which monitor each sits on. Read-only. "
                "Titles that look like passwords or banking are redacted."
            ),
            parameters={"type": "object", "properties": {}},
            category="desktop",
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            monitors = list_monitors()
            windows = _visible_windows()
        except Exception as exc:
            return ToolResult(
                tool_name=self.tool_id,
                content=f"Could not read the desktop: {exc}",
                success=False,
            )

        lines = [describe_monitors(monitors), ""]
        if not windows:
            lines.append("No visible windows.")
        else:
            lines.append(f"{len(windows)} visible window(s):")
        redacted = 0
        for window in windows:
            shown = redact_title(window["title"])
            if shown != window["title"]:
                redacted += 1
            # Report by the window's centre, not its corner: a window straddling
            # two screens belongs to the one showing most of it.
            screen = monitor_for(
                monitors,
                window["x"] + window["width"] // 2,
                window["y"] + window["height"] // 2,
            )
            where = f"monitor {screen.index}" if screen else "off-screen"
            mark = " [FOREGROUND]" if window["foreground"] else ""
            lines.append(f"  - {shown} — {where}{mark}")

        return ToolResult(
            tool_name=self.tool_id,
            content="\n".join(lines),
            success=True,
            metadata={
                "window_count": len(windows),
                "monitor_count": len(monitors),
                "redacted_count": redacted,
            },
        )


@ToolRegistry.register("inspect_window")
class InspectWindowTool(BaseTool):
    """Read the control tree of one window.

    Accessibility metadata rather than pixels, which is what M32's scope asks
    for: it names buttons and fields exactly, costs no vision call, and does
    not send a picture of the screen anywhere.
    """

    tool_id = "inspect_window"
    is_local = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="inspect_window",
            description=(
                "Read the controls inside one window — buttons, fields, labels "
                "— by its title. Read-only: nothing is clicked or typed. Use "
                "this before asking for a screenshot; it is faster, exact, and "
                "keeps the screen off the network."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": (
                            "Window title, or enough of it to identify one. "
                            "Call list_windows first if unsure."
                        ),
                    },
                    "max_elements": {
                        "type": "integer",
                        "description": (
                            f"Cap on controls returned (default {MAX_ELEMENTS})."
                        ),
                    },
                },
                "required": ["title"],
            },
            category="desktop",
            timeout_seconds=45.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        wanted = str(params.get("title", "") or "").strip()
        if not wanted:
            return ToolResult(
                tool_name=self.tool_id,
                content="Name the window to inspect.",
                success=False,
            )
        try:
            limit = int(params.get("max_elements") or MAX_ELEMENTS)
        except (TypeError, ValueError):
            limit = MAX_ELEMENTS
        limit = max(1, min(limit, MAX_ELEMENTS))

        try:
            import uiautomation as auto
        except ImportError:
            return ToolResult(
                tool_name=self.tool_id,
                content=(
                    "Window inspection needs the uiautomation package: "
                    "uv sync --extra desktop-awareness"
                ),
                success=False,
            )

        # A missing window must not hang the turn waiting for one to appear.
        auto.SetGlobalSearchTimeout(2)
        try:
            root = auto.GetRootControl()
            target = None
            for child in root.GetChildren():
                name = child.Name or ""
                if wanted.lower() in name.lower():
                    target = child
                    break
            if target is None:
                return ToolResult(
                    tool_name=self.tool_id,
                    content=(
                        f"No open window matching {wanted!r}. "
                        "Call list_windows to see what is open."
                    ),
                    success=False,
                )

            title = target.Name or ""
            shown = redact_title(title)
            if shown != title:
                return ToolResult(
                    tool_name=self.tool_id,
                    content=(
                        f"{shown} — this window looks like a password manager "
                        "or banking window, so its contents were not read."
                    ),
                    success=False,
                    metadata={"redacted": True},
                )

            collected: List[str] = []
            truncated = self._walk(target, collected, limit, depth=0)
        except Exception as exc:
            return ToolResult(
                tool_name=self.tool_id,
                content=f"Could not inspect {wanted!r}: {exc}",
                success=False,
            )

        header = f"Controls in {shown!r} ({len(collected)} shown"
        header += ", truncated)" if truncated else ")"
        return ToolResult(
            tool_name=self.tool_id,
            content="\n".join([header, *collected]) if collected else
            f"{shown!r} reports no readable controls.",
            success=True,
            metadata={"element_count": len(collected), "truncated": truncated},
        )

    def _walk(
        self,
        control: Any,
        out: List[str],
        limit: int,
        depth: int,
    ) -> bool:
        """Depth-first, bounded. Returns True if anything was left out."""
        if depth > MAX_DEPTH:
            return True
        try:
            children = control.GetChildren()
        except Exception:
            return False
        truncated = False
        for child in children:
            if len(out) >= limit:
                return True
            try:
                name = (child.Name or "").strip()
                kind = child.ControlTypeName
            except Exception:
                continue
            # Unnamed containers carry no information for the model; their
            # children still do, so descend without spending a line on them.
            if name:
                out.append(f"{'  ' * depth}- {kind}: {redact_title(name)}")
            if self._walk(child, out, limit, depth + 1):
                truncated = True
        return truncated


__all__ = ["InspectWindowTool", "ListWindowsTool", "MAX_DEPTH", "MAX_ELEMENTS"]
