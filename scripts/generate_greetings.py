"""Pre-render the wake-word acknowledgement clips in Sage's own voice.

The wake word should be answered instantly, so these are rendered once and
served as static files rather than synthesized per trigger — a live TTS call
costs a network round trip at exactly the moment the user is waiting to hear
that they were heard.

Several variants exist so repeated triggers don't replay one identical clip;
useWakeWord picks one at random. Keep them short: this plays while the user
may already be mid-sentence, and a long clip is more to talk over.

Run after changing GREETINGS (regenerates all of them):

    .venv\\Scripts\\python.exe scripts\\generate_greetings.py
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from openjarvis.speech.voice_profiles import DEFAULT_VOICE, VOICE_PROFILES
from openjarvis.tools.text_to_speech import TextToSpeechTool

GREETINGS = [
    ("hello-sir", "Hello, sir."),
    ("yes-sir", "Yes, sir."),
    ("sir", "Sir."),
]

DELIVERY = {
    "jarvis": {"emotion": "content", "version": "sonic36-content"},
    "frieren": {"emotion": "content", "version": "sonic36-content"},
}

OUT_DIR = Path(__file__).resolve().parent.parent / "frontend" / "public" / "greetings"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--voice",
        choices=["all", *(profile.name.casefold() for profile in VOICE_PROFILES)],
        default="all",
    )
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tool = TextToSpeechTool()

    manifest_path = OUT_DIR / "manifest.json"
    if args.voice != "all" and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {
            "default_voice_id": DEFAULT_VOICE.voice_id,
            "voices": {},
        }
    voices = manifest["voices"]
    assert isinstance(voices, dict)
    for profile in VOICE_PROFILES:
        profile_key = profile.name.casefold()
        if args.voice != "all" and args.voice != profile_key:
            continue
        delivery = DELIVERY[profile_key]
        profile_clips = []
        voice_dir = OUT_DIR / profile_key
        voice_dir.mkdir(parents=True, exist_ok=True)
        for slug, text in GREETINGS:
            result = tool.execute(
                text=text,
                voice_id=profile.voice_id,
                backend="cartesia",
                speed=profile.speed,
                volume=profile.volume,
                emotion=delivery["emotion"],
            )
            if not result.success:
                print(f"FAILED {profile.name} {slug!r}: {result.content}")
                return 1
            src = Path(result.metadata["audio_path"])
            dest = voice_dir / f"{slug}-{delivery['version']}{src.suffix}"
            shutil.copyfile(src, dest)
            profile_clips.append(f"greetings/{profile_key}/{dest.name}")
            print(f"{profile.name} {text!r} -> {dest} ({dest.stat().st_size} bytes)")
        voices[profile.voice_id] = profile_clips

    # The frontend reads this rather than hardcoding filenames, so adding a
    # variant here is the only change needed to put it in rotation.
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
