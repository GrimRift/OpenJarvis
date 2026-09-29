"""Create and edit images with OpenAI, price them, save them, record the cost.

One function per ability; the tools are thin wrappers. The client factory and
the telemetry sink are injectable so tests never reach the network or the
live telemetry.db.
"""

from __future__ import annotations

import base64
import io
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from openjarvis.images import settings as image_settings
from openjarvis.images.store import ImageRecord, ImageStore

logger = logging.getLogger(__name__)

#: USD per 1M tokens, from OpenAI's published pricing page (read 2026-09-29).
#: A model missing here has an UNKNOWN cost -- never a zero one.
PRICES: Dict[str, Dict[str, float]] = {
    "gpt-image-2.5-flare": {"text_in": 5.0, "image_in": 8.0, "out": 30.0},
    "gpt-image-2.5-sunburst": {"text_in": 5.0, "image_in": 8.0, "out": 30.0},
    "gpt-image-2": {"text_in": 5.0, "image_in": 8.0, "out": 30.0},
    "gpt-image-1.5": {"text_in": 5.0, "image_in": 8.0, "out": 32.0},
    "gpt-image-1": {"text_in": 5.0, "image_in": 10.0, "out": 40.0},
    "gpt-image-1-mini": {"text_in": 2.0, "image_in": 2.5, "out": 8.0},
}


class ImageError(Exception):
    """A failure to report to the user as it is (refusals included)."""


@dataclass
class Usage:
    text_in: int = 0
    image_in: int = 0
    out: int = 0

    @property
    def prompt_tokens(self) -> int:
        return self.text_in + self.image_in


def usage_from(response: Any) -> Optional[Usage]:
    """The token usage an Images API response reports, or None if it has none."""
    raw = getattr(response, "usage", None)
    if raw is None:
        return None
    data = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
    details = data.get("input_tokens_details") or {}
    return Usage(
        text_in=int(details.get("text_tokens") or 0),
        image_in=int(details.get("image_tokens") or 0),
        out=int(data.get("output_tokens") or 0),
    )


def cost_usd(model: str, usage: Optional[Usage]) -> Optional[float]:
    """Cost of one call, or None when the price or the usage is unknown."""
    price = PRICES.get(model)
    if price is None or usage is None:
        return None
    return (
        usage.text_in * price["text_in"]
        + usage.image_in * price["image_in"]
        + usage.out * price["out"]
    ) / 1_000_000


def _default_client() -> Any:
    from openai import OpenAI

    from openjarvis.core.credentials import get_tool_credential

    key = get_tool_credential("cloud_openai", "OPENAI_API_KEY") or get_tool_credential(
        "image_generate", "OPENAI_API_KEY"
    )
    if not key:
        raise ImageError("No OpenAI API key is configured (OPENAI_API_KEY).")
    return OpenAI(api_key=key, timeout=170.0, max_retries=0)


def _record_telemetry(
    model: str, usage: Optional[Usage], cost: Optional[float], seconds: float, kind: str
) -> None:
    """One telemetry.db row per image, so the Dashboard's totals include it."""
    try:
        from openjarvis.core.config import load_config
        from openjarvis.core.types import TelemetryRecord
        from openjarvis.telemetry.store import TelemetryStore

        store = TelemetryStore(load_config().telemetry.db_path, batch_size=1)
        try:
            store.record(
                TelemetryRecord(
                    timestamp=time.time(),
                    model_id=model,
                    engine="openai-images",
                    agent="image",
                    prompt_tokens=usage.prompt_tokens if usage else 0,
                    completion_tokens=usage.out if usage else 0,
                    total_tokens=(usage.prompt_tokens + usage.out) if usage else 0,
                    latency_seconds=seconds,
                    cost_usd=cost or 0.0,
                    metadata={
                        "kind": kind,
                        "image_tokens_in": usage.image_in if usage else 0,
                        "cost_known": cost is not None,
                    },
                )
            )
        finally:
            store.close()
    except Exception:  # noqa: BLE001 -- bookkeeping must never fail the image
        logger.warning("Could not record image telemetry", exc_info=True)


@dataclass
class Result:
    record: ImageRecord
    seconds: float
    usage: Optional[Usage]


class ImageService:
    def __init__(
        self,
        *,
        store: Optional[ImageStore] = None,
        client_factory: Callable[[], Any] = _default_client,
        record_telemetry: Callable[..., None] = _record_telemetry,
        settings_loader: Callable[
            [], image_settings.ImageSettings
        ] = image_settings.load,
    ) -> None:
        self._store = store
        self._client_factory = client_factory
        self._record_telemetry = record_telemetry
        self._settings_loader = settings_loader

    @property
    def store(self) -> ImageStore:
        if self._store is None:
            self._store = ImageStore()
        return self._store

    def settings(self) -> image_settings.ImageSettings:
        return self._settings_loader()

    def _finish(
        self,
        response: Any,
        *,
        began: float,
        prompt: str,
        kind: str,
        cfg: image_settings.ImageSettings,
        parent_id: Optional[str],
    ) -> Result:
        seconds = time.perf_counter() - began
        data = getattr(response, "data", None) or []
        b64 = getattr(data[0], "b64_json", None) if data else None
        if not b64:
            raise ImageError("OpenAI returned no image.")
        usage = usage_from(response)
        cost = cost_usd(cfg.model, usage)
        record = self.store.save(
            base64.b64decode(b64),
            folder=cfg.resolved_save_dir(),
            prompt=prompt,
            kind=kind,
            model=cfg.model,
            quality=cfg.quality,
            size=cfg.size,
            cost_usd=cost,
            parent_id=parent_id,
        )
        self._record_telemetry(cfg.model, usage, cost, seconds, kind)
        return Result(record=record, seconds=seconds, usage=usage)

    def _call(self, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except ImageError:
            raise
        except Exception as exc:  # noqa: BLE001 -- reported, never retried
            message = getattr(exc, "message", None) or str(exc)
            raise ImageError(message) from exc

    def generate(self, prompt: str) -> Result:
        cfg = self.settings()
        if not cfg.enabled:
            raise ImageError("Image generation is turned off in Settings > Images.")
        client = self._call(self._client_factory)
        began = time.perf_counter()
        response = self._call(
            lambda: client.images.generate(
                model=cfg.model, prompt=prompt, size=cfg.size, quality=cfg.quality
            )
        )
        return self._finish(
            response,
            began=began,
            prompt=prompt,
            kind="generate",
            cfg=cfg,
            parent_id=None,
        )

    def edit(
        self,
        png: bytes,
        instruction: str,
        *,
        parent_id: Optional[str] = None,
        transparent: bool = False,
    ) -> Result:
        cfg = self.settings()
        if not cfg.enabled:
            raise ImageError("Image generation is turned off in Settings > Images.")
        client = self._call(self._client_factory)
        source = io.BytesIO(png)
        source.name = "image.png"
        extra: Dict[str, Any] = {"background": "transparent"} if transparent else {}
        began = time.perf_counter()
        response = self._call(
            lambda: client.images.edit(
                model=cfg.model,
                image=source,
                prompt=instruction,
                size=cfg.size,
                quality=cfg.quality,
                **extra,
            )
        )
        return self._finish(
            response,
            began=began,
            prompt=instruction,
            kind="edit",
            cfg=cfg,
            parent_id=parent_id,
        )
