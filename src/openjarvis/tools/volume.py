"""Turn the sound up or down: Spotify's own volume, or the PC's master volume.

"Turn it up", "set the volume to 40%" and "turn it down a bit" all failed in
the 16 October wide test -- there was no volume control anywhere, and once the
model paused Spotify instead. The user chose one tool that follows what is
playing: while Spotify plays, "it" is Spotify (its Web API volume, so it also
works when Spotify is on the phone); otherwise it is the PC's master volume,
the one the keyboard's volume keys move. Naming the PC or Spotify always wins.

Neither level is what ``speech.media_hold`` or ``speech.ducking`` touch: those
turn each app's Windows session down while Sage listens or speaks and put it
back afterwards, so a change made here survives the hold ending.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec
from openjarvis.tools.spotify_control import _DEFAULT_TOKEN_PATH, _request

#: "A bit", and a bare up/down: the user's choice (16 October).
STEP = 10

_ACTIONS = ("up", "down", "set", "mute", "unmute", "status")
_TARGETS = ("auto", "spotify", "pc")


def _clamp(level: float) -> int:
    return int(max(0, min(100, round(level))))


def _target_level(action: str, current: int, percent: Optional[float]) -> int:
    if action == "set":
        return _clamp(percent if percent is not None else current)
    step = STEP if percent is None else abs(percent)
    return _clamp(current + step if action == "up" else current - step)


class _Master:
    """The default output device's master volume, through pycaw."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("the PC volume is only reachable on Windows")
        import comtypes

        try:
            comtypes.CoInitialize()
        except Exception:
            pass
        from pycaw.pycaw import AudioUtilities

        self._endpoint = AudioUtilities.GetSpeakers().EndpointVolume

    def level(self) -> int:
        return _clamp(self._endpoint.GetMasterVolumeLevelScalar() * 100)

    def muted(self) -> bool:
        return bool(self._endpoint.GetMute())

    def set_level(self, level: int) -> None:
        self._endpoint.SetMasterVolumeLevelScalar(level / 100.0, None)

    def set_mute(self, muted: bool) -> None:
        self._endpoint.SetMute(1 if muted else 0, None)


def _spotify_player(token: str) -> Dict[str, Any]:
    """``me/player``: what is playing and on which device ({} when idle)."""
    return _request(token, "GET", "me/player") or {}


def _spotify_playing(player: Dict[str, Any]) -> bool:
    device = player.get("device") or {}
    return bool(player.get("is_playing")) and device.get("volume_percent") is not None


