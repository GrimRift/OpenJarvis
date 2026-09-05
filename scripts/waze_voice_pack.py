r"""Render the Waze custom-voice prompt pack in Sage's voice, and play it back
one clip at a time while Waze records.

Waze has no import: its recorder captures through the phone microphone, so the
only way to get Sage onto turn-by-turn is to play these clips out loud next to
the phone and let Waze listen. That is a one-off session of ~40 short takes,
after which every turn instruction is spoken by Sage.

The prompt list is NOT guessed. Waze's recorder shows the exact phrases it
wants, and they must match, so the list lives in OPENJARVIS_DATA as
waze_prompts.json and is transcribed from the app.

    .venv\Scripts\python.exe scripts\waze_voice_pack.py generate
    .venv\Scripts\python.exe scripts\waze_voice_pack.py play

Clips are WAV rather than MP3 because they are played through local speakers,
never shipped to the phone -- and WAV is what the trimmer and the player read
without an ffmpeg dependency.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
import soundfile as sf

from openjarvis.speech.spoken_text import to_spoken_text
from openjarvis.speech.voice_profiles import JARVIS

# Cartesia leaves a little room at the head and tail of every clip. Waze caps
# each recording, and that budget should go to speech rather than silence.
SILENCE_FLOOR = 0.01
KEEP_PADDING_SECONDS = 0.05


def _data_dir() -> Path:
    return Path(os.environ.get("OPENJARVIS_DATA", r"C:\AI\OpenJarvis-Data"))


def _prompts_path() -> Path:
    return _data_dir() / "waze_prompts.json"


def _clip_dir() -> Path:
    return _data_dir() / "voice-clips" / "waze"


def _load_prompts() -> list[dict]:
    path = _prompts_path()
    if not path.exists():
        raise SystemExit(
            f"No prompt list at {path}.\n"
            "Transcribe it from Waze: Settings > Sound & voice > Voice "
            "directions > Record new voice. The phrases must match the app "
            "exactly, so do not invent them."
        )
    prompts = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(prompts, list) or not prompts:
        raise SystemExit(f"{path} should hold a non-empty list of prompts.")
    return prompts


def _trim(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    mono = audio if audio.ndim == 1 else audio.mean(axis=1)
    loud = np.flatnonzero(np.abs(mono) > SILENCE_FLOOR)
    if loud.size == 0:
        return audio
    pad = int(KEEP_PADDING_SECONDS * sample_rate)
    start = max(0, int(loud[0]) - pad)
    end = min(len(mono), int(loud[-1]) + pad)
    return audio[start:end]


def generate(only: str | None) -> int:
    from openjarvis.speech.cartesia_tts import CartesiaTTSBackend

    prompts = _load_prompts()
    out_dir = _clip_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    backend = CartesiaTTSBackend()

    expected = {f"{i:02d}-{p['id']}.wav" for i, p in enumerate(prompts, start=1)}
    if not only:
        # Dropping or reordering a prompt renumbers everything after it. Stale
        # clips would still sit in the directory and pair the player's prompts
        # against the wrong audio, so they go before anything is written.
        keep = {out_dir / name for name in expected}
        for stale in sorted(set(out_dir.glob("*.wav")) - keep):
            stale.unlink()
            print(f"removed stale {stale.name}")

    for index, prompt in enumerate(prompts, start=1):
        slug = prompt["id"]
        if only and only != slug:
            continue
        # Waze concatenates these fragments, so several are spoken with a
        # trailing comma to stop each one landing like a finished sentence.
        # "text" stays as Waze displays it, so the operator can match the list.
        text = prompt["text"]
        result = backend.synthesize(
            to_spoken_text(prompt.get("speak") or text),
            voice_id=JARVIS.voice_id,
            speed=JARVIS.speed,
            volume=JARVIS.volume,
            output_format="wav",
        )
        if not result.audio:
            print(f"FAILED {slug!r}: no audio returned")
            return 1
        dest = out_dir / f"{index:02d}-{slug}.wav"
        dest.write_bytes(result.audio)
        audio, sample_rate = sf.read(dest)
        trimmed = _trim(audio, sample_rate)
        sf.write(dest, trimmed, sample_rate)
        seconds = len(trimmed) / sample_rate
        print(f"{index:02d} {text!r} -> {dest.name} ({seconds:.1f}s)")
    return 0


# Waze rejects a pack larger than this, counting every mp3 together.
PACK_BYTE_BUDGET = 800_000
# Cartesia's default mp3 runs ~140 kbps, which puts 39 clips over that cap and
# would force the uploader to re-encode Sage's audio a second time. 64 kbps is
# the highest rate Cartesia offers that fits (48 kbps is rejected by the API).
PACK_BIT_RATE = 64_000


def package() -> int:
    """Render the pack under the filenames Waze's uploader expects."""
    from openjarvis.speech.cartesia_tts import CartesiaTTSBackend

    prompts = _load_prompts()
    out_dir = _data_dir() / "voice-clips" / "waze-pack"
    out_dir.mkdir(parents=True, exist_ok=True)
    backend = CartesiaTTSBackend()

    keep = {p["waze_file"] for p in prompts}
    for stale in sorted(out_dir.glob("*.mp3")):
        if stale.name not in keep:
            stale.unlink()
            print(f"removed stale {stale.name}")

    total = 0
    for prompt in prompts:
        result = backend.synthesize(
            to_spoken_text(prompt.get("speak") or prompt["text"]),
            voice_id=JARVIS.voice_id,
            speed=JARVIS.speed,
            volume=JARVIS.volume,
            output_format="mp3",
            bit_rate=PACK_BIT_RATE,
        )
        if not result.audio:
            print(f"FAILED {prompt['id']!r}: no audio returned")
            return 1
        dest = out_dir / prompt["waze_file"]
        dest.write_bytes(result.audio)
        total += len(result.audio)
        print(f"{dest.name:<28} {prompt['text']!r}")

    print()
    budget_kb = PACK_BYTE_BUDGET / 1000
    print(f"{len(prompts)} files, {total / 1000:.0f} kB of {budget_kb:.0f} kB")
    if total > PACK_BYTE_BUDGET:
        print("OVER BUDGET -- the pack needs re-encoding at a lower bitrate.")
        return 1
    return 0


