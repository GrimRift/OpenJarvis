"""NVIDIA Parakeet Realtime EOU 120M as a local streaming STT provider.

Runs the published ONNX export on onnxruntime (CUDA when available, CPU
otherwise) inside Sage's own process -- no NeMo, no torch. See ``session``
for how its tokens become Sage's turn events and ``decoder`` for where the
per-word confidence comes from.
"""

from __future__ import annotations

from typing import Optional

from openjarvis.speech.parakeet import model as _model
from openjarvis.speech.parakeet import weights
from openjarvis.speech.parakeet.session import (
    ParakeetEngine,
    ParakeetSession,
    load_engine,
    loaded_engine,
)


def unavailable_reason(*, model_dir: str = "", quant: str = "") -> Optional[str]:
    """Why Parakeet cannot serve right now, or None. Cheap: no model load."""
    reason = _model.available_reason()
    if reason:
        return reason
    return weights.unavailable_reason(weights.model_dir(model_dir), quant=quant)


def is_available(*, model_dir: str = "", quant: str = "") -> bool:
    return unavailable_reason(model_dir=model_dir, quant=quant) is None


__all__ = [
    "ParakeetEngine",
    "ParakeetSession",
    "is_available",
    "load_engine",
    "loaded_engine",
    "unavailable_reason",
    "weights",
]
