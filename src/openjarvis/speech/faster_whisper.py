"""Faster-Whisper speech-to-text backend (local, CTranslate2-based)."""

from __future__ import annotations

import logging
import os
import tempfile
from typing import List, Optional

from openjarvis.core.registry import SpeechRegistry
from openjarvis.speech._stubs import Segment, SpeechBackend, TranscriptionResult

# CTranslate2 (faster-whisper's backend) needs CUDA's cuBLAS/cuDNN DLLs to
# use device="cuda" — there's no system-wide CUDA Toolkit here, only the
# pip-installed nvidia-cublas-cu12/nvidia-cudnn-cu12 runtime packages, whose
# DLLs live under site-packages rather than anywhere Windows searches by
# default. os.add_dll_directory() alone does NOT fix this: CTranslate2's
# native code resolves cublas64_12.dll via a bare LoadLibrary call, which
# only consults the real PATH env var, not Python's DLL-directory registry
# (confirmed by trial — add_dll_directory left it 500ing with "Library
# cublas64_12.dll is not found", prepending PATH fixed it outright). Must
# run before ctranslate2/faster_whisper import below, at process startup.
if os.name == "nt":
    try:
        import nvidia.cublas
        import nvidia.cudnn

        _dll_dirs = []
        for _mod in (nvidia.cublas, nvidia.cudnn):
            # These are PEP 420 namespace packages (no __file__), so the
            # package directory comes from __path__ instead.
            _pkg_dir = next(iter(_mod.__path__), None)
            if not _pkg_dir:
                continue
            _bin_dir = os.path.join(_pkg_dir, "bin")
            if os.path.isdir(_bin_dir):
                _dll_dirs.append(_bin_dir)
                os.add_dll_directory(_bin_dir)  # covers Python-level extension loading too
        if _dll_dirs:
            os.environ["PATH"] = os.pathsep.join(_dll_dirs) + os.pathsep + os.environ.get("PATH", "")
    except ImportError:
        pass

try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None  # type: ignore[assignment, misc]

try:
    import ctranslate2
except ImportError:
    ctranslate2 = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


@SpeechRegistry.register("faster-whisper")
class FasterWhisperBackend(SpeechBackend):
    """Local speech-to-text using Faster-Whisper (CTranslate2)."""

    backend_id = "faster-whisper"

    def __init__(
        self,
        model_size: str = "base",
        device: str = "auto",
        compute_type: str = "float16",
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._model: Optional[WhisperModel] = None
        self._last_error: Optional[str] = None

    def _resolve_compute_type(self) -> str:
        """Pick a CTranslate2 compute type supported by the configured device."""
        if ctranslate2 is None:
            return self._compute_type

        try:
            supported = set(ctranslate2.get_supported_compute_types(self._device))
        except Exception as exc:
            logger.debug(
                "Could not inspect CTranslate2 compute types for %s: %s",
                self._device,
                exc,
            )
            return self._compute_type

        if self._compute_type in supported:
            return self._compute_type

        preferences = (
            ("int8", "float32", "int8_float32", "int16")
            if self._compute_type == "float16"
            else ("float32", "int8", "int8_float32", "int16")
        )
        fallback = next((value for value in preferences if value in supported), None)
        if fallback is None:
            return self._compute_type

        logger.warning(
            "CTranslate2 compute_type=%r is not supported on device=%r; "
            "using %r instead",
            self._compute_type,
            self._device,
            fallback,
        )
        return fallback

    def _ensure_model(self) -> WhisperModel:
        """Lazy-load the Whisper model on first use."""
        if self._model is None:
            if WhisperModel is None:
                self._last_error = (
                    "faster-whisper is not installed. "
                    "Install with: uv sync --extra desktop"
                )
                raise ImportError(self._last_error)
            compute_type = self._resolve_compute_type()
            self._model = WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=compute_type,
            )
        self._last_error = None
        return self._model

    def transcribe(
        self,
        audio: bytes,
        *,
        format: str = "wav",
        language: Optional[str] = None,
        initial_prompt: Optional[str] = None,
    ) -> TranscriptionResult:
        """Transcribe audio bytes using Faster-Whisper."""
        try:
            model = self._ensure_model()

            # Write audio to a temp file (faster-whisper needs a file path).
            # delete=False + manual unlink: on Windows an open
            # NamedTemporaryFile holds an exclusive handle, so PyAV's reopen
            # of tmp.name inside model.transcribe() fails with EACCES.
            suffix = f".{format}" if not format.startswith(".") else format
            tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            try:
                with tmp:
                    tmp.write(audio)

                # vad_filter=True runs Silero VAD first and skips non-speech
                # regions entirely. Without it, Whisper is well known to
                # hallucinate boilerplate text ("Thank you.", etc.) from
                # silence/background noise instead of returning empty —
                # which matters here because auto-triggered recordings
                # (wake word, continuous conversation) often capture mostly
                # silence, and a hallucinated non-empty transcript gets sent
                # as a real message, triggering a voice reply that re-arms
                # continuous conversation and sustains an unwanted loop.
                #
                # vad_filter alone wasn't enough in practice: a live case
                # captured only ambient noise (no wake-word-triggered
                # recording ever has real speech in it if the trigger itself
                # was a false positive) and Whisper still confidently
                # hallucinated a full fake exchange ("How are you? I'm
                # good.") from it — Silero's speech-probability gate judged
                # the noise "speech-like" enough to pass through.
                # min_speech_duration_ms rejects brief noise blips outright
                # before Whisper ever sees them. Tried also tightening
                # no_speech_threshold/log_prob_threshold below their
                # faster-whisper defaults (0.6 / -1.0) for extra margin, but
                # that rejected real manual speech-to-text too — reverted,
                # not worth the false-negative cost for a rare hallucination.
                kwargs = {
                    "vad_filter": True,
                    "vad_parameters": {"min_speech_duration_ms": 250},
                }
                if language:
                    kwargs["language"] = language
                if initial_prompt:
                    kwargs["initial_prompt"] = initial_prompt

                segments_iter, info = model.transcribe(tmp.name, **kwargs)
                segments_list = list(segments_iter)
            finally:
                try:
                    os.unlink(tmp.name)
                except OSError as unlink_exc:
                    logger.debug(
                        "Could not remove temp audio file %s: %s",
                        tmp.name,
                        unlink_exc,
                    )
        except Exception as exc:
            self._last_error = str(exc)
            raise

        # Build result
        text = "".join(seg.text for seg in segments_list).strip()
        segments = [
            Segment(
                text=seg.text.strip(),
                start=seg.start,
                end=seg.end,
                confidence=None,
            )
            for seg in segments_list
        ]

        self._last_error = None
        return TranscriptionResult(
            text=text,
            language=getattr(info, "language", None),
            confidence=getattr(info, "language_probability", None),
            duration_seconds=getattr(info, "duration", 0.0),
            segments=segments,
        )

    def health(self) -> bool:
        """Check if model is loaded or loadable."""
        try:
            self._ensure_model()
            return True
        except Exception as exc:
            self._last_error = str(exc)
            logger.debug("Faster-Whisper health check failed: %s", exc)
            return False

    def last_error(self) -> Optional[str]:
        """Return the last model load or transcription error, if any."""
        return self._last_error

    def supported_formats(self) -> List[str]:
        """Supported audio formats (same as ffmpeg/Whisper)."""
        return ["wav", "mp3", "m4a", "ogg", "flac", "webm"]
