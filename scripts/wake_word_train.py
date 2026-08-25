"""Record fresh mic samples and retrain the "Hey Sage" custom verifier.

The verifier (``<model>_verifier.pkl``, see ``speech/wake_word.py``) is a
small classifier trained on real recordings of one specific voice/mic, layered
on top of the openWakeWord model itself (which is trained purely on synthetic
TTS and barely responds to real speech at all). Whenever the mic changes,
the verifier trained on the old one no longer matches — this script rebuilds
it from a fresh recording session on whatever mic is currently the Windows
default input device.

Prompts through, in order:
  - 25 positive takes of "Hey Sage"
  - short negative reactions ("no", "hey", "wait", ...), a few repeats each
  - a handful of full negative sentences
  - a couple of room-ambience clips (stay quiet)

then extracts openWakeWord features from all of it, trains a fresh logistic
regression verifier, backs up the old one, writes the new one in its place,
and scores a few of the just-recorded clips through it as a smoke test.

Run interactively (it needs you to actually speak at each prompt):

    .venv\\Scripts\\python.exe scripts\\wake_word_train.py

Or retrain from an already-recorded session without recording again --
e.g. one captured through wake-word-trainer.html (the browser page records
through the exact mic pipeline live detection uses; this script's own
sounddevice/MME capture turned out to record at a noticeably lower level,
which produced a verifier that barely recognized real speech even though it
scored its own too-quiet training clips fine):

    .venv\\Scripts\\python.exe scripts\\wake_word_train.py --train-only \\
        --session <session_id>
"""

from __future__ import annotations

import argparse
import pickle
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

from openjarvis.speech.wake_word import CHUNK_SAMPLES, WARMUP_FRAMES, WakeWordDetector

SAMPLE_RATE = 16000
MODEL_PATH = Path(r"C:\AI\Hey_Sage.onnx")
MODEL_NAME = MODEL_PATH.stem
VERIFIER_PATH = MODEL_PATH.with_name(f"{MODEL_NAME}_verifier.pkl")
DATA_ROOT = Path(r"C:\AI\OpenJarvis-Data\wake_word_samples")

POSITIVE_COUNT = 25
POSITIVE_DURATION_S = 1.8

NEGATIVE_REACTIONS = ["no", "hey", "wait", "stop", "okay", "what", "yeah", "hold on"]
NEGATIVE_REACTION_REPEATS = 2
NEGATIVE_REACTION_DURATION_S = 1.5

NEGATIVE_SENTENCES = [
    "Can you check the weather for tomorrow.",
    "I need to leave for work in twenty minutes.",
    "That meeting got moved to next Thursday.",
    "Turn the volume down a little please.",
    "I'm not sure what I want for dinner tonight.",
]
NEGATIVE_SENTENCE_DURATION_S = 4.0

NEGATIVE_AMBIENCE_COUNT = 2
NEGATIVE_AMBIENCE_DURATION_S = 8.0

# Below this peak (out of int16's +-32767), a "speech" clip almost certainly
# missed the actual word — flagged so a dead take doesn't quietly pollute
# training instead of being noticed only after the retrained model flops.
QUIET_TAKE_WARNING_PEAK = 400


