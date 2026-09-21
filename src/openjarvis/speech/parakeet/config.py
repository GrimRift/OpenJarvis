"""Fixed properties of ``nvidia/parakeet_realtime_eou_120m-v1``.

These describe the trained model, not tunables: the front end must match
training exactly and the encoder is exported for one chunk shape. The only
knobs Sage exposes are in ``core/config.py`` (device, quantisation, the
end-of-turn timeout backstop).
"""

from __future__ import annotations

from dataclasses import dataclass

MODEL_ID = "nvidia/parakeet_realtime_eou_120m-v1"
ONNX_REPO = "altunenes/parakeet-rs"
ONNX_SUBDIR = "realtime_eou_120m-v1-onnx"
ONNX_FILES = ("encoder.onnx", "decoder_joint.onnx", "tokenizer.json")


@dataclass(frozen=True)
class ParakeetConfig:
    sample_rate: int = 16000
    # The encoder needs at least this much audio before its first frame, and
    # never looks back further than the cache plus this window.
    min_buffer_seconds: float = 0.5
    max_buffer_seconds: float = 8.0
    # Cache-aware streaming export: 16 new encoder frames per step (160 ms
    # of audio at the 10 ms hop after 8x subsampling), 9 frames of pre-encode
    # context carried over.
    pre_encode_cache: int = 9
    frames_per_chunk: int = 16
    samples_per_chunk: int = 2560
    n_fft: int = 512
    win_length: int = 400
    hop_length: int = 160
    n_mels: int = 128
    pre_emphasis: float = 0.97
    log_zero_guard: float = 5.9604645e-8
    fmax: float = 8000.0
    # RNNT: at most this many symbols per encoder frame before moving on.
    max_symbols: int = 3
    # Encoder cache shapes, from the export.
    encoder_layers: int = 17
    cache_channel_len: int = 70
    hidden: int = 512
    cache_time_len: int = 8
    decoder_hidden: int = 640
    # 8x subsampling of the 10 ms hop: one encoder frame per 80 ms of audio.
    frame_ms: int = 80
