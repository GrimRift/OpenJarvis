"""Hand the front back to the Sage app after Sage reads something in Opera.

Reading Outlook, Teams or a web page opens a temporary tab in the user's Opera
GX, and Opera comes to the front to draw it. When the read is done (the user's
rules, 29 September 2026):

* Opera goes back the way it was: a window that was minimised before is
  minimised again; one that was open stays open, behind Sage.
* The Sage Windows app comes to the front -- unless it is hidden in the tray,
  where the user put it on purpose. A minimised Sage counts as open and is
  restored.

Pages the user asked to *see* (web_open, Gmail, YouTube, Netflix) never pass
through here: Opera stays in front for those.

Only Windows has either program; everywhere else this does nothing. It never
raises: failing to tidy windows must not fail the read.
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import sys
from typing import Dict

logger = logging.getLogger(__name__)

#: The Sage app's single-instance hand-off: a second launch with this flag asks
#: the running app to come forward, unless it is hidden in the tray.
RETURN_FOCUS_ARG = "--return-focus"
_APP_PROCESS = "sage-desktop.exe"
_OPERA_PROCESS = "opera.exe"
_SW_SHOWMINNOACTIVE = 7


def _opera_windows() -> Dict[int, bool]:
    """Opera's visible top-level windows, each mapped to whether it is minimised."""
    if sys.platform != "win32":
        return {}
    import ctypes
    from ctypes import wintypes

    import psutil

    opera_pids = {
        p.info["pid"]
        for p in psutil.process_iter(["pid", "name"])
        if (p.info.get("name") or "").lower() == _OPERA_PROCESS
    }
    if not opera_pids:
        return {}
    user32 = ctypes.windll.user32
    found: Dict[int, bool] = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(handle, _param):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if (
            pid.value in opera_pids
            and user32.IsWindowVisible(handle)
            and user32.GetWindowTextLengthW(handle) > 0
        ):
            found[int(handle)] = bool(user32.IsIconic(handle))
        return True

    user32.EnumWindows(visit, 0)
    return found


def _running_app_exe() -> str:
    """Path of the running Sage app, or "" when it is not running."""
    import psutil

    for p in psutil.process_iter(["name", "exe"]):
        if (p.info.get("name") or "").lower() == _APP_PROCESS and p.info.get("exe"):
            return str(p.info["exe"])
    return ""


class ReadingFocus:
    """Snapshot Opera before a read; :meth:`finish` tidies up after it."""

    def __init__(self) -> None:
        self._before: Dict[int, bool] = {}
        with contextlib.suppress(Exception):
            self._before = _opera_windows()

    def finish(self) -> None:
        with contextlib.suppress(Exception):
            self._put_opera_back()
        with contextlib.suppress(Exception):
            self._return_to_sage()

    def _put_opera_back(self) -> None:
        if sys.platform != "win32":
            return
        import ctypes

        user32 = ctypes.windll.user32
        now = _opera_windows()
        for handle, was_minimised in self._before.items():
            if was_minimised and handle in now and not now[handle]:
                # Minimised without taking the focus from anything.
                user32.ShowWindow(handle, _SW_SHOWMINNOACTIVE)

    def _return_to_sage(self) -> None:
        if sys.platform != "win32":
            return
        exe = _running_app_exe()
        if not exe or not os.path.isfile(exe):
            # No app (Sage in a browser tab): nothing to hand the front to.
            return
        # The app's single-instance plugin passes this to the running app and
        # the new process exits at once. The app decides: shown or minimised
        # -> brought forward; hidden in the tray -> left alone.
        subprocess.Popen(
            [exe, RETURN_FOCUS_ARG],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )
