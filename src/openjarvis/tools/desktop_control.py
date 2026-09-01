"""Acting on the desktop. M32's second layer.

The read-only layer (`desktop_awareness`) ships first and stays read-only —
there is an AST test forbidding any interaction call in that module. Everything
that *changes* something lives here, so the boundary is a file, not a habit.

Three ways to act, in order of how much is known about what is being touched:

1. ``click_control`` — resolve a control **by name** in a named window and use
   its UI Automation pattern (Invoke, Toggle, ExpandCollapse). The element is
   found and verified before anything happens.
2. ``type_text`` — set a named field's value, or send keystrokes to the window
   that has focus.
3. ``click_at`` — a raw coordinate click, for apps that expose no accessibility
   tree at all. This is the only one where Sage presses something it could not
   name or verify, and it is last on purpose.

Guards, per the user's decisions: any window may be acted on **except** ones
the redaction rule considers sensitive, and anything whose name looks
destructive needs a confirmation first. The destructive check is a word list —
it raises the cost of common mistakes and is not a safety boundary.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.security import confirmations
from openjarvis.security.destructive_actions import describe_reason, looks_destructive
from openjarvis.security.screen_redaction import is_sensitive_title
from openjarvis.tools._stubs import BaseTool, ToolSpec

#: How deep to hunt for a named control before giving up.
MAX_SEARCH_DEPTH = 8


def _uia() -> Any:
    import uiautomation as auto

    # A missing window must not hang a turn waiting for one to appear.
    auto.SetGlobalSearchTimeout(2)
    return auto


def _find_window(auto: Any, title: str) -> Any:
    root = auto.GetRootControl()
    wanted = title.lower()
    for child in root.GetChildren():
        if wanted in (child.Name or "").lower():
            return child
    return None


def _find_control(window: Any, name: str, depth: int = 0) -> Any:
    """First control whose name contains *name*, breadth-ish and bounded."""
    if depth > MAX_SEARCH_DEPTH:
        return None
    wanted = name.lower()
    try:
        children = window.GetChildren()
    except Exception:
        return None
    for child in children:
        if wanted in (child.Name or "").lower():
            return child
    for child in children:
        found = _find_control(child, name, depth + 1)
        if found is not None:
            return found
    return None


def _patterns(control: Any) -> List[str]:
    """Which UI Automation patterns this control actually exposes."""
    available = []
    for label, getter in (
        ("Invoke", "GetInvokePattern"),
        ("Toggle", "GetTogglePattern"),
        ("Expand", "GetExpandCollapsePattern"),
        ("Value", "GetValuePattern"),
    ):
        try:
            if getattr(control, getter)() is not None:
                available.append(label)
        except Exception:
            pass
    return available


class _DesktopActionTool(BaseTool):
    """Shared guards. Every action tool answers the same two questions first."""

    is_local = False

    def _refuse_sensitive(self, title: str) -> Optional[ToolResult]:
        if is_sensitive_title(title):
            return ToolResult(
                tool_name=self.tool_id,
                content=(
                    "That window looks like a password manager or banking "
                    "window, so nothing was clicked or typed."
                ),
                success=False,
                metadata={"redacted": True},
            )
        return None

    def _needs_confirmation(self, label: str, params: Any) -> Optional[ToolResult]:
        """Gate a destructive-looking action on a real user turn.

        Checked per call rather than through ``ToolSpec.requires_confirmation``,
        which is static: pressing "Bold" and pressing "Delete Account" are the
        same tool and must not be treated the same way.
        """
        if not looks_destructive(label):
            return None
        if confirmations.decide(self.tool_id, params):
            return None
        return ToolResult(
            tool_name=self.tool_id,
            content=(
                f"Confirmation required before this: {describe_reason(label)}. "
                f"Tell the user exactly what it would do, with these arguments: "
                f"{params}. Do not claim it has happened. If they agree, call "
                f"again with identical arguments — their reply is what "
                f"authorises it."
            ),
            success=False,
            metadata={"requires_confirmation": True, "target": label},
        )

    def _resolve(
        self, window_title: str, control_name: str
    ) -> Tuple[Any, Any, Optional[ToolResult]]:
        try:
            auto = _uia()
        except ImportError:
            return None, None, ToolResult(
                tool_name=self.tool_id,
                content=(
                    "Desktop control needs the uiautomation package: "
                    "uv sync --extra desktop-awareness"
                ),
                success=False,
            )
        window = _find_window(auto, window_title)
        if window is None:
            return None, None, ToolResult(
                tool_name=self.tool_id,
                content=(
                    f"No open window matching {window_title!r}. "
                    "Call list_windows to see what is open."
                ),
                success=False,
            )
        refusal = self._refuse_sensitive(window.Name or "")
        if refusal is not None:
            return None, None, refusal
        if not control_name:
            return window, None, None
        control = _find_control(window, control_name)
        if control is None:
            return window, None, ToolResult(
                tool_name=self.tool_id,
                content=(
                    f"No control named {control_name!r} in "
                    f"{window_title!r}. Call inspect_window to see what is "
                    "there."
                ),
                success=False,
            )
        return window, control, None


@ToolRegistry.register("click_control")
class ClickControlTool(_DesktopActionTool):
    """Press a named control using its own UI Automation pattern."""

    tool_id = "click_control"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="click_control",
            description=(
                "Press a button, menu item or checkbox by name in a named "
                "window. Resolves the exact control first, so it never presses "
                "something it could not find. Prefer this over click_at."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "window": {
                        "type": "string",
                        "description": "Window title, or enough of it to match.",
                    },
                    "control": {
                        "type": "string",
                        "description": (
                            "Control name exactly as inspect_window reports it."
                        ),
                    },
                },
                "required": ["window", "control"],
            },
            category="desktop",
            timeout_seconds=45.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        window_title = str(params.get("window", "") or "").strip()
        control_name = str(params.get("control", "") or "").strip()
        if not window_title or not control_name:
            return ToolResult(
                tool_name=self.tool_id,
                content="Name both the window and the control.",
                success=False,
            )

        window, control, problem = self._resolve(window_title, control_name)
        if problem is not None:
            return problem

        actual = (control.Name or "").strip()
        blocked = self._needs_confirmation(actual or control_name, params)
        if blocked is not None:
            return blocked

        available = _patterns(control)
        try:
            if "Invoke" in available:
                control.GetInvokePattern().Invoke()
                how = "invoked"
            elif "Toggle" in available:
                control.GetTogglePattern().Toggle()
                how = "toggled"
            elif "Expand" in available:
                control.GetExpandCollapsePattern().Expand()
                how = "expanded"
            else:
                # No pattern: fall back to a click on the element's own
                # rectangle. Still an element it resolved, not a guessed point.
                control.Click(simulateMove=False)
                how = "clicked"
        except Exception as exc:
            return ToolResult(
                tool_name=self.tool_id,
                content=f"Could not press {actual or control_name!r}: {exc}",
                success=False,
            )

        return ToolResult(
            tool_name=self.tool_id,
            content=(
                f"{how.capitalize()} {actual or control_name!r} "
                f"in {window_title!r}."
            ),
            success=True,
            metadata={"action": how, "control": actual, "patterns": available},
        )


@ToolRegistry.register("type_text")
class TypeTextTool(_DesktopActionTool):
    """Put text into a named field, or type into whatever has focus."""

    tool_id = "type_text"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="type_text",
            description=(
                "Type text into a window. Names a field where possible, which "
                "sets its value directly; without one the text is typed into "
                "whatever currently has focus in that window."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "window": {"type": "string", "description": "Window title."},
                    "text": {"type": "string", "description": "Text to enter."},
                    "control": {
                        "type": "string",
                        "description": (
                            "Field name from inspect_window. Omit to type into "
                            "whatever has focus."
                        ),
                    },
                },
                "required": ["window", "text"],
            },
            category="desktop",
            timeout_seconds=45.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        window_title = str(params.get("window", "") or "").strip()
        text = str(params.get("text", "") or "")
        control_name = str(params.get("control", "") or "").strip()
        if not window_title or not text:
            return ToolResult(
                tool_name=self.tool_id,
                content="Name the window and give the text to type.",
                success=False,
            )

        window, control, problem = self._resolve(window_title, control_name)
        if problem is not None:
            return problem

        # Typing is judged on what is being typed, not on a control name: the
        # text is the consequential part.
        blocked = self._needs_confirmation(text, params)
        if blocked is not None:
            return blocked

        try:
            if control is not None and "Value" in _patterns(control):
                control.GetValuePattern().SetValue(text)
                how = "set"
            else:
                target = control if control is not None else window
                target.SetFocus()
                _uia().SendKeys(text, waitTime=0)
                how = "typed"
        except Exception as exc:
            return ToolResult(
                tool_name=self.tool_id,
                content=f"Could not type into {window_title!r}: {exc}",
                success=False,
            )

        return ToolResult(
            tool_name=self.tool_id,
            content=f"{how.capitalize()} {len(text)} characters into {window_title!r}.",
            success=True,
            metadata={"action": how, "characters": len(text)},
        )


@ToolRegistry.register("click_at")
class ClickAtTool(_DesktopActionTool):
    """Click a point on a monitor. The fallback when nothing is nameable."""

    tool_id = "click_at"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="click_at",
            description=(
                "Click a point on a monitor, for apps with no readable "
                "controls — games, remote desktop, some Electron apps. Last "
                "resort: click_control names what it presses and this does "
                "not. Coordinates are relative to the monitor's top-left."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "monitor": {
                        "type": "integer",
                        "description": (
                            "Monitor number from list_windows. 1 is the main one."
                        ),
                    },
                    "x": {
                        "type": "integer",
                        "description": "Pixels from the monitor's left.",
                    },
                    "y": {
                        "type": "integer",
                        "description": "Pixels from the monitor's top.",
                    },
                },
                "required": ["x", "y"],
            },
            category="desktop",
            timeout_seconds=30.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        from openjarvis.tools.desktop_monitors import list_monitors

        try:
            x = int(params.get("x"))
            y = int(params.get("y"))
        except (TypeError, ValueError):
            return ToolResult(
                tool_name=self.tool_id,
                content="x and y must be whole numbers.",
                success=False,
            )

        monitors = list_monitors()
        wanted = params.get("monitor")
        if wanted is None:
            chosen = next((m for m in monitors if m.is_primary), None)
        else:
            chosen = next((m for m in monitors if m.index == int(wanted)), None)
        if chosen is None:
            available = ", ".join(str(m.index) for m in monitors)
            return ToolResult(
                tool_name=self.tool_id,
                content=f"No such monitor. Available: {available}.",
                success=False,
            )
        if not (0 <= x < chosen.width and 0 <= y < chosen.height):
            return ToolResult(
                tool_name=self.tool_id,
                content=(
                    f"({x}, {y}) is outside monitor {chosen.index}, which is "
                    f"{chosen.width}x{chosen.height}."
                ),
                success=False,
            )

        # A blind click cannot be judged by name, so it is always confirmed.
        # This is the one action where Sage does not know what it is pressing.
        blocked = self._needs_confirmation("", params)
        if blocked is not None:
            return blocked

        try:
            auto = _uia()
            auto.Click(chosen.x + x, chosen.y + y, waitTime=0)
        except Exception as exc:
            return ToolResult(
                tool_name=self.tool_id,
                content=f"Could not click: {exc}",
                success=False,
            )

        return ToolResult(
            tool_name=self.tool_id,
            content=f"Clicked ({x}, {y}) on monitor {chosen.index}.",
            success=True,
            metadata={"monitor": chosen.index, "x": x, "y": y},
        )


__all__ = ["ClickAtTool", "ClickControlTool", "MAX_SEARCH_DEPTH", "TypeTextTool"]
