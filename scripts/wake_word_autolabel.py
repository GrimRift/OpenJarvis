"""Segment a long natural talking session into labeled wake-word training clips.

wake_word_train.py's recording flow needs one deliberate take per clip (press
Enter, say the word) -- fine for the first ~50-clip pass, too slow for adding
real bulk data. This instead takes ONE long recording (captured via the
useWakeWord.ts debug "save session" button, which streams through the exact
live mic pipeline) and:

  1. Finds each spoken burst by frame energy (the same 80ms/1280-sample
     frames the wake-word model itself scores).
  2. Transcribes each one with the project's own faster-whisper backend.
  3. Labels it positive if the transcript is recognizably "Hey Sage" (fuzzy:
     just needs "hey" and "sage"/"sag" as substrings, so minor STT slips
     like "hey sage." or "hey, saget" still count), negative otherwise.
  4. Also samples a few long quiet stretches as negative ambience.

Writes into the same session-directory layout wake_word_train.py already
understands, so training is just:

    .venv\\Scripts\\python.exe scripts\\wake_word_train.py \\
        --train-only --session <session_id>

Run:

    .venv\\Scripts\\python.exe scripts\\wake_word_autolabel.py <input.wav> <session_id>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

from openjarvis.core.paths import get_data_dir

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1280
FRAME_S = CHUNK_SAMPLES / SAMPLE_RATE

# A burst frame's energy must clear this multiple of the clip's own noise
# floor -- relative, not absolute, since mic gain varies session to session.
NOISE_MULTIPLIER = 4.0
MIN_ABS_ENERGY = 150.0
# Peak-centered windows, not gap-based run merging: an early first-pass cut
# gap-merged active frames and came back transcribing "is age." / "a sage."
# instead of "Hey Sage" -- "Sage" is the loud, emphasized half for this
# voice, "Hey" quiet and separated enough by a pause that it fell out of the
# same run and never got captured at all. A fixed, generously asymmetric
# window around each energy peak keeps the whole word regardless of which
# half is louder or how big the pause is.
FRAMES_BEFORE_PEAK = 15  # ~1.2s -- covers a quiet "Hey" well before a loud "Sage"
FRAMES_AFTER_PEAK = 6  # ~0.48s
MIN_PEAK_SEPARATION_FRAMES = 20  # distinct peaks must be at least this far apart

MIN_QUIET_RUN_FRAMES = 30  # ~2.4s of quiet before it counts as an ambience sample
MAX_AMBIENCE_CLIPS = 4


def find_segments(energies: np.ndarray) -> list[tuple[int, int]]:
    floor = float(np.percentile(energies, 20))
    threshold = max(floor * NOISE_MULTIPLIER, MIN_ABS_ENERGY)

    # Distinct local maxima above threshold, at least MIN_PEAK_SEPARATION_FRAMES
    # apart -- picks one point per utterance even across a noisy multi-frame
    # burst, without needing to decide where the "run" of loud frames starts
    # or ends (that's what clipped "Hey" off in the first version).
    candidates = [i for i in range(len(energies)) if energies[i] > threshold]
    peaks: list[int] = []
    for i in candidates:
        if peaks and i - peaks[-1] < MIN_PEAK_SEPARATION_FRAMES:
            if energies[i] > energies[peaks[-1]]:
                peaks[-1] = i
            continue
        peaks.append(i)

    out = []
    for p in peaks:
        s = max(0, p - FRAMES_BEFORE_PEAK)
        e = min(len(energies) - 1, p + FRAMES_AFTER_PEAK)
        out.append((s, e))
    return out


def find_quiet_runs(
    energies: np.ndarray, segments: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    occupied = np.zeros(len(energies), dtype=bool)
    for s, e in segments:
        occupied[s : e + 1] = True

    runs = []
    start = None
    for i, taken in enumerate(occupied):
        if not taken:
            if start is None:
                start = i
        elif start is not None:
            if i - start >= MIN_QUIET_RUN_FRAMES:
                runs.append((start, i - 1))
            start = None
    if start is not None and len(energies) - start >= MIN_QUIET_RUN_FRAMES:
        runs.append((start, len(energies) - 1))
    return runs


SAGE_LIKE = {"sage", "sag", "siege", "stage", "age"}
# "Hey Sage" mis-heard as "Hey, see you." five times in a row in one session
# (same ~1.7s cadence as the ones that transcribed correctly) -- distinct
# from the SAGE_LIKE fallback below because it needs "hey" actually present,
# so it's safe to also match "see"/"see you" without a length cap. "save" and
# "saints" joined it after a follow-up session where a user review of the
# transcripts (not a code change) caught more of the same pattern.
HEY_SAGE_MISHEARD_TAILS = {("see", "you"), ("see",), ("save",), ("saints",)}


def matches_hey_sage(text: str) -> bool:
    words = re.sub(r"[^a-z ]", "", text.lower()).split()
    if not words:
        return False
    if "hey" in words:
        if any(w in SAGE_LIKE for w in words):
            return True
        tail = tuple(words[words.index("hey") + 1 :])
        if tail in HEY_SAGE_MISHEARD_TAILS:
            return True
    # A short "<filler> <sage-word>" utterance with no "hey" at all -- a
    # first pass over a real natural-speech session came back "A sage."
    # transcribed a dozen times in a row for what were unmistakably repeated
    # "Hey Sage" attempts (same ~1.7s duration as the ones that DID
    # transcribe with "hey", same cadence): "Hey" reduced enough in casual
    # speech that Whisper hears it as "a"/"uh", while "Sage" still comes
    # through clearly. Length-capped so it can't also swallow an unrelated
    # longer sentence that happens to end in a similar-sounding word.
    if len(words) <= 2 and words[-1] in SAGE_LIKE:
        return True
    return False


def transcribe(backend, audio: np.ndarray) -> str:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(audio.tobytes())
    result = backend.transcribe(buf.getvalue(), format="wav")
    return result.text.strip()


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(1)
    input_path = Path(sys.argv[1])
    session_id = sys.argv[2]

    data, sr = sf.read(str(input_path), dtype="int16")
    if sr != SAMPLE_RATE:
        raise SystemExit(f"expected {SAMPLE_RATE}Hz, got {sr}Hz")

    step = CHUNK_SAMPLES
    n_frames = len(data) // step
    energies = np.array(
        [np.abs(data[i * step : (i + 1) * step]).mean() for i in range(n_frames)]
    )

    segments = find_segments(energies)
    quiet_runs = find_quiet_runs(energies, segments)[:MAX_AMBIENCE_CLIPS]
    print(f"Found {len(segments)} spoken bursts, {len(quiet_runs)} quiet stretches.\n")

    from openjarvis.speech.faster_whisper import FasterWhisperBackend

    backend = FasterWhisperBackend(
        model_size="distil-large-v3.5", device="cuda", compute_type="float16"
    )

    out_dir = get_data_dir() / "wake_word_samples" / session_id
    pos_dir, neg_dir = out_dir / "positive", out_dir / "negative"
    pos_dir.mkdir(parents=True, exist_ok=True)
    neg_dir.mkdir(parents=True, exist_ok=True)

    existing_pos = len(list(pos_dir.glob("auto_pos_*.wav")))
    existing_neg = len(list(neg_dir.glob("auto_neg_*.wav")))

    n_pos = n_neg = 0
    for s, e in segments:
        clip = data[s * step : (e + 1) * step]
        text = transcribe(backend, clip)
        is_positive = matches_hey_sage(text)
        label = "POSITIVE" if is_positive else "negative"
        print(f'  [{s * FRAME_S:5.2f}s-{e * FRAME_S:5.2f}s] "{text}" -> {label}')
        if is_positive:
            n_pos += 1
            name = f"auto_pos_{existing_pos + n_pos:03d}.wav"
            sf.write(str(pos_dir / name), clip, SAMPLE_RATE, subtype="PCM_16")
        else:
            n_neg += 1
            name = f"auto_neg_{existing_neg + n_neg:03d}.wav"
            sf.write(str(neg_dir / name), clip, SAMPLE_RATE, subtype="PCM_16")

    n_ambience = 0
    for s, e in quiet_runs:
        clip = data[s * step : (e + 1) * step]
        n_ambience += 1
        sf.write(
            str(neg_dir / f"auto_ambience_{existing_neg + n_neg + n_ambience:03d}.wav"),
            clip,
            SAMPLE_RATE,
            subtype="PCM_16",
        )

    print(
        f"\n{n_pos} positive, {n_neg + n_ambience} negative ({n_ambience} ambience) "
        f"clips written to {out_dir}"
    )
    print(
        '\nReview the transcripts above -- if any "Hey Sage" was mislabeled negative '
        f"or vice versa, move the file between {pos_dir} and {neg_dir} by hand "
        "before training."
    )


if __name__ == "__main__":
    main()
