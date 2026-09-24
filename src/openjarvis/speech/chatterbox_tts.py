"""Chatterbox Nano (local, voice-cloned) as a Sage TTS backend.

Two entry points, matching what Cartesia offers:

* ``ChatterboxTTSBackend`` -- the registry backend: one utterance in, a WAV
  out. Used by greetings, moments, the Waze pack, the digest and the batch
  fallback.
* ``ChatterboxContext`` -- the streaming context ``server/tts_stream_routes``
  drives, with the same surface as ``CartesiaTTSContext`` (``send_text``,
  ``finish``, ``cancel``, ``receive_audio``, ``flushes``), so the route can
  hold either and the browser hears the same ``pcm_f32le`` at 24 kHz.

Both talk to the sidecar process (``chatterbox_sidecar``) over localhost;
the model itself never loads into the server.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import urllib.error
import urllib.request
from typing import Any, AsyncIterator, Dict, List, Optional

from openjarvis.core.registry import TTSRegistry
from openjarvis.speech import chatterbox_sidecar as sidecar
from openjarvis.speech.tts import TTSBackend, TTSResult

logger = logging.getLogger(__name__)

STREAM_SAMPLE_RATE = 24000
STREAM_ENCODING = "pcm_f32le"
VOICE_ID_PREFIX = "chatterbox:"
DEFAULT_VOICE = "jarvis"

# How long a live reply may wait for a sidecar that is loading. Longer than
# this and the browser's batch fallback is the better experience.
# 25 s was not enough for a cold start after a reboot (disk cache empty),
# and the batch fallback behind it then waits again from scratch.
STREAM_START_WAIT_SECONDS = 45.0


def voice_name(voice_id: str, fallback: str = DEFAULT_VOICE) -> str:
    """``chatterbox:jarvis`` -> ``jarvis``; a Cartesia UUID -> the fallback."""
    value = (voice_id or "").strip()
    if value.startswith(VOICE_ID_PREFIX):
        return value[len(VOICE_ID_PREFIX) :] or fallback
    if value and "-" not in value and len(value) < 40:
        return value
    return fallback


def is_chatterbox_voice_id(voice_id: str) -> bool:
    return (voice_id or "").startswith(VOICE_ID_PREFIX)


def status(speech_cfg: Any) -> Dict[str, Any]:
    """Capability report for Settings and the Health page."""
    reason = sidecar.install_reason(speech_cfg)
    health = None if reason else sidecar.fetch_health(speech_cfg)
    proc = sidecar.process()
    wanted_voice = str(
        getattr(speech_cfg, "chatterbox_voice", DEFAULT_VOICE) or DEFAULT_VOICE
    )
    if reason:
        return {"available": False, "reason": reason, "sidecar_running": False}
    if not health:
        return {
            "available": True,
            "sidecar_running": proc.running(),
            "model_loaded": False,
            "reason": proc.last_error
            or "voice sidecar not running (starts on first use)",
            "voice": wanted_voice,
            "voices": _voice_names_from_disk(),
            "device": None,
        }
    voices = list(health.get("voices") or [])
    entry = {
        "available": True,
        "sidecar_running": True,
        "model_loaded": bool(health.get("model_loaded")),
        "device": health.get("device"),
        "requested_device": health.get("requested_device"),
        "voice": health.get("voice") or wanted_voice,
        "voice_ready": bool(health.get("voice_ready")),
        "voices": voices,
        "load_seconds": health.get("load_seconds"),
        "last_generation_seconds": health.get("last_generation_seconds"),
    }
    if not entry["voice_ready"]:
        entry["reason"] = f"voice {entry['voice']!r} has no reference recording yet"
    return entry


def _voice_names_from_disk() -> List[str]:
    root = sidecar.voices_dir()
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "reference.wav").exists())


def _http_json(
    url: str,
    *,
    method: str = "GET",
    body: Optional[bytes] = None,
    timeout: float = 10.0,
    content_type: str = "application/json",
) -> Any:
    request = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        request.add_header("Content-Type", content_type)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        kind = response.headers.get("Content-Type", "")
        return json.loads(raw.decode("utf-8")) if "json" in kind else raw


@TTSRegistry.register("chatterbox")
class ChatterboxTTSBackend(TTSBackend):
    backend_id = "chatterbox"

    def __init__(self, speech_cfg: Any = None) -> None:
        self._cfg = speech_cfg if speech_cfg is not None else _config_speech()

    def _voice(self, voice_id: str) -> str:
        configured = str(
            getattr(self._cfg, "chatterbox_voice", DEFAULT_VOICE) or DEFAULT_VOICE
        )
        return voice_name(voice_id, configured)

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str = "",
        speed: float = 1.0,
        volume: float = 1.0,
        output_format: str = "wav",
        **_: Any,
    ) -> TTSResult:
        """Whole-utterance WAV from the sidecar, starting it if need be.

        ``speed`` has no equivalent in Chatterbox and is ignored; ``volume``
        is left to the player, as the sidecar already levels its output.
        ``output_format`` other than wav is honoured only when ffmpeg is
        present (see ``_transcode``).
        """
        health = sidecar.process().ensure_started(
            self._cfg, wait=sidecar.START_TIMEOUT_SECONDS
        )
        if not health:
            raise RuntimeError(
                sidecar.process().last_error or "voice sidecar unavailable"
            )
        voice = self._voice(voice_id)
        payload = json.dumps({"text": text, "voice": voice}).encode("utf-8")
        try:
            audio = _http_json(
                sidecar.base_url(self._cfg) + "/synthesize",
                method="POST",
                body=payload,
                timeout=120.0,
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            raise RuntimeError(
                f"voice sidecar refused synthesis: {exc.code} {detail}"
            ) from exc
        if not isinstance(audio, (bytes, bytearray)):
            raise RuntimeError("voice sidecar returned no audio")
        fmt = "wav"
        if output_format and output_format != "wav":
            audio, fmt = _transcode(bytes(audio), output_format)
        return TTSResult(
            audio=bytes(audio),
            format=fmt,
            duration_seconds=_wav_seconds(bytes(audio)) if fmt == "wav" else 0.0,
            voice_id=VOICE_ID_PREFIX + voice,
            sample_rate=STREAM_SAMPLE_RATE,
            metadata={"backend": "chatterbox", "voice": voice},
        )

    def available_voices(self) -> List[str]:
        health = sidecar.fetch_health(self._cfg)
        names = list(health.get("voices") or []) if health else _voice_names_from_disk()
        return [VOICE_ID_PREFIX + n for n in names]

    def health(self) -> bool:
        return bool(status(self._cfg).get("model_loaded"))


def _config_speech() -> Any:
    try:
        from openjarvis.core.config import load_config

        return load_config().speech
    except Exception:
        return None


def _wav_seconds(data: bytes) -> float:
    try:
        import wave

        with wave.open(io.BytesIO(data)) as w:
            return round(w.getnframes() / float(w.getframerate() or 1), 3)
    except Exception:
        return 0.0


def _transcode(wav: bytes, output_format: str) -> tuple[bytes, str]:
    """WAV -> mp3 via ffmpeg for the callers that need it (Waze). Without
    ffmpeg the WAV is returned as is and the caller sees ``format='wav'``."""
    import shutil
    import subprocess

    exe = shutil.which("ffmpeg")
    if not exe:
        return wav, "wav"
    try:
        result = subprocess.run(
            [exe, "-loglevel", "error", "-i", "pipe:0", "-f", output_format, "pipe:1"],
            input=wav,
            capture_output=True,
            check=True,
            timeout=60,
        )
        return result.stdout, output_format
    except Exception:
        logger.warning("ffmpeg transcode to %s failed; returning wav", output_format)
        return wav, "wav"


def list_voices(speech_cfg: Any) -> Dict[str, Any]:
    """Voices with their metadata, from the sidecar when it is up."""
    health = sidecar.fetch_health(speech_cfg)
    if health:
        try:
            return _http_json(sidecar.base_url(speech_cfg) + "/voices", timeout=5.0)
        except Exception:
            logger.debug("sidecar /voices failed", exc_info=True)
    # The sidecar is not up (typically still loading after a boot): answer
    # from disk, with the same fields, so Settings shows the voice rather
    # than "?" while it starts.
    root = sidecar.voices_dir()
    voices = []
    for name in _voice_names_from_disk():
        seconds = 0.0
        try:
            import wave

            with wave.open(str(root / name / "reference.wav")) as w:
                seconds = round(w.getnframes() / float(w.getframerate() or 1), 1)
        except Exception:
            pass
        voices.append(
            {
                "name": name,
                "has_reference": True,
                "has_conditioning": (root / name / "conds.pt").exists(),
                "reference_seconds": seconds,
                **voice_meta(name),
            }
        )
    return {
        "default": str(
            getattr(speech_cfg, "chatterbox_voice", DEFAULT_VOICE) or DEFAULT_VOICE
        ),
        "current": None,
        "sidecar_starting": sidecar.process().running(),
        "voices": voices,
    }


def voice_meta(name: str) -> Dict[str, str]:
    """A voice's model and display name (voice_sidecar/engine.py META_FILE),
    read from disk: the Settings list needs them while the sidecar loads."""
    engine, label, orb = "nano", "", dict(ORB_NEUTRAL)
    try:
        data = json.loads(
            (sidecar.voices_dir() / name / "meta.json").read_text(encoding="utf-8")
        )
        engine = str(data.get("engine") or "nano")
        label = str(data.get("label") or "")
        raw = data.get("orb")
        if isinstance(raw, dict):
            for key in orb:
                orb[key] = min(3.0, max(0.5, float(raw.get(key, orb[key]))))
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return {
        "engine": engine if engine in ("nano", "turbo") else "nano",
        "label": label,
        "orb": orb,
    }


#: The orb reads a voice as min(1, (RMS x scale x gain) ** contrast); see
#: voice_sidecar/engine.py ORB_NEUTRAL for why voices differ.
ORB_NEUTRAL = {"gain": 1.0, "contrast": 1.0}


def orb_shaping(speech_cfg: Any = None) -> Dict[str, float]:
    """The chosen voice's orb shaping: server-spoken lines (moments,
    reminders) move the orb as that voice's chat replies do."""
    from openjarvis.speech.voice_choice import chosen_voice_id

    try:
        voice = chosen_voice_id(speech_cfg)
    except Exception:
        return dict(ORB_NEUTRAL)
    if not voice.startswith("chatterbox:"):
        return dict(ORB_NEUTRAL)
    return voice_meta(voice.split(":", 1)[1])["orb"]


