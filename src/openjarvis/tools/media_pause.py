"""Pause or resume what is playing on the PC: a video, music, any app.

The user asked for it on 6 October ("I tried to pause the video") and there
was nothing to do it with: spotify_control pauses Spotify only, through its
web API, and a YouTube video could only be closed. This goes through
Windows' media controls, so it reaches Opera's YouTube, Spotify and any
other player alike. A pause asked for here also outlasts the media hold
(``speech.media_hold``), which would otherwise resume the player when the
exchange ends.
"""

from __future__ import annotations

from typing import Any, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

ACTIONS = ("pause", "resume")
WHAT = ("all", "video", "music")


def _label(app_id: str) -> str:
    from openjarvis.speech.media_hold import kind_of

    kind = kind_of(app_id)
    if kind == "music":
        return "Spotify"
    if kind == "video":
        return "the browser video"
    return app_id.split("!")[-1] or app_id


@ToolRegistry.register("media_pause")
class MediaPauseTool(BaseTool):
    """Pause / resume any app's media through Windows' media controls."""

    tool_id = "media_pause"
    is_local = True

    def __init__(self, allowed_dirs: Optional[List[str]] = None) -> None:
        super().__init__()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="media_pause",
            description=(
                "Pause or resume what is playing on the PC -- a YouTube video "
                "in the browser, Spotify, any player. 'pause the video' / "
                "'stop the video' -> action='pause', what='video'; 'pause the "
                "music' -> what='music'; 'pause everything' -> 'all'; 'resume "
                "the video' / 'play it again' -> action='resume'. Media is "
                "already paused while the user talks to Sage and comes back "
                "on its own afterwards: call this when the user wants it to "
                "STAY paused, or to resume it. Use spotify_control for "
                "changing the song or playlist, close_media to close it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(ACTIONS)},
                    "what": {
                        "type": "string",
                        "enum": list(WHAT),
                        "description": "video = the browser, music = Spotify.",
                    },
                },
                "required": ["action"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        from openjarvis.speech import media_hold

        action = str(params.get("action") or "").strip().lower()
        what = str(params.get("what") or "all").strip().lower()
        if action not in ACTIONS or what not in WHAT:
            return ToolResult(
                tool_name=self.tool_id,
                content="action must be pause or resume; what all, video or music.",
                success=False,
            )
        if action == "pause":
            apps = media_hold.pause_for_user(what)
            content = (
                "Paused " + ", ".join(_label(a) for a in apps) + "; it stays paused."
                if apps
                else "Nothing of that kind is playing."
            )
        else:
            apps = media_hold.resume_for_user(what)
            content = (
                "Resumed " + ", ".join(_label(a) for a in apps) + "."
                if apps
                else "Nothing of that kind was paused."
            )
        return ToolResult(
            tool_name=self.tool_id,
            content=content,
            success=True,
            metadata={"action": action, "apps": apps},
        )
