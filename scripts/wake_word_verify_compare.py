"""Compare wake-word verifiers on the recorded corpus: accuracy and latency.

Runs each candidate over every clip under the wake-word sample directory
(``positive`` / ``negative`` folders) through the very same path the
server uses -- ``WakeWordVerifier`` with ``heard_wake_phrase`` -- so the
number reported is the number the wake word would see.

    .venv\\Scripts\\python.exe scripts\\wake_word_verify_compare.py
    .venv\\Scripts\\python.exe scripts\\wake_word_verify_compare.py ^
        --verifiers tiny.en parakeet --limit 60

Caveat that matters when reading the result: most positives here are the
1.8 s ring at the moment the detector fired, which is mid-phrase. A verifier
that "hears" the phrase in them is one that completes it (Whisper, prompted
with the phrase, does); one that transcribes what is really there may not.
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _clips(root: str, label: str, limit: int) -> list[str]:
    paths = sorted(glob.glob(os.path.join(root, "**", label, "*.wav"), recursive=True))
    return paths[:limit] if limit else paths


def _verifier(name: str, config):
    from openjarvis.speech.wake_word_verify import (
        WakeWordVerifier,
        local_verifier_backend,
    )

    config.speech.wake_word_verify_model = name
    import openjarvis.speech.wake_word_verify as module

    module._LOCAL_MODEL = None
    module._LOCAL_MODEL_KEY = ()
    backend = local_verifier_backend(config)
    return WakeWordVerifier(backend, initial_prompt=config.speech.initial_prompt)


async def _run(
    verifier, paths: list[str], strict: bool
) -> tuple[int, list[float], list[str]]:
    hits, times, heard = 0, [], []
    for path in paths:
        pcm = Path(path).read_bytes()[44:]  # 16 kHz mono 16-bit WAV body
        verdict = await verifier.verify(pcm, strict=strict)
        hits += int(verdict.confirmed)
        times.append(verdict.ms)
        heard.append(verdict.heard)
    return hits, times, heard


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=r"C:\AI\OpenJarvis-Data\wake_word_samples")
    parser.add_argument("--verifiers", nargs="+", default=["tiny.en", "parakeet"])
    parser.add_argument(
        "--limit", type=int, default=0, help="clips per class (0 = all)"
    )
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    from openjarvis.core.config import load_config

    config = load_config()
    positives = _clips(args.root, "positive", args.limit)
    negatives = _clips(args.root, "negative", args.limit)
    print(f"{len(positives)} positives, {len(negatives)} negatives under {args.root}\n")
    print(
        f"{'verifier':10s} {'accept+':>8s} {'accept-':>8s} {'p50 ms':>7s} {'p95 ms':>7s}  (loaded in)"  # noqa: E501
    )
    for name in args.verifiers:
        started = time.perf_counter()
        verifier = _verifier(name, config)
        asyncio.run(verifier.verify(Path(positives[0]).read_bytes()[44:]))  # warm
        load_s = time.perf_counter() - started
        pos_hits, pos_ms, pos_heard = asyncio.run(
            _run(verifier, positives, args.strict)
        )
        neg_hits, neg_ms, _ = asyncio.run(_run(verifier, negatives, args.strict))
        ms = pos_ms + neg_ms
        print(
            f"{name:10s} {pos_hits:4d}/{len(positives):<3d} {neg_hits:4d}/{len(negatives):<3d}"  # noqa: E501
            f" {statistics.median(ms):7.0f} {sorted(ms)[int(len(ms) * 0.95) - 1]:7.0f}  ({load_s:.1f}s)"  # noqa: E501
        )
        sample = [h for h in pos_heard if h][:6]
        print(f"{'':10s} heard e.g. {sample}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
