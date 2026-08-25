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

import json
import shutil
from pathlib import Path

from openjarvis.tools.text_to_speech import TextToSpeechTool

GREETINGS = [
    ("hello-sir", "Hello, sir."),
    ("yes-sir", "Yes, sir?"),
    ("sir", "Sir?"),
]

OUT_DIR = Path(__file__).resolve().parent.parent / "frontend" / "public" / "greetings"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tool = TextToSpeechTool()

    manifest = []
    for slug, text in GREETINGS:
        result = tool.execute(text=text, voice_id="", backend="cartesia")
        if not result.success:
            print(f"FAILED {slug!r}: {result.content}")
            return 1
        src = Path(result.metadata["audio_path"])
        dest = OUT_DIR / f"{slug}{src.suffix}"
        shutil.copyfile(src, dest)
        manifest.append(f"greetings/{dest.name}")
        print(f"{text!r} -> {dest} ({dest.stat().st_size} bytes)")

    # The frontend reads this rather than hardcoding filenames, so adding a
    # variant here is the only change needed to put it in rotation.
    manifest_path = OUT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
