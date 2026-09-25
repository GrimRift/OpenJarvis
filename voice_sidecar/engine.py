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
#: The two Chatterbox models the engine can speak with. Nano is GPT-2 small;
#: Turbo is GPT-2 medium over the same vocoder and voice encoder, and on 24
#: September it carried a cloned voice's accent where Nano pulled it toward
#: its own.
MODELS = ("nano", "turbo")
DEFAULT_MODEL = "nano"
# Share of the card the allocator may hold: 0.3 of 8 GB is 2.4 GB, room for
# Nano's fp32 weights plus one reply's activations, never the whole card.
# Turbo's measured peak was 3.0 GB with the stock loop (24 September), plus
# the graphed loop's caches.
MEMORY_FRACTIONS = {"nano": 0.3, "turbo": 0.55}
#: Turbo's own files; its vocoder and voice encoder are byte-identical to
#: Nano's (same sha256), so those are linked rather than downloaded twice.
TURBO_REPO = "ResembleAI/chatterbox-turbo"
NANO_REPO = "ResembleAI/chatterbox-nano"
TURBO_OWN_FILES = (
    "t3_turbo_v1.safetensors",
    "added_tokens.json",
    "merges.txt",
    "special_tokens_map.json",
    "tokenizer_config.json",
    "vocab.json",
    "conds.pt",
)
TURBO_SHARED_FILES = ("s3gen_meanflow.safetensors", "ve.safetensors")
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
#: Which model a voice belongs to and the name shown for it. Kept apart from
#: voice.json, which is the sampling and is rewritten whole by save_params.
META_FILE = "meta.json"
#: How the orb reads a voice: level = min(1, (RMS x scale x gain) ** contrast).
#: Turbo speaks more smoothly than Nano -- shallower dips between syllables,
#: 25% less of the 3-8 Hz pulse the eye reads as talking (measured 24
#: September) -- and its orb "moved too little". Each voice's pair is fitted
#: so its orb moves as Nano Jarvis's does; neutral changes nothing.
#: release and kick: how fast the speaking orb draws in after a syllable and
#: how hard an onset lights it -- a smooth voice's orb held one size
#: (25 September).
ORB_NEUTRAL = {"gain": 1.0, "contrast": 1.0, "release": 0.035, "kick": 1.0}


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
    engine: str = DEFAULT_MODEL
    label: str = ""
    orb: Dict[str, float] = field(default_factory=lambda: dict(ORB_NEUTRAL))