@ToolRegistry.register("volume")
class VolumeTool(BaseTool):
    """Change how loud things are: Spotify while it plays, else the PC."""

    tool_id = "volume"
    is_local = False

    def __init__(
        self,
        token_path: str = _DEFAULT_TOKEN_PATH,
        master_factory: Any = None,
    ) -> None:
        self._token_path = str(token_path)
        self._master_factory = master_factory or _Master

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.tool_id,
            description=(
                "Make things louder or quieter: 'turn it up', 'turn it down a "
                "bit', 'set the volume to 40%', 'mute', 'how loud is it?'. "
                f"up/down move {STEP} points unless the user gives an amount. "
                "target='auto' (default) changes Spotify while Spotify is "
                "playing and the PC's master volume otherwise; 'pc' when they "
                "say the PC, computer, laptop or speakers; 'spotify' when they "
                "name Spotify, or when the same request also plays something "
                "on Spotify ('play Ben&Ben and set the volume to 40%'). Never "
                "pause instead of changing the volume. Only for a volume "
                "request: a bare 'stop' or 'pause' is media_pause, never mute."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(_ACTIONS)},
                    "percent": {
                        "type": "number",
                        "description": (
                            "For set: the level, 0-100. For up/down: how many "
                            f"points to move (default {STEP})."
                        ),
                    },
                    "target": {
                        "type": "string",
                        "enum": list(_TARGETS),
                        "description": "auto (default), spotify or pc.",
                    },
                },
                "required": ["action"],
            },
            category="media",
        )

    def execute(self, **params: Any) -> ToolResult:
        action = str(params.get("action") or "").strip().lower()
        target = str(params.get("target") or "auto").strip().lower()
        if action not in _ACTIONS:
            return self._fail(f"action must be one of {', '.join(_ACTIONS)}.")
        if target not in _TARGETS:
            return self._fail(f"target must be one of {', '.join(_TARGETS)}.")
        percent: Optional[float] = None
        if params.get("percent") not in (None, ""):
            try:
                percent = float(params["percent"])
            except (TypeError, ValueError):
                return self._fail(f"Could not read {params['percent']!r} as a percent.")
        if action == "set" and percent is None:
            return self._fail("Say what level to set, 0-100.")

        if target in ("auto", "spotify"):
            handled, result = self._try_spotify(action, percent, target == "spotify")
            if handled:
                return result
        return self._pc(action, percent)

    def _try_spotify(
        self, action: str, percent: Optional[float], named: bool
    ) -> Tuple[bool, ToolResult]:
        """``(handled, result)``. Not handled means: use the PC volume."""
        if not Path(self._token_path).exists():
            if named:
                return True, self._fail(
                    "Spotify is not connected. Run: jarvis connect spotify"
                )
            return False, self._fail("")
        from openjarvis.connectors.spotify_auth import (
            SpotifyAuthError,
            call_with_refresh,
        )

        def run(token: str) -> Tuple[bool, ToolResult]:
            player = _spotify_player(token)
            if not _spotify_playing(player):
                if not named:
                    return False, self._fail("")
                if not player.get("device"):
                    return True, self._fail(
                        "Spotify isn't playing on any device, so it has no "
                        "volume to change."
                    )
            device = player.get("device") or {}
            current = device.get("volume_percent")
            if current is None:
                return True, self._fail(
                    f"Spotify on {device.get('name') or 'that device'} does not "
                    "let its volume be changed remotely."
                )
            name = device.get("name") or "Spotify"
            if action == "status":
                return True, self._ok(
                    f"Spotify ({name}) is at {current}%.", "spotify", current
                )
            if action in ("mute", "unmute"):
                # Spotify has no mute; 0 is the nearest honest thing, and
                # unmuting cannot know the old level, so it returns to 50%.
                level = 0 if action == "mute" else max(int(current), 50)
            else:
                level = _target_level(action, int(current), percent)
            params = {"volume_percent": level}
            if device.get("id"):
                params["device_id"] = device["id"]
            _request(token, "PUT", "me/player/volume", params=params)
            return True, self._ok(
                f"Spotify ({name}) volume {current}% -> {level}%.", "spotify", level
            )

        try:
            return call_with_refresh(run, self._token_path)
        except SpotifyAuthError as exc:
            if named:
                return True, self._fail(f"Spotify authentication failed: {exc}")
        except httpx.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", 0)
            if named:
                return True, self._fail(
                    "Spotify would not change its volume"
                    + (f" (HTTP {status})." if status else ".")
                )
        return False, self._fail("")

    def _pc(self, action: str, percent: Optional[float]) -> ToolResult:
        try:
            master = self._master_factory()
            current = master.level()
            if action == "status":
                muted = " (muted)" if master.muted() else ""
                return self._ok(f"The PC volume is {current}%{muted}.", "pc", current)
            if action in ("mute", "unmute"):
                master.set_mute(action == "mute")
                word = "muted" if action == "mute" else f"unmuted, at {current}%"
                return self._ok(f"The PC is {word}.", "pc", current)
            level = _target_level(action, current, percent)
            master.set_level(level)
            # Turning it up from mute and hearing nothing is the classic trap.
            if master.muted() and action in ("up", "set") and level > 0:
                master.set_mute(False)
        except Exception as exc:
            return self._fail(f"Could not reach the PC volume: {exc}")
        return self._ok(f"PC volume {current}% -> {level}%.", "pc", level)

    def _ok(self, text: str, where: str, level: int) -> ToolResult:
        return ToolResult(
            tool_name=self.tool_id,
            content=text,
            success=True,
            metadata={"target": where, "level": level},
        )

    def _fail(self, text: str) -> ToolResult:
        return ToolResult(tool_name=self.tool_id, content=text, success=False)


__all__ = ["STEP", "VolumeTool"]
