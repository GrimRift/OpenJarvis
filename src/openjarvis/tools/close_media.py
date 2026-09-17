"""Close what is playing: YouTube and Netflix tabs, the Spotify app.

The user asks for this by voice ("close YouTube", "close Netflix", "close
Spotify", "close everything") when a video or music has run its course.
Tabs go through the same Opera GX debugging port the play tools use, so
every tab on the site closes -- Sage's own media window included. Spotify
is a desktop app: it is quit outright, by decision, not paused and hidden.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

TARGETS = ("youtube", "netflix", "spotify", "all")

#: Hosts whose tabs each target closes. Spotify's web player counts as
#: Spotify too, alongside the app.
SITE_HOSTS: Dict[str, tuple] = {
    "youtube": ("youtube.com", "youtu.be", "music.youtube.com"),
    "netflix": ("netflix.com",),
    "spotify": ("open.spotify.com",),
}
SPOTIFY_PROCESS = "Spotify.exe"


def host_matches(url: str, hosts: tuple) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in hosts)


def tabs_to_close(targets: List[Dict[str, Any]], what: str) -> List[Dict[str, Any]]:
    """The page targets whose site the request names, in listing order."""
    wanted = [what] if what != "all" else ["youtube", "netflix", "spotify"]
    hosts = tuple(h for w in wanted for h in SITE_HOSTS[w])
    return [
        t
        for t in targets
        if t.get("type") == "page" and host_matches(str(t.get("url") or ""), hosts)
    ]


def quit_spotify() -> Optional[int]:
    """End every Spotify process. Returns how many were found, or None when
    the platform has no such thing."""
    if sys.platform != "win32":
        return None
    try:
        import psutil
    except ImportError:
        psutil = None  # type: ignore[assignment]
    found = 0
    if psutil is not None:
        for proc in psutil.process_iter(["name"]):
            if (proc.info.get("name") or "").lower() == SPOTIFY_PROCESS.lower():
                found += 1
                try:
                    proc.terminate()
                except psutil.Error:
                    continue
        return found
    result = subprocess.run(
        ["taskkill", "/IM", SPOTIFY_PROCESS, "/F"],
        capture_output=True,
        text=True,
        check=False,
    )
    return 1 if result.returncode == 0 else 0


@ToolRegistry.register("close_media")
class CloseMediaTool(BaseTool):
    """Close YouTube / Netflix tabs and quit Spotify."""

    tool_id = "close_media"
    is_local = True

    def __init__(self, allowed_dirs: Optional[List[str]] = None) -> None:
        super().__init__()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="close_media",
            description=(
                "Close what is playing. 'close YouTube' / 'close the video' → "
                "what='youtube' (every YouTube tab in the browser); 'close "
                "Netflix' → 'netflix'; 'close Spotify' / 'quit Spotify' → "
                "'spotify' (quits the app and closes its web player); 'close "
                "everything' / 'close the media' → 'all'. Closing is "
                "immediate; do not ask for confirmation first."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "what": {
                        "type": "string",
                        "enum": list(TARGETS),
                        "description": "Which player to close.",
                    }
                },
                "required": ["what"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        what = str(params.get("what") or "").strip().lower()
        if what not in TARGETS:
            return ToolResult(
                tool_name=self.tool_id,
                content=f"what must be one of {', '.join(TARGETS)}.",
                success=False,
            )
        notes: List[str] = []
        closed_titles: List[str] = []
        try:
            from openjarvis.tools.cdp import Browser
            from openjarvis.tools.opera_control import DEBUG_PORT, port_is_open

            if port_is_open():
                browser = Browser(DEBUG_PORT)
                for tab in tabs_to_close(browser.targets(), what):
                    browser.close_target(str(tab.get("id") or ""))
                    closed_titles.append(
                        str(tab.get("title") or tab.get("url") or "tab")
                    )
            elif what != "spotify":
                notes.append(
                    "the browser's control port is not open, so no tabs closed"
                )
        except Exception as exc:  # noqa: BLE001 -- report, do not raise
            notes.append(f"tabs: {exc}")

        quit_count: Optional[int] = None
        if what in ("spotify", "all"):
            try:
                quit_count = quit_spotify()
            except Exception as exc:  # noqa: BLE001
                notes.append(f"Spotify: {exc}")

        parts: List[str] = []
        if closed_titles:
            shown = "; ".join(t[:60] for t in closed_titles[:5])
            more = (
                f" and {len(closed_titles) - 5} more" if len(closed_titles) > 5 else ""
            )
            parts.append(f"Closed {len(closed_titles)} tab(s): {shown}{more}.")
        elif what != "spotify":
            parts.append(f"No {what if what != 'all' else 'media'} tabs were open.")
        if quit_count is not None:
            parts.append("Quit Spotify." if quit_count else "Spotify was not running.")
        if notes:
            parts.append("(" + "; ".join(notes) + ")")
        return ToolResult(
            tool_name=self.tool_id,
            content=" ".join(parts),
            success=True,
            metadata={"closed": closed_titles, "spotify_quit": quit_count},
        )


__all__ = ["CloseMediaTool", "quit_spotify", "tabs_to_close"]
