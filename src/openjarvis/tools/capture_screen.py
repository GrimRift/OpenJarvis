"""Look at what is actually on screen.

`inspect_window` reads text — titles, button labels, the accessibility tree.
That is exact and cheap, and it is blind to everything that is only pixels. A
poster, a chart, a photo, a screenshot of an error dialog in a game: none of
those name themselves anywhere the accessibility tree can see, so "identify the
character on my second monitor" had no answer.

This tool captures and *asks the vision model itself*, returning prose. It does
not hand an image back to the caller, because a tool result is text and OpenAI
does not accept images in a tool message — routing pixels back through the
agent loop would mean rebuilding how tool results re-enter a turn. Asking here
keeps the whole thing one tool call.

Read-only: it captures, it never clicks. And it is bounded — one capture per
call, downscaled, never on a timer.
"""

from __future__ import annotations

import base64
import io
from typing import Any, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.security.screen_redaction import is_sensitive_title
from openjarvis.tools._stubs import BaseTool, ToolSpec
from openjarvis.tools.desktop_monitors import list_monitors

DEFAULT_QUESTION = "Describe what is on this screen."

#: A reasoning model spends tokens thinking before it writes anything, and a
#: dense screenshot is a lot to think about. Measured on a 1280px capture of a
#: busy browser window: 600 tokens produced `finish_reason: length` and **zero
#: characters**, all of it spent reasoning; 3000 produced a full answer using
#: 1523. The first test passed only because that screen was simpler.
VISION_MAX_TOKENS = 3000

#: One retry with real headroom, mirroring morning_digest's handling of the
#: same failure. Empty output must never be reported as "nothing to see".
VISION_RETRY_MAX_TOKENS = 8000


def _resolve_vision_model(config: Any) -> str:
    """Which model gets the picture.

    An explicit setting wins. Otherwise prefer the cloud model already
    configured for chat — it is the stronger reader and needs no download —
    and fall back to the local vision model when there is no cloud key.
    """
    vision = getattr(config, "vision", None)
    explicit = str(getattr(vision, "model", "") or "").strip()
    if explicit:
        return explicit

    intelligence = getattr(config, "intelligence", None)
    candidate = str(getattr(intelligence, "default_model", "") or "").strip()
    try:
        from openjarvis.server.cloud_router import _load_keys, is_cloud_model

        if candidate and is_cloud_model(candidate) and _load_keys():
            return candidate
        if _load_keys().get("OPENAI_API_KEY"):
            return "gpt-5.6-luna"
    except Exception:
        pass
    return str(getattr(vision, "local_model", "") or "qwen3-vl:8b")


def _downscale(image: Any, max_edge: int) -> Any:
    """Shrink so the long edge fits *max_edge*, preserving aspect."""
    width, height = image.size
    longest = max(width, height)
    if longest <= max_edge:
        return image
    scale = max_edge / float(longest)
    from PIL import Image

    return image.resize(
        (max(1, int(width * scale)), max(1, int(height * scale))),
        Image.LANCZOS,
    )