def prepare_engine(speech_cfg: Any, voice: str) -> Optional[Dict[str, Any]]:
    """Have the sidecar load *voice*'s model now (a switch takes 6-13 s),
    so the first reply in a newly chosen voice does not wait for it."""
    if not sidecar.fetch_health(speech_cfg):
        return None
    try:
        return _http_json(
            f"{sidecar.base_url(speech_cfg)}/engine/prepare",
            method="POST",
            body=json.dumps({"voice": voice}).encode("utf-8"),
            timeout=120.0,
            content_type="application/json",
        )
    except Exception:
        logger.debug("sidecar engine prepare failed", exc_info=True)
        return None


def upload_voice(
    speech_cfg: Any, name: str, filename: str, data: bytes, engine: str = ""
) -> Dict[str, Any]:
    """Hand a reference recording to the sidecar, which converts it, stores
    it under the voices directory for model *engine* (nano or turbo; the
    loaded one when empty) and computes its conditioning."""
    health = sidecar.process().ensure_started(
        speech_cfg, wait=sidecar.START_TIMEOUT_SECONDS
    )
    if not health:
        raise RuntimeError(sidecar.process().last_error or "voice sidecar unavailable")
    boundary = "----SageVoiceUpload"
    safe_name = (filename or "reference.wav").replace('"', "")
    crlf = "\r\n"
    head = (
        f"--{boundary}{crlf}"
        f'Content-Disposition: form-data; name="file"; filename="{safe_name}"{crlf}'
        f"Content-Type: application/octet-stream{crlf}{crlf}"
    )
    tail = f"{crlf}--{boundary}--{crlf}"
    body = head.encode("utf-8") + data + tail.encode("utf-8")
    try:
        return _http_json(
            f"{sidecar.base_url(speech_cfg)}/voices/{name}"
            + (f"?engine={engine}" if engine else ""),
            method="POST",
            body=body,
            timeout=180.0,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"voice sidecar refused the recording: {detail}") from exc