def _orb_shaping(raw: Any) -> Dict[str, float]:
    shaping = dict(ORB_NEUTRAL)
    if isinstance(raw, dict):
        for key, low, high in (
            ("gain", 0.5, 3.0),
            ("contrast", 0.5, 3.0),
            ("release", 0.02, 0.2),
            ("kick", 0.5, 3.0),
        ):
            try:
                shaping[key] = min(high, max(low, float(raw.get(key, shaping[key]))))
            except (TypeError, ValueError):
                pass
    return shaping


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

    def meta(self, name: str) -> Dict[str, str]:
        path = self.path(name) / META_FILE
        data: Dict[str, Any] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                logger.warning("unreadable %s; treating it as a Nano voice", path)
        engine = str(data.get("engine") or DEFAULT_MODEL)
        return {
            "engine": engine if engine in MODELS else DEFAULT_MODEL,
            "label": str(data.get("label") or ""),
            "orb": _orb_shaping(data.get("orb")),
        }

    def engine(self, name: str) -> str:
        return str(self.meta(name)["engine"])

    def save_meta(
        self, name: str, *, engine: Optional[str] = None, label: Optional[str] = None
    ) -> None:
        meta = self.meta(name)
        if engine is not None:
            if engine not in MODELS:
                raise ValueError(f"unknown engine {engine!r}")
            meta["engine"] = engine
        if label is not None:
            meta["label"] = label.strip()[:60]
        self.path(name).mkdir(parents=True, exist_ok=True)
        (self.path(name) / META_FILE).write_text(json.dumps(meta), encoding="utf-8")

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
            **self.meta(name),
        )

    def store_reference(
        self, name: str, source: Path, engine: Optional[str] = None
    ) -> Path:
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
        if engine is not None:
            self.save_meta(name, engine=engine)
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
        self,
        device: str,
        voices: VoiceStore,
        precision: str = "fp32",
        model: str = DEFAULT_MODEL,
    ) -> None:
        self.precision = precision if precision in ("fp16", "fp32") else "fp32"
        self.requested_device = device
        self.device = device
        self.voices = voices
        self.kind = model if model in MODELS else DEFAULT_MODEL
        self.model = None
        self.current_voice: Optional[str] = None
        self.lock = threading.Lock()
        self.load_seconds = 0.0
        self.last_generation_seconds = 0.0
        self.generations = 0
        # Seconds the last switch between models took, for /health.
        self.switch_seconds = 0.0
        # The graphed speech-token loop (fast_t3.py); None means stock.
        self.fast_t3 = None
        self._ve = None
        self._ve_lock = threading.Lock()
        self._janitor: Optional[threading.Thread] = None

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
            torch.cuda.set_per_process_memory_fraction(MEMORY_FRACTIONS[self.kind])
        if self.kind == "turbo":
            self.model = ChatterboxTurboTTS.from_local(
                turbo_checkpoint_dir(), device=self.device, nano=False
            )
        else:
            self.model = ChatterboxTurboTTS.from_pretrained(
                device=self.device, nano=True
            )
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
        if self.fast_t3 is not None and self._janitor is None:
            self._janitor = threading.Thread(
                target=self._release_idle_caches, name="cache-janitor", daemon=True
            )
            self._janitor.start()
        logger.info(
            "Chatterbox %s loaded on %s in %.1fs",
            self.kind.title(),
            self.device,
            self.load_seconds,
        )

    def _release_idle_caches(self) -> None:
        """Every 20 s, free the long-sentence caches nobody has used for a
        minute (fast_t3.release_idle) -- only when no piece is generating:
        the lock is tried, never waited on."""
        while True:
            time.sleep(20)
            self._release_idle_once()

    def _release_idle_once(self) -> None:
        # A pass of its own, so nothing of it outlives the pass: a local for
        # the graphs in the loop slept beside it, and held the old model's
        # speech-token half and its graph pools on the card through a
        # Turbo-to-Nano switch, which then ran out of memory (25 September).
        if not self.lock.acquire(blocking=False):
            return
        try:
            graphed = self.fast_t3
            if graphed is not None and graphed.release_idle():
                import torch

                torch.cuda.empty_cache()
                logger.info("released idle speech-token caches")
        except Exception:
            logger.debug("cache release failed", exc_info=True)
        finally:
            self.lock.release()

    def gpu_memory(self) -> Dict[str, Any]:
        """What this process holds on the card, for /health."""
        if not self.device.startswith("cuda"):
            return {}
        import torch

        buckets = sorted(self.fast_t3.buckets) if self.fast_t3 is not None else []
        return {
            "allocated_mb": round(torch.cuda.memory_allocated() / 2**20),
            "reserved_mb": round(torch.cuda.memory_reserved() / 2**20),
            "cache_buckets": buckets,
        }

    def switch(self, kind: str) -> None:
        """Put model *kind* on the card in place of the current one.

        One at a time, by the user's choice (24 September): both resident
        would hold ~5.5 GB of an 8 GB card. The switch is paid once, when a
        voice of the other model is chosen; generation waits on the lock
        while the models change.
        """
        if kind not in MODELS:
            raise ValueError(f"unknown engine {kind!r}")
        if kind == self.kind and self.model is not None:
            return
        import gc

        started = time.monotonic()
        with self.lock:
            if kind == self.kind and self.model is not None:
                return
            self.model = None
            self.fast_t3 = None
            with self._ve_lock:
                self._ve = None
            self.current_voice = None
            gc.collect()
            if self.device.startswith("cuda"):
                import torch

                torch.cuda.empty_cache()
            self.kind = kind
            try:
                self.load()
            except Exception as exc:
                # Left half-loaded, every later request retried the switch
                # and failed the same way. A fresh process starts on the
                # chosen voice's model, with the card to itself.
                if "out of memory" in str(exc).lower():
                    logger.critical("switch to %s ran out of memory; exiting", kind)
                    os._exit(3)
                raise
        self.switch_seconds = round(time.monotonic() - started, 1)
        logger.info("switched to %s in %.1fs", kind.title(), self.switch_seconds)

    @property
    def loaded(self) -> bool:
        return self.model is not None

    def use_voice(self, name: str) -> None:
        """Make *name* the voice ``generate`` speaks with, loading its model
        if the other one is on the card and computing its conditioning if
        needed. Cheap when already current."""
        wanted = self.voices.engine(name)
        if wanted != self.kind or self.model is None:
            self.switch(wanted)
            self._use_conditioning(name)
            self.warm_up(name)
            return
        self._use_conditioning(name)

    def _use_conditioning(self, name: str) -> None:
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
        with self.lock:
            prepared = self._prepare_conditionals_cpu(ref)
            prepared.save(conds)
            self.model.conds = self._cast_conds(prepared.to(self.device))
        self.current_voice = folder.name
        logger.info(
            "conditioning for %s computed in %.1fs", name, time.monotonic() - started
        )

    def prepare_voice(self, name: str) -> None:
        """Compute and cache *name*'s conditioning without making it current
        or loading its model: conditioning is the same for Nano and Turbo,
        so a voice uploaded for the model not on the card is ready when it
        is chosen."""
        if self.model is None:
            raise RuntimeError("model not loaded")
        folder = self.voices.path(name)
        ref = folder / REFERENCE_FILE
        if not ref.exists():
            raise FileNotFoundError(f"voice {name!r} has no reference recording")
        with self.lock:
            self._prepare_conditionals_cpu(ref).save(folder / CONDS_FILE)
        if self.current_voice == folder.name:
            self.current_voice = None

    def _prepare_conditionals_cpu(self, ref: Path):
        """``prepare_conditionals`` with every step on the CPU.

        It used to move the speech tokenizer, speaker encoder and voice
        encoder (0.5 GB) onto the card for the call, and on 24 September,
        with the card near the process's memory cap, three of four uploads
        failed with CUDA out of memory. On the CPU it takes 0.6-2.7 s and
        the result matched the GPU's: identical speech tokens, embeddings
        within float rounding (1e-4). The steps are the library's own, in
        its order. Conditioning does not depend on the model: Nano and
        Turbo share the tokenizer and both encoders.
        """
        import librosa
        import torch
        from chatterbox.tts_turbo import S3_SR, S3GEN_SR, Conditionals, T3Cond

        model = self.model
        wav, _ = librosa.load(str(ref), sr=S3GEN_SR)
        wav = model.norm_loudness(wav, S3GEN_SR)
        ref_16k = librosa.resample(wav, orig_sr=S3GEN_SR, target_sr=S3_SR)
        modules = self._conditioning_module_list()
        homes = [next(m.parameters()).device for m in modules]
        for module in modules:
            module.to("cpu")
        try:
            gen = model.s3gen.embed_ref(
                wav[: model.DEC_COND_LEN], S3GEN_SR, device="cpu"
            )
            tokens, _ = model.s3gen.tokenizer.forward(
                [ref_16k[: model.ENC_COND_LEN]],
                max_len=model.t3.hp.speech_cond_prompt_len,
            )
            ve = torch.from_numpy(
                model.ve.embeds_from_wavs([ref_16k], sample_rate=S3_SR)
            ).mean(axis=0, keepdim=True)
        finally:
            for module, home in zip(modules, homes):
                module.to(home)
        t3 = T3Cond(
            speaker_emb=ve,
            cond_prompt_speech_tokens=torch.atleast_2d(tokens),
            emotion_adv=0.5 * torch.ones(1, 1, 1),
        )
        return Conditionals(t3, gen)

    # The speech tokenizer (472 MB), speaker encoder and voice encoder are
    # used only to turn a reference recording into conditioning, which is
    # cached per voice. Resident on the card they were 30% of the weights
    # for something that runs once per uploaded voice, so they live on the
    # CPU, where the conditioning is computed.
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
            s3gen_cls.device = property(lambda s3: next(s3.flow.parameters()).device)
            s3gen_cls._sage_device_from_flow = True
        for module in self._conditioning_module_list():
            module.to("cpu")
        torch.cuda.empty_cache()

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
                try:
                    audio = self._synthesize(text, sampling, vocode_runaway=last)
                except Exception as exc:
                    _exit_if_cuda_poisoned(exc)
                    raise
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
        except Exception as exc:
            _exit_if_cuda_poisoned(exc)
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

    # ------------------------------------------------------ who is talking

    def _speaker_encoder(self):
        """A CPU copy of the model's voice encoder, taken once.

        Its own copy because the model's is moved to the card and back by
        `use_voice`, and a fingerprint asked for mid-move would run on the
        wrong device. On the CPU it is 12 ms for two seconds of speech
        (measured 24 September), so it never waits for generation's lock.
        """
        if self._ve is None:
            with self._ve_lock:
                if self._ve is None:
                    if self.model is None:
                        raise RuntimeError("model not loaded")
                    import copy

                    ve = copy.deepcopy(self.model.ve).to("cpu").float()
                    ve.eval()
                    self._ve = ve
        return self._ve

    def speaker_embedding(self, samples: np.ndarray, sample_rate: int) -> List[float]:
        """The voice fingerprint of *samples* (mono float), L2-normalised."""
        ve = self._speaker_encoder()
        with self._ve_lock:
            embedding = ve.embeds_from_wavs(
                [np.asarray(samples, dtype=np.float32)],
                sample_rate,
                as_spk=True,
                trim_top_db=None,
            )
        return [float(x) for x in np.asarray(embedding).reshape(-1)]

    def voice_embedding(self, name: str) -> List[float]:
        """The fingerprint of a voice's reference recording: what Sage sounds
        like when it speaks with that voice."""
        import librosa

        ref = self.voices.path(name) / REFERENCE_FILE
        if not ref.exists():
            raise FileNotFoundError(f"voice {name!r} has no reference recording")
        samples, rate = librosa.load(str(ref), sr=16000, mono=True)
        return self.speaker_embedding(samples, rate)

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
    # A code read out letter by letter ("C E I T G D eleven D") takes ~0.4 s a
    # character, not 0.09: at the word rate an order number ran past the
    # budget three times and the line was cut mid-word (25 September).
    spelled = sum(len(t) for t in _SPELLED.findall(text))
    return int(SAMPLE_RATE * ((len(text) - spelled) * 0.09 + spelled * 0.4 + 1.0))


