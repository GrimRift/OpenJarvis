"""The image the user pasted this turn, for ``image_edit(image="attached")``.

The model must never carry an image through tool arguments, so the tool reads
it from here. A process-level slot rather than a ``ContextVar``, for the
reason measured in ``security/page_access.py``: a ContextVar set in the
request handler is gone by the time a tool runs in the executor's thread.

Every chat request calls ``set_turn`` with its messages, so the slot always
holds exactly the newest turn's images -- a turn with none clears it, and an
image pasted three messages ago is never edited by mistake.
"""

from __future__ import annotations

import base64
import re
import threading
import time
from typing import Any, List, Optional

#: An image left in the slot longer than this is not "the one just pasted".
MAX_AGE_SECONDS = 600.0

_lock = threading.Lock()
_images: List[str] = []
_set_at = 0.0


def _newest_user_images(messages: Any) -> List[str]:
    for message in reversed(list(messages or [])):
        role = getattr(message, "role", None)
        if role is None and isinstance(message, dict):
            role = message.get("role")
        if str(getattr(role, "value", role)) != "user":
            continue
        images = getattr(message, "images", None)
        if images is None and isinstance(message, dict):
            images = message.get("images")
        return [str(i) for i in (images or []) if i]
    return []


def set_turn(messages: Any) -> None:
    """Record this turn's pasted images (or none), replacing the last turn's."""
    global _images, _set_at
    with _lock:
        _images = _newest_user_images(messages)
        _set_at = time.monotonic()


def clear() -> None:
    global _images, _set_at
    with _lock:
        _images = []
        _set_at = 0.0


def attached_png() -> Optional[bytes]:
    """The first image pasted this turn, decoded, or None if there is none."""
    with _lock:
        if not _images or time.monotonic() - _set_at > MAX_AGE_SECONDS:
            return None
        data = _images[0]
    if data.startswith("data:"):
        data = data.partition(",")[2]
    try:
        return base64.b64decode(data, validate=False)
    except (ValueError, TypeError):
        return None


# An image turn is normally answered by the vision model in one step, with no
# tools (routes._has_attached_image). These are the words that ask to CHANGE
# the picture rather than look at it, so that turn goes to the tool loop
# instead. Anchored on edit verbs aimed at the picture; a question ("what's
# in the background?") has no such verb and stays on the vision path.
_EDIT_INTENT = re.compile(
    r"""
    \b(?:
        (?:make|turn|change|convert|transform|edit|modify|recolou?r|retouch
            |restyle)
            \s+(?:it|this|that|the|them|him|her|my|its|into)\b
      | (?:remove|erase|delete|cut\s+out|get\s+rid\s+of)\s+(?:the\s+)?(?:\w+\s+){0,2}
            (?:background|bg|person|people|text|watermark|object|sky|car|him|her
            |them|it)\b
      | (?:add|put|replace|swap)\b[^?]{0,60}
            \b(?:to|in|into|on|with|onto)\s+(?:it|this|the|that)\b
      | (?:in|into|as)\s+(?:an?\s+)?(?:anime|cartoon|pixar|ghibli|watercolou?r
            |oil\s+painting|pixel\s+art|sketch|comic|3d|van\s+gogh)\b(?:\s+style)?
      | \w+\s+style\b(?=[^?]*$)
      | \b(?:brighter|darker|sunset|night\s+time|black\s+and\s+white)\b(?=[^?]*$)
      | \bupscale\b|\bcolou?ri[sz]e\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def wants_edit(text: str) -> bool:
    """Whether a message sent with an image asks to change that image."""
    return bool(_EDIT_INTENT.search(text or ""))
