"""Desktop app launcher — bring a known application to the screen.

Deliberately allowlist-only: the model picks a *name* from a fixed table,
never a path or command line. This is the narrow, deterministic half of
Windows control (same risk category as ``notify_windows``) and is kept
strictly separate from general mouse/keyboard/screen automation.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, Dict, List

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

# Each entry lists candidate launch targets tried in order. A Microsoft Store
# (Appx) install exposes a WindowsApps shim exe; a classic desktop install
# puts the exe elsewhere. Listing both keeps this working across either.
_APPS: Dict[str, Dict[str, Any]] = {
    "spotify": {
        "display": "Spotify",
        "process": "Spotify.exe",
        "candidates": [
            os.path.join(
                os.environ.get("LOCALAPPDATA", ""),
                "Microsoft",
                "WindowsApps",
                "Spotify.exe",
            ),
            os.path.join(os.environ.get("APPDATA", ""), "Spotify", "Spotify.exe"),
        ],
    },
    "obsidian": {
        "display": "Obsidian",
        "process": "Obsidian.exe",
        "candidates": [
            os.path.join(
                os.environ.get("LOCALAPPDATA", ""), "Obsidian", "Obsidian.exe"
            ),
        ],
    },
    "explorer": {
        "display": "File Explorer",
        "process": "explorer.exe",
        "candidates": ["explorer.exe"],
    },
    "notepad": {
        "display": "Notepad",
        "process": "notepad.exe",
        "candidates": ["notepad.exe"],
    },
    "calculator": {
        "display": "Calculator",
        "process": "CalculatorApp.exe",
        "candidates": ["calc.exe"],
    },
}


def is_app_running(app_key: str) -> bool:
    """True when *app_key*'s process is actually live on this machine.

    Callers need this to tell "the window is on screen" apart from indirect
    signals that outlive the process. Spotify is the motivating case: a
    Connect device registration lingers server-side after the client exits,
    so the Web API keeps reporting that device as active — and even
    ``is_playing: true`` — while no window and no audio exist. Trusting the
    device list as proof of life means never launching the app.
    """
    process = _APPS.get(app_key, {}).get("process", "")
    if not process or os.name != "nt":
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {process}", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return process.lower() in (result.stdout or "").lower()


def _resolve_target(app_key: str) -> str:
    """Return the first launch target that exists, or "" when none do."""
    for candidate in _APPS[app_key]["candidates"]:
        if not candidate:
            continue
        if os.path.isabs(candidate):
            if os.path.exists(candidate):
                return candidate
        elif shutil.which(candidate):
            return candidate
    return ""


@ToolRegistry.register("open_app")
class OpenAppTool(BaseTool):
    """Launch or focus a known desktop application."""

    tool_id = "open_app"
    is_local = False

    def __init__(self, allowed_apps: List[str] | None = None) -> None:
        if allowed_apps is None:
            env_value = os.environ.get("OPENJARVIS_ALLOWED_APPS", "")
            allowed_apps = [a.strip() for a in env_value.split(os.pathsep) if a.strip()]
        # Empty means "every app in the built-in table"; the table itself is
        # the real boundary, so this env var only narrows, never widens.
        self._allowed = [a.lower() for a in allowed_apps] or list(_APPS)

    @property
    def spec(self) -> ToolSpec:
        names = ", ".join(sorted(self._allowed))
        return ToolSpec(
            name="open_app",
            description=(
                "Open a desktop application on the user's screen so they can "
                f"see it. Available apps: {names}. Use when the user asks to "
                "open, launch, or show an app."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": "Which application to open.",
                        "enum": sorted(self._allowed),
                    },
                },
                "required": ["app"],
            },
            category="system",
            timeout_seconds=20.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        app = str(params.get("app", "")).strip().lower()

        if not app:
            return ToolResult(
                tool_name="open_app",
                content="No app specified.",
                success=False,
            )
        if app not in _APPS or app not in self._allowed:
            return ToolResult(
                tool_name="open_app",
                content=(
                    f"{app!r} is not an allowed app. "
                    f"Available: {', '.join(sorted(self._allowed))}."
                ),
                success=False,
            )

        target = _resolve_target(app)
        display = _APPS[app]["display"]
        if not target:
            return ToolResult(
                tool_name="open_app",
                content=f"{display} does not appear to be installed on this machine.",
                success=False,
            )

        try:
            # Popen, not run(): launching a GUI app should return as soon as
            # the process starts, not block this tool until the user closes
            # the window. Fixed argument list, shell=False (the default) —
            # nothing here is ever built from model-supplied text.
            subprocess.Popen([target])
        except Exception as exc:
            return ToolResult(
                tool_name="open_app",
                content=f"Failed to open {display}: {exc}",
                success=False,
            )

        return ToolResult(
            tool_name="open_app",
            content=f"{display} is opening.",
            success=True,
            metadata={"app": app},
        )


__all__ = ["OpenAppTool"]
