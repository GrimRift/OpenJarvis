"""Ask the vision model about an image, from the server side.

Extracted from ``tools/capture_screen.py``, which needed this first and whose
constants carry the measurements behind them. A second caller now exists --
document pages that no text extractor can read correctly -- and duplicating
the model resolution, the downscale and the empty-answer retry would have
meant two places to fix when any of them is wrong.

A tool result is text, and OpenAI does not accept images in a ``tool``
message, so the picture is answered *here* rather than handed back to the
agent loop.
"""

from __future__ import annotations

import base64
import io
from typing import Any, Optional

#: Describing a picture is not a reasoning problem. The engine defaults to
#: "high" for any tool-free call, which is the wrong default here and buys
#: nothing measurable: high 3.4s/138 tokens versus minimal 2.4s/100 on the
#: same capture.
VISION_REASONING_EFFORT = "minimal"

#: A reasoning model spends tokens thinking before it writes anything, and a
#: dense image is a lot to think about. Measured on a 1280px capture of a busy
#: browser window: 600 tokens produced ``finish_reason: length`` and **zero
#: characters**, all of it spent reasoning; 3000 produced a full answer using
#: 1523.
VISION_MAX_TOKENS = 3000

#: One retry with real headroom, mirroring morning_digest's handling of the
#: same failure. Empty output must never be reported as "nothing to see".
VISION_RETRY_MAX_TOKENS = 8000


def resolve_vision_model(config: Any) -> str:
    """Which model gets the picture.

    An explicit setting wins. Otherwise prefer the cloud model already
    configured for chat -- it is the stronger reader and needs no download --
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


def downscale(image: Any, max_edge: int) -> Any:
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


def to_data_url(image: Any, fmt: str = "PNG") -> str:
    """A PIL image as a ``data:`` URL, which is what the engine accepts."""
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/{fmt.lower()};base64,{encoded}"


def ask_vision(
    data_url: str,
    question: str,
    *,
    model: str,
    engine: Any = None,
    max_tokens: int = VISION_MAX_TOKENS,
    retry_max_tokens: int = VISION_RETRY_MAX_TOKENS,
) -> Optional[str]:
    """Answer *question* about one image, or ``None`` if the model said nothing."""
    from openjarvis.core.types import Message, Role

    message = Message(role=Role.USER, content=question)
    message.images = [data_url]

    if engine is None:
        import os

        # CloudEngine reads os.environ; the keys live in cloud-keys.env, which
        # only cloud_router knows how to read. Without this the caller reports
        # "OpenAI client not available" on a machine perfectly well configured
        # for chat.
        try:
            from openjarvis.server.cloud_router import _load_keys

            for name, value in _load_keys().items():
                os.environ.setdefault(name, value)
        except Exception:
            pass

        from openjarvis.engine.cloud import CloudEngine

        engine = CloudEngine()

    result = engine.generate(
        [message],
        model=model,
        max_tokens=max_tokens,
        reasoning_effort=VISION_REASONING_EFFORT,
    )
    answer = str((result or {}).get("content") or "").strip()
    if answer:
        return answer

    # Ran out of budget mid-thought rather than having nothing to say.
    if str((result or {}).get("finish_reason") or "") != "length":
        return None
    retry = engine.generate(
        [message],
        model=model,
        max_tokens=retry_max_tokens,
        reasoning_effort=VISION_REASONING_EFFORT,
    )
    return str((retry or {}).get("content") or "").strip() or None


__all__ = [
    "VISION_MAX_TOKENS",
    "VISION_REASONING_EFFORT",
    "VISION_RETRY_MAX_TOKENS",
    "ask_vision",
    "downscale",
    "resolve_vision_model",
    "to_data_url",
]
