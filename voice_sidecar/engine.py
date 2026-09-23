"""The loaded Chatterbox model and the voices it can speak with."""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger("voice_sidecar")

SAMPLE_RATE = 24000
# Share of the card the allocator may hold: 0.3 of 8 GB is 2.4 GB, room for
# fp32 weights plus one reply's activations, never the whole card.
MEMORY_FRACTION = 0.3
# How many fresh samples a runaway generation gets before it is cut.
RUNAWAY_RETRIES = 2
# The library's own sampling, which a runaway is retried with: it never ran
# away in the 22 September bench.
LIBRARY_SAMPLING = dict(temperature=0.8, top_k=1000, top_p=0.95, repetition_penalty=1.2)
# Cache lengths captured at warm-up, so the first real line does not pay for
# a capture: they hold every piece up to ~110 characters.
WARM_BUCKETS = (512, 640, 768)
REFERENCE_FILE = "reference.wav"
CONDS_FILE = "conds.pt"
PARAMS_FILE = "voice.json"


# Generation controls Chatterbox Turbo/Nano actually honour. (cfg_weight,
# exaggeration and min_p are accepted by generate() and ignored by these
# models -- the library says so at runtime -- so they are not offered.)
#
# Measured 22 September: pushing these toward "calm" (temperature 0.5,
# top_k 300, repetition_penalty 1.3) made a two-sentence input run to the
# 1000-token cap -- 31 s of audio for 108 characters -- because the
# end-of-speech token stopped being sampled. A later bench (24 clips per
# setting, transcribed back) found temperature 0.55 alone repeats a phrase
# in 4 of 24 while 0.7 repeats in 0 of 24, and the steadier short-text
# sampling (0.45) that briefly existed produced today's two runaways on
# 29- and 37-character lines. Lower temperature is where the repeats come
# from, so the defaults stay; steadiness comes from sentence-sized
# segments, the level control below, and the reference recording, which
# carries the character.
@dataclass
class VoiceParams:
    temperature: float = 0.7
    top_p: float = 0.95
    top_k: int = 1000
    repetition_penalty: float = 1.2
    # Output level, RMS on the float scale. Chatterbox normalises the
    # reference, not what it generates, and a voice that drifts in volume
    # between sentences is the first thing a listener notices. 0.16 is
    # about -16 dBFS: 0.09 left moments and reminders (whose volumes were
    # tuned against Cartesia's hotter clips) nearly inaudible.
    target_rms: float = 0.16
    peak_limit: float = 0.97

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VoiceParams":
        params = cls()
        for key, value in (data or {}).items():
            if hasattr(params, key):
                try:
                    setattr(params, key, type(getattr(params, key))(value))
                except (TypeError, ValueError):
                    pass
        params.temperature = min(1.5, max(0.05, params.temperature))
        params.top_p = min(1.0, max(0.1, params.top_p))
        params.top_k = min(5000, max(1, params.top_k))
        params.repetition_penalty = min(3.0, max(1.0, params.repetition_penalty))
        params.target_rms = min(0.3, max(0.0, params.target_rms))
        return params


@dataclass
class VoiceInfo:
    name: str
    has_reference: bool
    has_conditioning: bool
    reference_seconds: float
    params: Dict[str, Any] = field(default_factory=dict)


class VoiceStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> Path:
        safe = "".join(c for c in name.lower() if c.isalnum() or c in "-_") or "voice"
        return self.root / safe

    def names(self) -> List[str]:
        return sorted(
            p.name for p in self.root.iterdir() if (p / REFERENCE_FILE).exists()
        )

    def params(self, name: str) -> VoiceParams:
        path = self.path(name) / PARAMS_FILE
        if path.exists():
            try:
                return VoiceParams.from_dict(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            except (OSError, ValueError):
                logger.warning("unreadable %s; using defaults", path)
        return VoiceParams()

    def save_params(self, name: str, params: VoiceParams) -> None:
        self.path(name).mkdir(parents=True, exist_ok=True)
        (self.path(name) / PARAMS_FILE).write_text(
            json.dumps(asdict(params), indent=2), encoding="utf-8"
        )

    def info(self, name: str) -> VoiceInfo:
        folder = self.path(name)
        ref = folder / REFERENCE_FILE
        seconds = 0.0
        if ref.exists():
            try:
                import soundfile as sf

                seconds = float(sf.info(str(ref)).duration)
            except Exception:
                seconds = 0.0
        return VoiceInfo(
            name=folder.name,
            has_reference=ref.exists(),
            has_conditioning=(folder / CONDS_FILE).exists(),
            reference_seconds=round(seconds, 1),
            params=asdict(self.params(name)),
        )

    def store_reference(self, name: str, source: Path) -> Path:
        """Convert any audio file to the mono 24 kHz WAV Chatterbox reads,
        and drop stale conditioning so it is recomputed."""
        import librosa
        import soundfile as sf

        folder = self.path(name)
        folder.mkdir(parents=True, exist_ok=True)
        audio, _ = librosa.load(str(source), sr=SAMPLE_RATE, mono=True)
        if len(audio) / SAMPLE_RATE < 5.0:
            raise ValueError("the reference must be longer than 5 seconds")
        target = folder / REFERENCE_FILE
        sf.write(str(target), audio, SAMPLE_RATE, subtype="PCM_16")
        conds = folder / CONDS_FILE
        if conds.exists():
            conds.unlink()
        return target

    def remove(self, name: str) -> bool:
        folder = self.path(name)
        if not folder.exists():
            return False
        shutil.rmtree(folder)
        return True


class ChatterboxEngine:
    """One model, one lock: the GPU serialises generation anyway, and a
    second reply must not interleave its sentences with the first."""

    def __init__(
        self, device: str, voices: VoiceStore, precision: str = "fp32"
    ) -> None:
        self.precision = precision if precision in ("fp16", "fp32") else "fp32"
        self.requested_device = device
        self.device = device
        self.voices = voices
        self.model = None
        self.current_voice: Optional[str] = None
        self.lock = threading.Lock()
        self.load_seconds = 0.0
        self.last_generation_seconds = 0.0
        self.generations = 0
        # The graphed speech-token loop (fast_t3.py); None means stock.
        self.fast_t3 = None

    # ------------------------------------------------------------ loading

    def load(self) -> None:
        import torch
        from chatterbox.tts_turbo import ChatterboxTurboTTS

        started = time.monotonic()
        if self.device.startswith("cuda") and not torch.cuda.is_available():
            logger.warning(
                "CUDA requested but torch.cuda.is_available() is False; using CPU"
            )
            self.device = "cpu"
        if self.device.startswith("cuda"):
            # Measured 22 September: the process held 3.25 GB on an 8 GB card
            # with 1.7 GB of weights, the rest being the caching allocator
            # keeping every reply's peak activations. A hard fraction makes
            # the allocator free its cache before growing, so the process
            # stays near weights + context.
            torch.cuda.set_per_process_memory_fraction(MEMORY_FRACTION)
        self.model = ChatterboxTurboTTS.from_pretrained(device=self.device, nano=True)
        if self.precision == "fp16" and self.device.startswith("cuda"):
            # Halves the resident weights, and the voice with them: compared
            # by ear on 22 September the half-precision vocoder sounded
            # metallic on every line ("alien" was the word used). Kept as an
            # opt-in for cards that cannot hold fp32; not the default.
            self.model.t3.half()
            self.model.s3gen.half()
        self._park_conditioning_modules()
        if self.device.startswith("cuda") and self.precision == "fp32":
            from .fast_t3 import GraphedT3

            self.fast_t3 = GraphedT3(self.model.t3, self.device)
        self.load_seconds = round(time.monotonic() - started, 1)
        logger.info(
            "Chatterbox Nano loaded on %s in %.1fs", self.device, self.load_seconds
        )

    @property
    def loaded(self) -> bool:
        return self.model is not None

    def use_voice(self, name: str) -> None:
        """Make *name* the voice ``generate`` speaks with, computing and
        caching its conditioning if needed. Cheap when already current."""
        if self.model is None:
            raise RuntimeError("model not loaded")
        if name == self.current_voice:
            return
        from chatterbox.tts_turbo import Conditionals

        folder = self.voices.path(name)
        ref = folder / REFERENCE_FILE
        if not ref.exists():
            raise FileNotFoundError(f"voice {name!r} has no reference recording")
        conds = folder / CONDS_FILE
        if conds.exists():
            try:
                self.model.conds = self._cast_conds(
                    Conditionals.load(conds, map_location=self.device)
                )
                self.current_voice = folder.name
                return
            except Exception:
                logger.warning(
                    "cached conditioning for %s unreadable; recomputing", name
                )
        started = time.monotonic()
        with self.lock, self._conditioning_modules_on_device():
            self.model.prepare_conditionals(str(ref))
            self.model.conds.save(conds)
            self.model.conds = self._cast_conds(self.model.conds)
        self.current_voice = folder.name
        logger.info(
            "conditioning for %s computed in %.1fs", name, time.monotonic() - started
        )

    # The speech tokenizer (472 MB), speaker encoder and voice encoder are
    # used only to turn a reference recording into conditioning, which is
    # cached per voice. Resident on the card they were 30% of the weights
    # for something that runs once per uploaded voice, so they live on the
    # CPU and visit the GPU for that one call.
    _CONDITIONING_MODULES = (
        ("s3gen", "tokenizer"),
        ("s3gen", "speaker_encoder"),
        ("", "ve"),
    )

    def _conditioning_module_list(self):
        found = []
        for owner, name in self._CONDITIONING_MODULES:
            parent = getattr(self.model, owner) if owner else self.model
            module = getattr(parent, name, None)
            if module is not None and hasattr(module, "to"):
                found.append(module)
        return found

    def _park_conditioning_modules(self) -> None:
        if not self.device.startswith("cuda"):
            return
        import torch

        s3gen_cls = type(self.model.s3gen)
        # S3Token2Wav.device reads the tokenizer's parameters, which would
        # report "cpu" once parked and put the flow's noise there; the flow
        # is what generation runs, so read the device from it instead.
        if not getattr(s3gen_cls, "_sage_device_from_flow", False):
            s3gen_cls.device = property(
                lambda s3: next(s3.flow.parameters()).device
            )
            s3gen_cls._sage_device_from_flow = True
        for module in self._conditioning_module_list():
            module.to("cpu")
        torch.cuda.empty_cache()

    def _conditioning_modules_on_device(self):
        import contextlib

        if not self.device.startswith("cuda"):
            return contextlib.nullcontext()

        @contextlib.contextmanager
        def visit():
            import torch

            modules = self._conditioning_module_list()
            for module in modules:
                module.to(self.device)
            try:
                yield
            finally:
                for module in modules:
                    module.to("cpu")
                torch.cuda.empty_cache()

        return visit()

    def _cast_conds(self, conds):
        if self.precision != "fp16" or not self.device.startswith("cuda"):
            return conds
        import torch

        for key, value in list(conds.gen.items()):
            if torch.is_tensor(value) and value.is_floating_point():
                conds.gen[key] = value.half()
        return conds

    def _autocast(self):
        import contextlib

        import torch

        if self.precision == "fp16" and self.device.startswith("cuda"):
            return torch.autocast("cuda", dtype=torch.float16)
        return contextlib.nullcontext()

    # --------------------------------------------------------- synthesis

    def generate(self, text: str, voice: str) -> np.ndarray:
        """Float32 mono at 24 kHz for one utterance. Blocking."""
        import torch

        text = text.strip()
        if not text:
            return np.zeros(0, dtype=np.float32)
        self.use_voice(voice)
        params = self.voices.params(voice)
        started = time.monotonic()
        sampling = dict(
            temperature=params.temperature,
            top_p=params.top_p,
            top_k=params.top_k,
            repetition_penalty=params.repetition_penalty,
        )
        audio = None
        for attempt in range(RUNAWAY_RETRIES + 1):
            last = attempt == RUNAWAY_RETRIES
            with self.lock, torch.inference_mode(), self._autocast():
                audio = self._synthesize(text, sampling, vocode_runaway=last)
            if audio is not None and not _ran_away(audio, text):
                break
            if last:
                break
            # A phrase said twice is the failure the user hears ("repeats
            # words"); another sample at the library defaults, which never
            # ran away in the bench, is the fix. The cut is a last resort.
            logger.warning(
                "runaway generation (%s for %d chars); retry %d",
                "over budget" if audio is None else f"{len(audio) / SAMPLE_RATE:.1f}s",
                len(text),
                attempt + 1,
            )
            sampling = dict(LIBRARY_SAMPLING)
        if _ran_away(audio, text):
            audio = audio[: _budget_samples(text)]
        self.last_generation_seconds = round(time.monotonic() - started, 3)
        self.generations += 1
        # Hand the allocator's cache back after every piece: the activations
        # of a long sentence otherwise stay reserved for the process life.
        if self.device.startswith("cuda"):
            torch.cuda.empty_cache()
        return _level(audio, params)

    def _synthesize(
        self, text: str, sampling: Dict[str, Any], *, vocode_runaway: bool
    ) -> Optional[np.ndarray]:
        """One take of *text*. None when the speech tokens ran over the
        length budget and *vocode_runaway* is off: turning a runaway into
        audio only to discard it cost seconds, and ran the card out of memory
        on 23 September."""
        if self.fast_t3 is None:
            wav = self.model.generate(text, **sampling)
            return wav.squeeze(0).detach().float().cpu().numpy().astype(np.float32)
        from .fast_t3 import PieceTooLong

        try:
            return self._synthesize_graphed(text, sampling, vocode_runaway)
        except PieceTooLong:
            wav = self.model.generate(text, **sampling)
            return wav.squeeze(0).detach().float().cpu().numpy().astype(np.float32)
        except Exception:
            logger.warning(
                "graphed speech tokens failed; using the stock loop from now on",
                exc_info=True,
            )
            self.fast_t3 = None
            wav = self.model.generate(text, **sampling)
            return wav.squeeze(0).detach().float().cpu().numpy().astype(np.float32)

    def _synthesize_graphed(
        self, text: str, sampling: Dict[str, Any], vocode_runaway: bool
    ) -> Optional[np.ndarray]:
        """``ChatterboxTurboTTS.generate`` with its speech-token loop
        replaced by fast_t3's; every other step is the library's own."""
        import torch
        from chatterbox.tts_turbo import S3GEN_SIL, punc_norm

        from .fast_t3 import token_budget

        model = self.model
        normed = punc_norm(text)
        text_tokens = model.tokenizer(
            normed, return_tensors="pt", padding=True, truncation=True
        ).input_ids.to(self.device)
        budget = token_budget(_budget_samples(text) / SAMPLE_RATE)
        speech_tokens, stopped = self.fast_t3.generate(
            model.conds.t3, text_tokens, max_tokens=budget, **sampling
        )
        if not stopped and not vocode_runaway:
            return None
        speech_tokens = speech_tokens[speech_tokens < 6561].to(self.device)
        silence = torch.tensor([S3GEN_SIL, S3GEN_SIL, S3GEN_SIL]).long().to(self.device)
        speech_tokens = torch.cat([speech_tokens, silence])
        wav, _ = model.s3gen.inference(
            speech_tokens=speech_tokens, ref_dict=model.conds.gen, n_cfm_timesteps=2
        )
        wav = wav.squeeze(0).detach().cpu().numpy()
        wav = model.watermarker.apply_watermark(wav, sample_rate=model.sr)
        return np.asarray(wav, dtype=np.float32).reshape(-1)

    def warm_up(self, voice: str) -> None:
        try:
            self.generate("Ready, sir.", voice)
        except Exception:
            logger.warning("warm-up generation failed", exc_info=True)
        if self.fast_t3 is not None:
            try:
                import torch

                with self.lock, torch.inference_mode():
                    self.fast_t3.prepare(WARM_BUCKETS)
            except Exception:
                logger.warning("graph warm-up failed", exc_info=True)


def _budget_samples(text: str) -> int:
    """Ceiling on how long *text* can take to say.

    Measured 22 September over 48 clean clips (10-107 characters): every
    one fit 75 ms per character plus 0.4 s. The old 110 ms + 2 s let a
    37-character line said twice (6.6 s) through as normal; this catches
    it with room for a slow, pause-heavy delivery.
    """
    return int(SAMPLE_RATE * (len(text) * 0.09 + 1.0))


def _ran_away(audio: np.ndarray, text: str) -> bool:
    return len(audio) > _budget_samples(text)


def _level(audio: np.ndarray, params: VoiceParams) -> np.ndarray:
    """Bring a segment to the voice's target level without clipping."""
    if audio.size == 0 or params.target_rms <= 0:
        return audio
    rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    if rms <= 1e-6:
        return audio
    gain = params.target_rms / rms
    # Never more than 4x: a near-silent fragment must not become noise.
    scaled = audio.astype(np.float64) * min(gain, 4.0)
    # Soft knee above `knee` rather than pulling the whole clip down for a
    # few peaks: speech has a high crest factor, and a hard peak limit left
    # the voice 6 dB under its target (measured -18 dBFS for a -16 target).
    knee = 0.85
    over = np.abs(scaled) > knee
    if over.any():
        excess = (np.abs(scaled[over]) - knee) / (1.0 - knee)
        scaled[over] = np.sign(scaled[over]) * (knee + (1.0 - knee) * np.tanh(excess))
    return np.clip(scaled, -params.peak_limit, params.peak_limit).astype(np.float32)


def default_voices_dir() -> Path:
    data = os.environ.get("OPENJARVIS_DATA") or r"C:\AI\OpenJarvis-Data"
    return Path(data) / "voices"
