"""Where the Parakeet ONNX files live and how they arrive.

The export is fetched once into the data directory (outside the repo, beside
the wake-word samples), the way faster-whisper and openWakeWord keep their
models. A ``[speech] parakeet_model_dir`` in config.toml overrides the
location; a partial download is detected by size, not by presence.
"""

from __future__ import annotations

import logging
import os
import urllib.request
from typing import Dict, Optional

from openjarvis.core.paths import get_data_dir
from openjarvis.speech.parakeet.config import ONNX_FILES, ONNX_REPO, ONNX_SUBDIR

logger = logging.getLogger(__name__)

# Sizes of the published export, so a truncated download is refused rather
# than fed to onnxruntime, which fails late and unhelpfully.
EXPECTED_SIZES: Dict[str, int] = {
    "encoder.onnx": 459_341_289,
    "decoder_joint.onnx": 21_347_639,
    "tokenizer.json": 20_053,
}


def default_model_dir() -> str:
    return os.path.join(get_data_dir(), "models", "parakeet-realtime-eou")


def model_dir(configured: str = "") -> str:
    return (
        configured
        or os.environ.get("OPENJARVIS_PARAKEET_DIR", "")
        or default_model_dir()
    )


def download_url(name: str) -> str:
    return f"https://huggingface.co/{ONNX_REPO}/resolve/main/{ONNX_SUBDIR}/{name}"


def missing_files(directory: str, *, quant: str = "") -> list[str]:
    """Files absent or the wrong size. Quantised variants are produced
    locally, so only their presence is checked."""
    missing = []
    for name in ONNX_FILES:
        path = os.path.join(directory, name)
        if not os.path.exists(path):
            missing.append(name)
            continue
        expected = EXPECTED_SIZES.get(name)
        if expected is not None and os.path.getsize(path) != expected:
            missing.append(name)
    if quant:
        for stem in ("encoder", "decoder_joint"):
            if not os.path.exists(os.path.join(directory, f"{stem}_{quant}.onnx")):
                missing.append(f"{stem}_{quant}.onnx")
    return missing


def unavailable_reason(directory: str, *, quant: str = "") -> Optional[str]:
    missing = missing_files(directory, quant=quant)
    if missing:
        return f"Parakeet model files missing in {directory}: {', '.join(missing)}"
    return None


def ensure_downloaded(directory: str, *, progress=None) -> None:
    """Fetch any missing or truncated file. Blocking; call off the loop."""
    os.makedirs(directory, exist_ok=True)
    for name in missing_files(directory):
        if name not in ONNX_FILES:
            continue
        url = download_url(name)
        target = os.path.join(directory, name)
        partial = target + ".part"
        logger.info("Parakeet: downloading %s", url)
        with (
            urllib.request.urlopen(url, timeout=60) as response,
            open(partial, "wb") as out,
        ):
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while True:
                block = response.read(1 << 20)
                if not block:
                    break
                out.write(block)
                done += len(block)
                if progress:
                    progress(name, done, total)
        expected = EXPECTED_SIZES.get(name)
        if expected is not None and os.path.getsize(partial) != expected:
            os.remove(partial)
            raise RuntimeError(f"Parakeet: {name} downloaded with the wrong size")
        os.replace(partial, target)
