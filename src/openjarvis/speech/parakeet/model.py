"""The two ONNX sessions behind Parakeet: a cache-aware encoder and a
decoder+joint network, plus the state each carries between steps.

Derived from Parakeet-ONNX (Apache-2.0, github.com/thomas097/Parakeet-ONNX).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np
from numpy.typing import NDArray

from openjarvis.speech._cuda_dlls import ensure_cuda_dll_dirs
from openjarvis.speech.parakeet.config import ParakeetConfig

# onnxruntime resolves the CUDA runtime by bare LoadLibrary, exactly as
# CTranslate2 does, so the pip-installed DLLs must be on PATH first.
ensure_cuda_dll_dirs()

try:
    import onnxruntime as ort
except ImportError:  # pragma: no cover - exercised by is_available()
    ort = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

CUDA_PROVIDER = "CUDAExecutionProvider"
CPU_PROVIDER = "CPUExecutionProvider"


class ModelLoadError(RuntimeError):
    pass


@dataclass
class EncoderCache:
    """What the encoder remembers between 160 ms chunks."""

    last_channel: NDArray
    last_time: NDArray
    last_channel_len: NDArray

    @classmethod
    def empty(cls, cfg: ParakeetConfig) -> "EncoderCache":
        return cls(
            last_channel=np.zeros(
                (cfg.encoder_layers, 1, cfg.cache_channel_len, cfg.hidden),
                dtype=np.float32,
            ),
            last_time=np.zeros(
                (cfg.encoder_layers, 1, cfg.hidden, cfg.cache_time_len),
                dtype=np.float32,
            ),
            last_channel_len=np.array([0], dtype=np.int64),
        )


@dataclass
class DecoderState:
    """The prediction network's LSTM state and the last emitted token."""

    h: NDArray
    c: NDArray
    last_token: NDArray

    @classmethod
    def initial(cls, cfg: ParakeetConfig, blank_id: int) -> "DecoderState":
        return cls(
            h=np.zeros((1, 1, cfg.decoder_hidden), dtype=np.float32),
            c=np.zeros((1, 1, cfg.decoder_hidden), dtype=np.float32),
            last_token=np.full((1, 1), blank_id, dtype=np.int32),
        )


@dataclass
class OnnxModel:
    encoder: "ort.InferenceSession"
    decoder_joint: "ort.InferenceSession"
    device: str
    providers: Tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def load(
        cls, model_dir: str, *, device: str = "cuda", quant: str = ""
    ) -> "OnnxModel":
        """Open both sessions; ``device`` is what was asked, ``.device`` what
        was actually obtained (CUDA silently absent means CPU, and the
        caller must be able to see that it did)."""
        if ort is None:
            raise ModelLoadError("onnxruntime is not installed")
        suffix = f"_{quant}" if quant else ""
        encoder_path = os.path.join(model_dir, f"encoder{suffix}.onnx")
        decoder_path = os.path.join(model_dir, f"decoder_joint{suffix}.onnx")
        for path in (encoder_path, decoder_path):
            if not os.path.exists(path):
                raise ModelLoadError(
                    f"missing model file {os.path.basename(path)} in {model_dir}"
                )

        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        # The CUDA build warns about the Memcpy nodes the export needs on
        # every load; it is expected, not actionable.
        options.log_severity_level = 3
        # A streaming session runs one small step at a time; extra threads
        # only add contention with the wake-word and STT models beside it.
        options.intra_op_num_threads = max(1, min(4, (os.cpu_count() or 4) // 2))

        providers = [CPU_PROVIDER]
        wanted_cuda = device.lower().startswith("cuda")
        if wanted_cuda:
            available = ort.get_available_providers()
            if CUDA_PROVIDER in available:
                providers.insert(0, CUDA_PROVIDER)
            else:
                logger.warning(
                    "Parakeet: CUDA requested but onnxruntime has no CUDA provider "
                    "(installed providers: %s); using CPU",
                    ", ".join(available),
                )

        encoder = ort.InferenceSession(
            encoder_path, providers=providers, sess_options=options
        )
        decoder = ort.InferenceSession(
            decoder_path, providers=providers, sess_options=options
        )
        got = tuple(encoder.get_providers())
        actual = "cuda" if got and got[0] == CUDA_PROVIDER else "cpu"
        if wanted_cuda and actual != "cuda":
            logger.warning("Parakeet: CUDA provider did not initialise; running on CPU")
        return cls(encoder=encoder, decoder_joint=decoder, device=actual, providers=got)

    def run_encoder(
        self, features: NDArray, length: int, cache: EncoderCache
    ) -> Tuple[NDArray, EncoderCache]:
        """``[1, hidden, T]`` encoder output for ``[1, n_mels, T']`` features."""
        outputs = self.encoder.run(
            None,
            {
                "audio_signal": features.astype(np.float32, copy=False),
                "length": np.asarray([length], dtype=np.int64),
                "cache_last_channel": cache.last_channel,
                "cache_last_time": cache.last_time,
                "cache_last_channel_len": cache.last_channel_len.astype(
                    np.int64, copy=False
                ),
            },
        )
        encoded, _, last_channel, last_time, last_channel_len = outputs
        return encoded, EncoderCache(last_channel, last_time, last_channel_len)

    def run_decoder(
        self, encoder_frame: NDArray, state: DecoderState
    ) -> Tuple[NDArray, NDArray, NDArray]:
        """Joint logits ``[vocab]`` for one encoder frame, plus the new LSTM
        state (only to be kept if a non-blank symbol is accepted)."""
        outputs = self.decoder_joint.run(
            None,
            {
                "encoder_outputs": encoder_frame.astype(np.float32, copy=False),
                "targets": state.last_token.astype(np.int32, copy=False),
                "target_length": np.array([1], dtype=np.int32),
                "input_states_1": state.h.astype(np.float32, copy=False),
                "input_states_2": state.c.astype(np.float32, copy=False),
            },
        )
        logits, _, new_h, new_c = outputs
        # The export returns [1, 1, 1, vocab]; flatten to the vocabulary.
        return np.asarray(logits).reshape(-1), new_h, new_c


def available_reason() -> Optional[str]:
    """Why the engine cannot run at all, or None."""
    if ort is None:
        return "onnxruntime is not installed"
    return None
