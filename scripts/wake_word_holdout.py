"""Held-out check before a retrained verifier replaces the live one.

Takes the same sessions ``wake_word_train.py --train-only --session ...``
would train on, holds out one clip in five of each class (by clip, so no
window of a test clip leaks into training), trains on the rest, and scores
the held-out clips the way the live pipeline does -- lead-in noise, the
warm-up gate, patience -- with the candidate verifier and, for comparison,
with the verifier currently installed. Prints recall and rejection per
session block (the prefix before the first underscore of the clip name) so
"far" and "quiet" are read separately, not averaged away.

    .venv\\Scripts\\python.exe scripts\\wake_word_holdout.py \\
        --session natural_session_1 --session natural_session_2 \\
        --session natural_session_3 --session conditions_20260917
"""

from __future__ import annotations

import argparse
import pickle
import random
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import numpy as np
import soundfile as sf

from openjarvis.speech.wake_word import CHUNK_SAMPLES, WakeWordDetector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wake_word_train import (  # noqa: E402
    DATA_ROOT,
    MODEL_PATH,
    extract_negative_features,
    extract_positive_features,
)


def _block(path: Path) -> str:
    name = path.stem
    return name.split("_")[0] + "_" + name.split("_")[1] if "_" in name else name


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", action="append", required=True)
    parser.add_argument("--threshold", type=float, default=0.65)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    positives: list[Path] = []
    negatives: list[Path] = []
    for name in args.session:
        positives += sorted((DATA_ROOT / name / "positive").glob("*.wav"))
        negatives += sorted((DATA_ROOT / name / "negative").glob("*.wav"))
    rng = random.Random(args.seed)
    rng.shuffle(positives)
    rng.shuffle(negatives)
    held_pos, train_pos = (
        positives[: len(positives) // 5],
        positives[len(positives) // 5 :],
    )
    held_neg, train_neg = (
        negatives[: len(negatives) // 5],
        negatives[len(negatives) // 5 :],
    )
    print(
        f"train {len(train_pos)}+/{len(train_neg)}-, "
        f"held out {len(held_pos)}+/{len(held_neg)}-"
    )

    import openwakeword
    from openwakeword.custom_verifier_model import train_verifier_model
    from openwakeword.utils import download_models

    download_models(model_names=["_none_"])
    oww = openwakeword.Model(wakeword_models=[str(MODEL_PATH)])
    pos_f = extract_positive_features(oww, train_pos)
    neg_f = extract_negative_features(oww, train_neg)
    model = train_verifier_model(
        np.vstack((pos_f, neg_f)), np.array([1] * pos_f.shape[0] + [0] * neg_f.shape[0])
    )
    candidate = Path(tempfile.mkdtemp()) / f"{MODEL_PATH.stem}_verifier.pkl"
    with open(candidate, "wb") as f:
        pickle.dump(model, f)

    # The detector finds its verifier next to the model file; point a copy of
    # the model at the candidate so nothing touches C:\AI.
    candidate_model = candidate.with_name(MODEL_PATH.name)
    candidate_model.write_bytes(MODEL_PATH.read_bytes())

    for label, model_path in (
        ("installed", MODEL_PATH),
        ("candidate", candidate_model),
    ):
        print(f"\n== {label} verifier, threshold {args.threshold} ==")
        by_block: dict[str, list[bool]] = defaultdict(list)
        for p in held_pos:
            by_block["+ " + _block(p)].append(_score(model_path, p, args.threshold))
        for p in held_neg:
            by_block["- " + _block(p)].append(not _score(model_path, p, args.threshold))
        for block in sorted(by_block):
            ok = sum(by_block[block])
            n = len(by_block[block])
            print(f"  {block:22s} {ok}/{n}")
        pos_ok = sum(sum(v) for k, v in by_block.items() if k.startswith("+"))
        pos_n = sum(len(v) for k, v in by_block.items() if k.startswith("+"))
        neg_ok = sum(sum(v) for k, v in by_block.items() if k.startswith("-"))
        neg_n = sum(len(v) for k, v in by_block.items() if k.startswith("-"))
        print(f"  recall {pos_ok}/{pos_n}   rejected {neg_ok}/{neg_n}")


def _score(model_path: Path, path: Path, threshold: float) -> bool:
    lead_in = (np.random.default_rng(7).standard_normal(48000) * 100).astype(np.int16)
    detector = WakeWordDetector(model_path=str(model_path), threshold=threshold)
    data, _ = sf.read(str(path), dtype="int16")
    full = np.concatenate([lead_in, data])
    for i in range(0, len(full) - CHUNK_SAMPLES, CHUNK_SAMPLES):
        if detector.is_detection(detector.score(full[i : i + CHUNK_SAMPLES].tobytes())):
            return True
    return False


if __name__ == "__main__":
    main()