def delete_voice(speech_cfg: Any, name: str) -> Dict[str, Any]:
    if sidecar.fetch_health(speech_cfg):
        try:
            return _http_json(
                f"{sidecar.base_url(speech_cfg)}/voices/{name}",
                method="DELETE",
                timeout=10.0,
            )
        except Exception:
            logger.debug("sidecar delete failed; removing on disk", exc_info=True)
    import shutil

    folder = sidecar.voices_dir() / name
    if folder.exists():
        shutil.rmtree(folder)
        return {"removed": True}
    return {"removed": False}


class ChatterboxContext:
    """One reply's streaming session with the sidecar.

    Same contract as ``CartesiaTTSContext``: text goes in as it is written,
    audio comes out as each segment is synthesised, ``flushes`` counts the
    segments fully spoken, and ``cancel`` guarantees nothing more is heard.
    """

    def __init__(self, speech_cfg: Any, voice_id: str, *, context_id: str = "") -> None:
        self._cfg = speech_cfg
        self._voice = voice_name(
            voice_id,
            str(
                getattr(speech_cfg, "chatterbox_voice", DEFAULT_VOICE) or DEFAULT_VOICE
            ),
        )
        self._context_id = context_id or "chatterbox"
        self._socket: Any = None
        self._generation = 0
        self._flushes = 0
        self._done = False
        self._cancelled = False
        self._finished = False
        self.used_warm = False

    @property
    def context_id(self) -> str:
        return self._context_id

    @property
    def flushes(self) -> int:
        return self._flushes

    @property
    def voice(self) -> str:
        return self._voice

    async def __aenter__(self) -> "ChatterboxContext":
        import websockets

        health = await asyncio.to_thread(
            sidecar.process().ensure_started, self._cfg, wait=STREAM_START_WAIT_SECONDS
        )
        if not health:
            raise RuntimeError(
                sidecar.process().last_error or "voice sidecar unavailable"
            )
        self._socket = await websockets.connect(
            sidecar.ws_url(self._cfg), open_timeout=5, max_size=32 * 1024 * 1024
        )
        await self._socket.send(json.dumps({"type": "begin", "voice": self._voice}))
        ready = json.loads(await asyncio.wait_for(self._socket.recv(), timeout=30))
        if ready.get("type") != "ready":
            raise RuntimeError(f"voice sidecar: {ready.get('reason') or ready}")
        self._generation = int(ready.get("generation") or 0)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        socket, self._socket = self._socket, None
        if socket is not None:
            try:
                await socket.close()
            except Exception:
                pass

    async def send_text(self, text: str) -> None:
        if self._socket is None or self._finished or self._cancelled:
            raise RuntimeError("Chatterbox context is closed")
        await self._socket.send(json.dumps({"type": "text", "text": text}))

    async def finish(self) -> None:
        if self._socket is None or self._finished or self._cancelled:
            return
        self._finished = True
        await self._socket.send(json.dumps({"type": "finish"}))

    async def cancel(self) -> None:
        if self._socket is None or self._cancelled:
            return
        self._cancelled = True
        try:
            await self._socket.send(json.dumps({"type": "cancel"}))
        except Exception:
            pass

    async def receive_audio(self) -> AsyncIterator[bytes]:
        """Yield pcm_f32le chunks until the reply is done or cancelled."""
        if self._socket is None:
            raise RuntimeError("Chatterbox context is not open")
        async for raw in self._socket:
            if isinstance(raw, (bytes, bytearray)):
                if not self._cancelled:
                    yield bytes(raw)
                continue
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            kind = message.get("type")
            if kind == "segment_done":
                # The sidecar says short segments together with the next
                # one and acknowledges them in one message; counting that
                # as one left the other "unspoken", and the route re-sent
                # it -- a sentence said twice.
                self._flushes += max(1, int(message.get("segments") or 1))
            elif kind == "done":
                self._done = True
                return
            elif kind == "cancelled":
                self._cancelled = True
                return
            elif kind == "error":
                if message.get("fatal"):
                    raise RuntimeError(f"voice sidecar: {message.get('reason')}")
                logger.warning(
                    "voice sidecar segment failed: %s", message.get("reason")
                )


__all__ = [
    "delete_voice",
    "list_voices",
    "upload_voice",
    "STREAM_ENCODING",
    "STREAM_SAMPLE_RATE",
    "VOICE_ID_PREFIX",
    "ChatterboxContext",
    "ChatterboxTTSBackend",
    "is_chatterbox_voice_id",
    "status",
    "voice_name",
]