@ToolRegistry.register("capture_screen")
class CaptureScreenTool(BaseTool):
    """Capture a monitor and answer a question about what it shows."""

    tool_id = "capture_screen"
    is_local = False

    def __init__(self, config: Any = None, engine: Any = None) -> None:
        self._config = config
        self._engine = engine

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="capture_screen",
            description=(
                "Look at what is displayed on one of the user's monitors and "
                "answer a question about it. Use this for anything that is "
                "only visible as an image — a picture, a chart, a game, a "
                "video — where inspect_window has no text to read. Prefer "
                "inspect_window when the answer is a button or field name: it "
                "is faster and keeps the screen off the network."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "monitor": {
                        "type": "integer",
                        "description": (
                            "Which monitor to look at, as numbered by "
                            "list_windows. Omit for the primary."
                        ),
                    },
                    "question": {
                        "type": "string",
                        "description": (
                            "What to find out, e.g. 'which character is "
                            "shown?'. Omit for a general description."
                        ),
                    },
                },
            },
            category="desktop",
            timeout_seconds=90.0,
        )

    def _config_or_load(self) -> Any:
        if self._config is not None:
            return self._config
        try:
            from openjarvis.core.config import load_config

            return load_config()
        except Exception:
            return None

    def _grab(self, monitor: Any) -> Any:
        from PIL import ImageGrab

        if monitor is None:
            return ImageGrab.grab()
        box = (
            monitor.x,
            monitor.y,
            monitor.x + monitor.width,
            monitor.y + monitor.height,
        )
        # all_screens is required for a second monitor: without it the grab is
        # clipped to the primary and a negative-x display comes back blank.
        return ImageGrab.grab(bbox=box, all_screens=True)

    def _ask(self, model: str, data_url: str, question: str) -> Optional[str]:
        from openjarvis.core.types import Message, Role

        message = Message(role=Role.USER, content=question)
        message.images = [data_url]

        engine = self._engine
        if engine is None:
            import os

            # CloudEngine reads os.environ; the keys live in cloud-keys.env,
            # which only cloud_router knows how to read. Without this the tool
            # reports "OpenAI client not available" on a machine that is
            # perfectly well configured for chat.
            try:
                from openjarvis.server.cloud_router import _load_keys

                for name, value in _load_keys().items():
                    os.environ.setdefault(name, value)
            except Exception:
                pass

            from openjarvis.engine.cloud import CloudEngine

            engine = CloudEngine()
        result = engine.generate(
            [message], model=model, max_tokens=VISION_MAX_TOKENS
        )
        answer = str((result or {}).get("content") or "").strip()
        if answer:
            return answer

        # Ran out of budget mid-thought rather than having nothing to say.
        if str((result or {}).get("finish_reason") or "") != "length":
            return None
        retry = engine.generate(
            [message], model=model, max_tokens=VISION_RETRY_MAX_TOKENS
        )
        return str((retry or {}).get("content") or "").strip() or None

    def execute(self, **params: Any) -> ToolResult:
        question = str(params.get("question", "") or "").strip() or DEFAULT_QUESTION
        config = self._config_or_load()
        vision_config = getattr(config, "vision", None)
        max_edge = int(getattr(vision_config, "max_edge", 1280) or 1280)

        try:
            monitors = list_monitors()
        except Exception:
            monitors = []

        chosen = None
        wanted = params.get("monitor")
        if wanted is not None and monitors:
            for monitor in monitors:
                if monitor.index == int(wanted):
                    chosen = monitor
                    break
            if chosen is None:
                available = ", ".join(str(m.index) for m in monitors)
                return ToolResult(
                    tool_name=self.tool_id,
                    content=f"No monitor {wanted}. Available: {available}.",
                    success=False,
                )
        elif monitors:
            chosen = next((m for m in monitors if m.is_primary), monitors[0])

        # A capture takes in whatever happens to be on that screen, including a
        # password manager the user forgot was open. Refuse rather than send it.
        if chosen is not None:
            from openjarvis.tools.desktop_awareness import _visible_windows

            try:
                for window in _visible_windows():
                    centre_x = window["x"] + window["width"] // 2
                    centre_y = window["y"] + window["height"] // 2
                    if chosen.contains(centre_x, centre_y) and is_sensitive_title(
                        window["title"]
                    ):
                        return ToolResult(
                            tool_name=self.tool_id,
                            content=(
                                f"Monitor {chosen.index} currently shows a "
                                "window that looks like a password manager or "
                                "banking window, so it was not captured."
                            ),
                            success=False,
                            metadata={"redacted": True},
                        )
            except Exception:
                pass

        try:
            image = self._grab(chosen).convert("RGB")
            image = _downscale(image, max_edge)
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            encoded = base64.b64encode(buffer.getvalue()).decode()
        except Exception as exc:
            return ToolResult(
                tool_name=self.tool_id,
                content=f"Could not capture the screen: {exc}",
                success=False,
            )

        model = _resolve_vision_model(config)
        try:
            answer = self._ask(model, f"data:image/png;base64,{encoded}", question)
        except Exception as exc:
            return ToolResult(
                tool_name=self.tool_id,
                content=f"Captured the screen but the vision model failed: {exc}",
                success=False,
            )
        if not answer:
            return ToolResult(
                tool_name=self.tool_id,
                content="Captured the screen but the vision model returned nothing.",
                success=False,
            )

        where = f"monitor {chosen.index}" if chosen else "the screen"
        return ToolResult(
            tool_name=self.tool_id,
            content=f"Looking at {where}: {answer}",
            success=True,
            metadata={
                "monitor": chosen.index if chosen else None,
                "vision_model": model,
                "capture_size": list(image.size),
            },
        )


__all__ = ["CaptureScreenTool", "DEFAULT_QUESTION"]