#: Tokens a voice spells rather than says: letters and digits mixed, or
#: runs of capitals ("7SX", "CEITGD11D", "NU").
_SPELLED = __import__("re").compile(
    r"\b(?:(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{2,}|[A-Z]{2,})\b"
)


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


#: What a CUDA error that leaves the process's GPU context unusable says.
_POISONED = ("device-side assert", "illegal memory access", "cudaErrorAssert")


def _exit_if_cuda_poisoned(exc: BaseException) -> None:
    """End the process after a CUDA error the context cannot recover from.

    Every later GPU call fails the same way -- the stock-loop fallback
    included -- so on 24 September the sidecar stayed up and silent for 20
    minutes: greetings (static files) played, replies did not. Exiting lets
    the server start a fresh one on the next reply.
    """
    if any(mark in str(exc) for mark in _POISONED):
        logger.critical(
            "CUDA context lost (%s); exiting so a fresh sidecar starts", exc
        )
        logging.shutdown()
        os._exit(70)


def turbo_checkpoint_dir() -> Path:
    """Turbo's checkpoint folder, completed from what is already on disk.

    Turbo's own files come from its repository (downloaded once, 1.9 GB for
    the text model); the vocoder and voice encoder are Nano's, linked in,
    since both repositories hold the same bytes and a second copy is 1 GB.
    """
    from huggingface_hub import hf_hub_download

    folder = None
    for name in TURBO_OWN_FILES:
        folder = Path(hf_hub_download(TURBO_REPO, name)).parent
    for name in TURBO_SHARED_FILES:
        target = folder / name
        if target.exists():
            continue
        source = Path(os.path.realpath(hf_hub_download(NANO_REPO, name)))
        try:
            os.link(source, target)
        except OSError:
            shutil.copyfile(source, target)
    return folder


def default_voices_dir() -> Path:
    data = os.environ.get("OPENJARVIS_DATA") or r"C:\AI\OpenJarvis-Data"
    return Path(data) / "voices"
