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
import time
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
            # Electron/Squirrel per-user installs land under Programs\, which
            # is where Obsidian actually is; the bare LOCALAPPDATA path below
            # is kept only as a fallback for other layouts.
            os.path.join(
                os.environ.get("LOCALAPPDATA", ""),
                "Programs",
                "Obsidian",
                "Obsidian.exe",
            ),
            os.path.join(
                os.environ.get("LOCALAPPDATA", ""), "Obsidian", "Obsidian.exe"
            ),
            os.path.join(
                os.environ.get("PROGRAMFILES", ""), "Obsidian", "Obsidian.exe"
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


def _pids_for(process_name: str) -> set:
    """PIDs currently running under *process_name*."""
    if os.name != "nt":
        return set()
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/NH", "/FO", "CSV"],
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    pids = set()
    for line in (result.stdout or "").splitlines():
        fields = [f.strip('"') for f in line.split('","')]
        if len(fields) > 1 and fields[0].lower().startswith(process_name.lower()):
            try:
                pids.add(int(fields[1]))
            except ValueError:
                continue
    return pids


def _raise_window(hwnd: int) -> None:
    """Restore and foreground *hwnd*.

    Windows refuses SetForegroundWindow from a process that does not own the
    current foreground window — which is exactly our position, since the
    request arrives over HTTP with the user's browser in front. The call then
    only flashes the taskbar button, which is what "it opened but stayed
    minimised" looked like. SwitchToThisWindow is the long-standing fallback
    the shell itself uses for alt-tab style activation and is not subject to
    that restriction, so it runs whenever the polite call reports failure.
    """
    import ctypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    sw_restore = 9
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, sw_restore)
    user32.BringWindowToTop(hwnd)

    # Attaching to the foreground window's input queue for the duration of
    # the call is what makes it work while the user is actually using the
    # machine. ForegroundLockTimeout reads 0x7FFFFFFF here, so whatever the
    # user last typed into — the browser they sent the request from — holds
    # the lock indefinitely and a plain SetForegroundWindow is refused.
    # Sharing the input queue makes this process a legitimate caller for
    # that moment. Detaching again matters: leaving the queues attached
    # couples the two threads' input state.
    foreground = user32.GetForegroundWindow()
    target_thread = user32.GetWindowThreadProcessId(foreground, None)
    our_thread = kernel32.GetCurrentThreadId()
    attached = False
    if target_thread and target_thread != our_thread:
        attached = bool(user32.AttachThreadInput(our_thread, target_thread, True))
    try:
        raised = user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(our_thread, target_thread, False)

    if not raised:
        try:
            user32.SwitchToThisWindow(hwnd, True)
        except Exception:
            pass


def _focus_app_window(process_name: str, timeout_seconds: float = 10.0) -> bool:
    """Wait for *process_name* to show a window, then bring it to the front.

    Matches on process name rather than the PID returned by Popen: Store
    shims and Electron launchers exit or hand off to a child, so the window
    frequently belongs to a different process than the one started.

    Success is confirmed by reading the foreground window back, and the
    raise is retried until it holds. One raise is not enough for an app that
    builds its window in stages: Obsidian's process appears within a second
    and briefly owns a window that can be raised, then replaces it with the
    real one, which comes up unfocused — the app opens, and sits behind the
    browser exactly as if nothing had been raised at all.
    """
    if os.name != "nt":
        return False

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )

    def _foreground_pid() -> int:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(
            user32.GetForegroundWindow(), ctypes.byref(pid)
        )
        return pid.value

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        pids = _pids_for(process_name)
        if pids:
            found = []

            def _callback(hwnd, _lparam, _pids=pids, _found=found):
                if not user32.IsWindowVisible(hwnd):
                    return True
                if user32.GetWindow(hwnd, 4):  # GW_OWNER — skip tool windows
                    return True
                if not user32.GetWindowTextLengthW(hwnd):
                    return True
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value in _pids:
                    _found.append(hwnd)
                    return False
                return True

            user32.EnumWindows(enum_proc(_callback), 0)
            if found:
                _raise_window(found[0])
                if _foreground_pid() in pids:
                    return True
        time.sleep(0.4)
    return False


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
                f"see it. Available apps: {names}. Use only when the user "
                "asks to open, launch, or show an app and nothing more. For "
                "playing or controlling music, use spotify_control instead — "
                "it opens Spotify itself, so opening the app first is both "
                "unnecessary and leaves the request unfinished."
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

        process = _APPS[app].get("process", "")

        # Focus rather than launch when it is already running: starting a
        # second copy is not what "open X" means, and the duplicate is what
        # the user would have to close afterwards.
        if process and is_app_running(app):
            _focus_app_window(process, timeout_seconds=4.0)
            return ToolResult(
                tool_name="open_app",
                content=(
                    f"{display} was already running — its window is now at "
                    "the front of the screen."
                ),
                success=True,
                metadata={"app": app, "launched": False},
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

        # A launched window does not come forward on its own here — see
        # _raise_window. Reporting the outcome rather than a bare "opening"
        # also matters for the agent loop: an ambiguous in-progress result
        # invited the model to call this tool again and again until the loop
        # guard stopped it (observed live, ~14 turns for one request).
        focused = _focus_app_window(process) if process else False
        content = (
            f"{display} is now open and in front of you."
            if focused
            else f"{display} was launched."
        )
        return ToolResult(
            tool_name="open_app",
            content=content,
            success=True,
            metadata={"app": app},
        )


__all__ = ["OpenAppTool"]
