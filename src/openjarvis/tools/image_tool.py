"""Image tools (M40): create, edit/refine, and send to the phone.

The picture never passes through a tool result: the result text names the
image id (so a later "make it brighter" can find it, even after a reload), and
the UI gets the picture from ``metadata["image"]`` on ``tool_call_end`` and
fetches it from ``GET /v1/images/{id}``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.images import attachments
from openjarvis.images.service import ImageError, ImageService, Result
from openjarvis.images.store import ImageRecord
from openjarvis.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)

#: The model is slow (15-50 s measured); the executor's 30 s default would
#: cut it off and the image would still be billed.
_TIMEOUT_SECONDS = 180.0

_REPLY_HINT = (
    "The picture is already on the user's screen. Reply in one short line "
    '(e.g. "Here\'s your cafe, Sir."); do not describe it, link it, or '
    "mention the file path unless asked."
)


def _cost_text(cost: Optional[float]) -> str:
    return f"${cost:.3f}" if cost is not None else "cost unknown"


def _image_meta(record: ImageRecord, seconds: float) -> Dict[str, Any]:
    return {
        "id": record.id,
        "url": f"/v1/images/{record.id}",
        "prompt": record.prompt,
        "kind": record.kind,
        "model": record.model,
        "quality": record.quality,
        "size": record.size,
        "cost_usd": record.cost_usd,
        "parent_id": record.parent_id,
        "file": Path(record.path).name,
        "seconds": round(seconds, 1),
    }


def _success(tool: str, verb: str, result: Result) -> ToolResult:
    record = result.record
    return ToolResult(
        tool_name=tool,
        content=(
            f"{verb} image {record.id} ({_cost_text(record.cost_usd)}, "
            f"saved as {Path(record.path).name}). {_REPLY_HINT}"
        ),
        success=True,
        metadata={"image": _image_meta(record, result.seconds)},
    )


def _failure(tool: str, exc: Exception) -> ToolResult:
    # Refusals included: say what OpenAI said, once. No retry loop.
    return ToolResult(
        tool_name=tool,
        content=(
            f"Image request failed: {exc}. Tell the user plainly; do not retry "
            "unless they ask."
        ),
        success=False,
    )


class _ImageTool(BaseTool):
    is_local = False

    def __init__(self, service: Optional[ImageService] = None) -> None:
        self._service = service

    @property
    def service(self) -> ImageService:
        if self._service is None:
            self._service = ImageService()
        return self._service


@ToolRegistry.register("image_generate")
class ImageGenerateTool(_ImageTool):
    """Create a new image from a description."""

    tool_id = "image_generate"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="image_generate",
            description=(
                "Create (draw, generate, paint, make) a NEW picture from a "
                "description: 'draw me a cafe at sunset', 'make an image of...', "
                "'generate a logo for...'. Takes 15-50 s. The picture appears on "
                "the user's screen by itself. To change an existing picture use "
                "image_edit instead."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": (
                            "A full, specific description of the picture: "
                            "subject, setting, style, mood. Expand a short "
                            "request into a good image prompt."
                        ),
                    },
                },
                "required": ["prompt"],
            },
            category="media",
            timeout_seconds=_TIMEOUT_SECONDS,
            required_capabilities=["network:fetch"],
        )

    def execute(self, **params: Any) -> ToolResult:
        prompt = str(params.get("prompt") or "").strip()
        if not prompt:
            return ToolResult(
                tool_name="image_generate", content="No prompt provided.", success=False
            )
        try:
            return _success("image_generate", "Created", self.service.generate(prompt))
        except ImageError as exc:
            return _failure("image_generate", exc)


@ToolRegistry.register("image_edit")
class ImageEditTool(_ImageTool):
    """Edit the image pasted this turn, or refine an earlier generated one."""

    tool_id = "image_edit"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="image_edit",
            description=(
                "Change an existing picture. Use for (1) a picture the user "
                "pasted/attached this message: 'make the sky a sunset', 'remove "
                "the background' -> image='attached'; (2) refining a picture "
                "Sage made earlier: 'brighter', 'now anime style', 'add a cat' "
                "-> image='last' or that image's id (img_...). Takes 15-50 s; "
                "the result appears on screen by itself."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "instruction": {
                        "type": "string",
                        "description": (
                            "What to change, and to keep everything else the "
                            "same unless the user said otherwise."
                        ),
                    },
                    "image": {
                        "type": "string",
                        "description": (
                            "'attached' (pasted this message), 'last' (the "
                            "newest picture Sage made), or an image id "
                            "img_... from earlier in the conversation."
                        ),
                    },
                    "transparent_background": {
                        "type": "boolean",
                        "description": (
                            "True when the user wants the background removed "
                            "(a transparent PNG)."
                        ),
                    },
                },
                "required": ["instruction", "image"],
            },
            category="media",
            timeout_seconds=_TIMEOUT_SECONDS,
            required_capabilities=["network:fetch"],
        )

    def execute(self, **params: Any) -> ToolResult:
        instruction = str(params.get("instruction") or "").strip()
        which = str(params.get("image") or "").strip() or "last"
        transparent = bool(params.get("transparent_background"))
        if not instruction:
            return ToolResult(
                tool_name="image_edit",
                content="No instruction provided.",
                success=False,
            )
        parent_id: Optional[str] = None
        if which.lower() == "attached":
            png = attachments.attached_png()
            if png is None:
                # Falling back to "last" here would edit the wrong picture.
                return ToolResult(
                    tool_name="image_edit",
                    content=(
                        "No image is attached to this message. Ask the user "
                        "to paste it again, or use image='last' for Sage's "
                        "newest picture."
                    ),
                    success=False,
                )
        else:
            record = (
                self.service.store.latest()
                if which.lower() == "last"
                else self.service.store.get(which)
            )
            if record is None:
                return ToolResult(
                    tool_name="image_edit",
                    content=(
                        f"No image found for {which!r}. Sage has made none yet."
                        if which.lower() == "last"
                        else f"No image found with id {which!r}."
                    ),
                    success=False,
                )
            try:
                png = Path(record.path).read_bytes()
            except OSError:
                return ToolResult(
                    tool_name="image_edit",
                    content=(
                        f"Image {record.id}'s file is gone ({record.path}); it "
                        "may have been moved or deleted."
                    ),
                    success=False,
                )
            parent_id = record.id
        try:
            result = self.service.edit(
                png, instruction, parent_id=parent_id, transparent=transparent
            )
        except ImageError as exc:
            return _failure("image_edit", exc)
        return _success("image_edit", "Edited", result)


def send_photo_to_phone(path: str, caption: str) -> tuple[bool, str]:
    """Send a picture through ``[notifications] channel``. Returns (ok, why)."""
    from openjarvis.core.config import load_config

    spec = (load_config().notifications.channel or "").strip()
    if not spec:
        return False, "no phone channel is configured ([notifications] channel)"
    from openjarvis.agents.proactive_agent import _build_notification_channel

    channel = _build_notification_channel(spec)
    send_photo = getattr(channel, "send_photo", None)
    if channel is None or send_photo is None:
        return False, f"the {spec.partition(':')[0]} channel cannot send pictures"
    ok = bool(send_photo(spec.partition(":")[2], path, caption=caption))
    return ok, "" if ok else "Telegram refused or could not be reached"


@ToolRegistry.register("image_to_phone")
class ImageToPhoneTool(_ImageTool):
    """Send a generated picture to the user's phone (Telegram)."""

    tool_id = "image_to_phone"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="image_to_phone",
            description=(
                "Send a picture Sage made to the user's phone (Telegram): 'send "
                "it to my phone', 'text me that picture'. image='last' for the "
                "newest, or an image id img_..."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "'last' or an image id img_...",
                    },
                },
                "required": [],
            },
            category="media",
            timeout_seconds=60.0,
            required_capabilities=["network:fetch"],
        )

    def execute(self, **params: Any) -> ToolResult:
        which = str(params.get("image") or "").strip() or "last"
        store = self.service.store
        record = store.latest() if which.lower() == "last" else store.get(which)
        if record is None:
            return ToolResult(
                tool_name="image_to_phone",
                content=f"No image found for {which!r}.",
                success=False,
            )
        if not Path(record.path).exists():
            return ToolResult(
                tool_name="image_to_phone",
                content=f"Image {record.id}'s file is gone ({record.path}).",
                success=False,
            )
        ok, why = send_photo_to_phone(record.path, record.prompt[:900])
        if not ok:
            return ToolResult(
                tool_name="image_to_phone",
                content=f"Could not send it: {why}.",
                success=False,
            )
        return ToolResult(
            tool_name="image_to_phone",
            content=f"Sent image {record.id} to the user's phone (Telegram).",
            success=True,
        )