def record_clip(duration_s: float) -> np.ndarray:
    audio = sd.rec(
        int(duration_s * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
    )
    sd.wait()
    return audio.reshape(-1)


def prompt_and_record(
    prompt: str, duration_s: float, out_path: Path, *, is_speech: bool = True
) -> Path:
    input(f"{prompt}  [Enter to record {duration_s:.1f}s]")
    print("  recording...", end="", flush=True)
    audio = record_clip(duration_s)
    sf.write(str(out_path), audio, SAMPLE_RATE, subtype="PCM_16")
    peak = int(np.abs(audio).max())
    too_quiet = is_speech and peak < QUIET_TAKE_WARNING_PEAK
    flag = " -- very quiet, might have missed it" if too_quiet else ""
    print(f" done (peak {peak}){flag}")
    return out_path


def record_session(session_dir: Path) -> tuple[list[Path], list[Path]]:
    pos_dir = session_dir / "positive"
    neg_dir = session_dir / "negative"
    pos_dir.mkdir(parents=True, exist_ok=True)
    neg_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nRecording into {session_dir}\n")

    print('=== Positive: say "Hey Sage" right after pressing Enter, no pause ===\n')
    positive_paths = [
        prompt_and_record(
            f'[{i}/{POSITIVE_COUNT}] Say "Hey Sage"',
            POSITIVE_DURATION_S,
            pos_dir / f"hey_sage_{i:02d}.wav",
        )
        for i in range(1, POSITIVE_COUNT + 1)
    ]

    print("\n=== Negative: short reactions ===\n")
    negative_paths: list[Path] = []
    n = 0
    for phrase in NEGATIVE_REACTIONS:
        for _ in range(NEGATIVE_REACTION_REPEATS):
            n += 1
            slug = phrase.replace(" ", "_")
            negative_paths.append(
                prompt_and_record(
                    f'Say "{phrase}"',
                    NEGATIVE_REACTION_DURATION_S,
                    neg_dir / f"reaction_{n:02d}_{slug}.wav",
                )
            )

    print("\n=== Negative: full sentences ===\n")
    for i, sentence in enumerate(NEGATIVE_SENTENCES, start=1):
        negative_paths.append(
            prompt_and_record(
                f'Say: "{sentence}"',
                NEGATIVE_SENTENCE_DURATION_S,
                neg_dir / f"sentence_{i:02d}.wav",
            )
        )

    print("\n=== Negative: room ambience (stay quiet) ===\n")
    for i in range(1, NEGATIVE_AMBIENCE_COUNT + 1):
        negative_paths.append(
            prompt_and_record(
                f"[{i}/{NEGATIVE_AMBIENCE_COUNT}] Stay quiet",
                NEGATIVE_AMBIENCE_DURATION_S,
                neg_dir / f"ambience_{i:02d}.wav",
                is_speech=False,
            )
        )

    print(
        f"\nRecorded {len(positive_paths)} positive, "
        f"{len(negative_paths)} negative clips.\n"
    )
    return positive_paths, negative_paths


def _loudest_frame(data: np.ndarray) -> int:
    step = CHUNK_SAMPLES
    n_frames = len(data) // step
    if n_frames == 0:
        return 0
    energies = [
        float(np.abs(data[i * step : (i + 1) * step]).mean()) for i in range(n_frames)
    ]
    return int(np.argmax(energies))


def _clip_features(
    oww, data: np.ndarray, *, word_frame: int | None, repeats: int
) -> list[np.ndarray]:
    """Feature windows for one clip, on a buffer that's already settled.

    openWakeWord scores a rolling window of the last ``model_inputs`` frames
    (16 = 1.28s here), not each frame alone, so which frames features are
    captured at matters as much as the audio itself -- and a freshly reset
    model's score spikes right as that window first fills, regardless of
    audio content (confirmed empirically: random noise and this session's
    own quiet-room negatives both spike there). Recording clips start right
    after a key press, so the spoken word usually lands close to that same
    fill point; capturing there taught a verifier that recognized "a buffer
    that just filled" rather than the word -- it fired on refresh noise and
    (proven by feeding a positive with realistic lead-in first) scored the
    real word only ~0.53 once it wasn't sitting in that transient, versus
    ~0.90 when it was.

    Every clip is therefore padded with ``WARMUP_FRAMES`` of quiet noise
    before any feature is captured -- for both classes, so neither leans on
    the transient -- landing every capture on a buffer already past it. This
    mirrors ``WakeWordDetector``'s own runtime warm-up gate (wake_word.py),
    so training and inference finally agree on what a "ready" buffer is.
    """
    window = oww.model_inputs[MODEL_NAME]
    step = CHUNK_SAMPLES
    pad_frames = WARMUP_FRAMES + 2  # a little past the runtime gate, for margin
    floor = pad_frames + window - 1  # earliest frame whose window is past the padding
    out: list[np.ndarray] = []

    for rep in range(repeats):
        oww.reset()
        # Small per-repeat offset so repeats aren't byte-identical (the
        # library's own extractor does the same for variation).
        offset = 0 if rep == 0 else int(np.random.randint(0, step))
        noise = np.random.default_rng().standard_normal(pad_frames * step) * 100
        pad = noise.astype(np.int16)
        d = np.concatenate([pad, data[offset:]])
        n_frames = (len(d) - step) // step
        if word_frame is None:
            lo, hi = floor, n_frames - 1
        else:
            w = max(pad_frames, word_frame + pad_frames - (1 if offset else 0))
            lo = max(floor, w)
            hi = min(w + window - 1, n_frames - 1)
        if hi < lo:
            continue
        for i in range(n_frames):
            oww.predict(d[i * step : (i + 1) * step])
            if lo <= i <= hi:
                out.append(oww.preprocessor.get_features(window))
    return out


def extract_positive_features(oww, clip_paths: list[Path]) -> np.ndarray:
    feats: list[np.ndarray] = []
    for p in clip_paths:
        data = sf.read(str(p), dtype="int16")[0]
        feats.extend(
            _clip_features(oww, data, word_frame=_loudest_frame(data), repeats=5)
        )
    if not feats:
        raise RuntimeError(
            'No positive features could be extracted -- check that the recordings '
            'actually contain "Hey Sage" audibly.'
        )
    return np.vstack(feats)


def extract_negative_features(oww, clip_paths: list[Path]) -> np.ndarray:
    feats: list[np.ndarray] = []
    for p in clip_paths:
        data = sf.read(str(p), dtype="int16")[0]
        feats.extend(_clip_features(oww, data, word_frame=None, repeats=1))
    if not feats:
        raise RuntimeError("No negative features could be extracted")
    return np.vstack(feats)


def train_verifier(
    positive_paths: list[Path], negative_paths: list[Path], output_path: Path
) -> None:
    import openwakeword
    from openwakeword.custom_verifier_model import train_verifier_model
    from openwakeword.utils import download_models

    download_models(model_names=["_none_"])
    oww = openwakeword.Model(wakeword_models=[str(MODEL_PATH)])

    print("Extracting positive features...")
    positive_features = extract_positive_features(oww, positive_paths)
    print(f"  {positive_features.shape[0]} positive feature windows")

    print("Extracting negative features...")
    negative_features = extract_negative_features(oww, negative_paths)
    print(f"  {negative_features.shape[0]} negative feature windows")

    print("Training logistic regression verifier...")
    model = train_verifier_model(
        np.vstack((positive_features, negative_features)),
        np.array([1] * positive_features.shape[0] + [0] * negative_features.shape[0]),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        pickle.dump(model, f)
    print(f"Saved verifier to {output_path}")


def smoke_test(positive_paths: list[Path], negative_paths: list[Path]) -> None:
    """Score clips the way they'll actually be heard: mid-session, gated.

    A bare clip fed straight into a fresh detector reports whatever the
    buffer-fill transient (see _clip_features' docstring) adds on top of the
    real word, which is exactly the number that looked fine before this was
    ever a shipped, working detector. Prefixing lead-in noise and reading
    ``is_detection()`` (which applies the warm-up gate and patience, not
    just a bare threshold) is what the live pipeline actually does.
    """
    lead_in = (np.random.default_rng().standard_normal(48000) * 100).astype(np.int16)

    def fires(path: Path) -> bool:
        detector = WakeWordDetector(model_path=str(MODEL_PATH))
        data, _ = sf.read(str(path), dtype="int16")
        full = np.concatenate([lead_in, data])
        hit = False
        for i in range(0, len(full) - CHUNK_SAMPLES, CHUNK_SAMPLES):
            score = detector.score(full[i : i + CHUNK_SAMPLES].tobytes())
            if detector.is_detection(score):
                hit = True
        return hit

    print("\n=== Smoke test: mid-session, with the real detection gate ===")
    pairs = (("positive", positive_paths, True), ("negative", negative_paths, False))
    for label, paths, wants_fire in pairs:
        hits = 0
        for p in paths:
            fired = fires(p)
            hits += fired == wants_fire
            mark = "ok" if fired == wants_fire else "WRONG"
            print(f"  {label:8s} {p.name:30s} fired={fired} [{mark}]")
        print(f"  -> {hits}/{len(paths)} correct\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--train-only",
        action="store_true",
        help="Skip recording; retrain from an existing session's clips.",
    )
    parser.add_argument(
        "--session",
        default="",
        help="Session id under wake_word_samples/ to train from (with --train-only).",
    )
    args = parser.parse_args()

    if args.train_only:
        if not args.session:
            parser.error("--train-only requires --session")
        session_dir = DATA_ROOT / args.session
        if not session_dir.is_dir():
            parser.error(f"No such session directory: {session_dir}")
        positive_paths = sorted((session_dir / "positive").glob("*.wav"))
        negative_paths = sorted((session_dir / "negative").glob("*.wav"))
        if not positive_paths or not negative_paths:
            parser.error(f"Session {args.session} has no positive/negative clips")
        print(f"Training from {session_dir}")
        print(
            f"  {len(positive_paths)} positive, "
            f"{len(negative_paths)} negative clips\n"
        )
    else:
        session_dir = DATA_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            positive_paths, negative_paths = record_session(session_dir)
        except KeyboardInterrupt:
            print(f"\nStopped early. Partial recordings kept at {session_dir}")
            return

    if VERIFIER_PATH.exists():
        backup = VERIFIER_PATH.with_name(
            f"{VERIFIER_PATH.stem}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}{VERIFIER_PATH.suffix}"
        )
        VERIFIER_PATH.rename(backup)
        print(f"Backed up existing verifier to {backup}")

    train_verifier(positive_paths, negative_paths, VERIFIER_PATH)
    smoke_test(positive_paths, negative_paths)

    print("\nDone. Restart the Sage backend to pick up the new verifier.")


if __name__ == "__main__":
    main()