def play(start_at: int) -> int:
    import sounddevice as sd

    prompts = _load_prompts()
    clips = sorted(_clip_dir().glob("*.wav"))
    if len(clips) != len(prompts):
        print(
            f"{len(clips)} clips for {len(prompts)} prompts -- run generate first."
        )
        return 1

    print(
        "\nFor each prompt: tap record in Waze, press Enter here, then stop and "
        "save in Waze.\n"
        "  Enter = play    r = replay    s = skip    q = quit\n"
    )
    index = max(1, start_at)
    while index <= len(prompts):
        clip = clips[index - 1]
        text = prompts[index - 1]["text"]
        choice = input(f"[{index}/{len(prompts)}] {text!r} > ").strip().casefold()
        if choice == "q":
            break
        if choice == "s":
            index += 1
            continue
        audio, sample_rate = sf.read(clip)
        sd.play(audio, sample_rate)
        sd.wait()
        if choice != "r":
            index += 1
    print("\nAnything you skipped stays in Waze's default voice.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate", help="render every prompt in Sage's voice")
    gen.add_argument("--only", help="regenerate a single prompt id")
    playback = sub.add_parser("play", help="play clips one at a time for Waze")
    playback.add_argument("--start-at", type=int, default=1)
    sub.add_parser("package", help="render mp3s under Waze's upload filenames")
    args = parser.parse_args()
    if args.command == "generate":
        return generate(args.only)
    if args.command == "package":
        return package()
    return play(args.start_at)


if __name__ == "__main__":
    raise SystemExit(main())
