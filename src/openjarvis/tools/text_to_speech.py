"""Text-to-speech tool — synthesize text to audio via configurable TTS backend."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any

from openjarvis.core.registry import ToolRegistry, TTSRegistry
from openjarvis.core.types import ToolResult
from openjarvis.speech.spoken_text import to_spoken_text
from openjarvis.tools._stubs import BaseTool, ToolSpec

#: Clips older than this are gone in every sense that matters: the token that
#: addressed them lives in an in-memory map that dies with the server, so
#: nothing can still be playing one from a previous day.
_CLIP_MAX_AGE_SECONDS = 24 * 60 * 60


def _clip_dir() -> Path:
    """One directory for generated clips, rather than one per clip.

    ``tempfile.mkdtemp`` per synthesis left a directory behind every time:
    377 of them had accumulated over sixteen days here, and once empty they
    could not be removed at all -- rmtree and PowerShell both got
    "Access is denied" on directories the user owned. Files inside a
    directory we keep are deletable; directories Windows has finished with
    are not always. So the clutter is not created in the first place.
    """
    directory = Path(tempfile.gettempdir()) / "jarvis-tts"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _prune_old_clips(directory: Path) -> None:
    """Delete clips older than a day.

    Best-effort: a file that will not delete is left alone rather than
    failing a synthesis the user is waiting on. Deliberately not silent
    about nothing happening -- an earlier version wrapped the whole sweep in
    ``ignore_errors=True`` and removed almost nothing while appearing to
    work.
    """
    import time

    cutoff = time.time() - _CLIP_MAX_AGE_SECONDS
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            continue


@ToolRegistry.register("text_to_speech")
class TextToSpeechTool(BaseTool):
    """Synthesize text into spoken audio using a TTS backend."""

    tool_id = "text_to_speech"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="text_to_speech",
            description=(
                "Convert text to spoken audio. Returns the file path to the "
                "generated audio file."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The text to synthesize into speech.",
                    },
                    "voice_id": {
                        "type": "string",
                        "description": "Voice identifier for the TTS backend.",
                    },
                    "backend": {
                        "type": "string",
                        "description": "TTS backend (cartesia, kokoro, openai_tts).",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Directory to save the audio file.",
                    },
                    "speed": {"type": "number"},
                    "volume": {"type": "number"},
                    "emotion": {"type": "string"},
                },
                "required": ["text"],
            },
            category="audio",
            timeout_seconds=120.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        # Ensure TTS backends are registered
        import openjarvis.speech  # noqa: F401

        text = params.get("text", "")
        voice_id = params.get("voice_id", "")
        backend_key = params.get("backend", "cartesia")
        _ALIASES = {"openai": "openai_tts"}
        backend_key = _ALIASES.get(backend_key, backend_key)
        output_dir = params.get("output_dir", "")
        speed = float(params.get("speed", 1.0))
        volume = float(params.get("volume", 1.0))
        emotion = str(params.get("emotion", ""))

        if not text:
            return ToolResult(
                tool_name="text_to_speech",
                content="No text provided.",
                success=False,
            )

        if not TTSRegistry.contains(backend_key):
            return ToolResult(
                tool_name="text_to_speech",
                content=f"TTS backend '{backend_key}' not available.",
                success=False,
            )

        backend_cls = TTSRegistry.get(backend_key)
        backend = backend_cls()

        synthesis_kwargs = {
            "voice_id": voice_id,
            "speed": speed,
            "volume": volume,
        }
        if emotion:
            synthesis_kwargs["emotion"] = emotion
        # This is a derived, audio-only rendering. The caller's full text and
        # any stored chat/tool data remain untouched.
        spoken_text = to_spoken_text(text)
        result = backend.synthesize(spoken_text, **synthesis_kwargs)

        # Save to file
        ext = result.format or "mp3"
        if output_dir:
            out_dir = Path(output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            audio_path = out_dir / f"digest.{ext}"
        else:
            out_dir = _clip_dir()
            _prune_old_clips(out_dir)
            audio_path = out_dir / f"clip-{uuid.uuid4().hex}.{ext}"
        result.save(audio_path)

        return ToolResult(
            tool_name="text_to_speech",
            content=str(audio_path),
            success=True,
            metadata={
                "audio_path": str(audio_path),
                "format": ext,
                "duration_seconds": result.duration_seconds,
                "voice_id": result.voice_id,
                "backend": backend_key,
            },
        )
