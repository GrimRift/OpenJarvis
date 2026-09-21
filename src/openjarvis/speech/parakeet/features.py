"""Log-mel front end for Parakeet, in numpy.

The model was trained on NeMo's ``AudioToMelSpectrogramPreprocessor``
(16 kHz, 512-point FFT, 25 ms Hann window, 10 ms hop, 128 mels, 0.97
pre-emphasis, natural log with a small guard). This reproduces it without
torch, so the STT path adds no new heavyweight dependency to Sage's venv.

Derived from Parakeet-ONNX (Apache-2.0, github.com/thomas097/Parakeet-ONNX).
"""

from __future__ import annotations

import math

import numpy as np
from numpy.lib.stride_tricks import as_strided
from numpy.typing import NDArray
from scipy.fft import rfft

from openjarvis.speech.parakeet.config import ParakeetConfig


class MelFrontEnd:
    def __init__(self, config: ParakeetConfig) -> None:
        self._config = config
        self._mels = _mel_filterbank(config)
        self._window = np.hanning(config.win_length).astype(np.float32)

    def __call__(self, audio: NDArray[np.float32]) -> NDArray[np.float32]:
        """``[1, n_mels, frames]`` log-mel features for a float32 waveform."""
        pre = _preemphasis(audio, self._config.pre_emphasis)
        mel = self._mels @ self._power_spectrum(pre)
        mel_log = np.log(np.maximum(mel, 0.0) + self._config.log_zero_guard)
        return mel_log[np.newaxis, :, :].astype(np.float32, copy=False)

    def _power_spectrum(self, audio: NDArray[np.float32]) -> NDArray[np.float32]:
        cfg = self._config
        pad = cfg.n_fft // 2
        padded = np.pad(audio, (pad, pad))
        frames = 1 + (len(padded) - cfg.win_length) // cfg.hop_length
        stride = padded.strides[0]
        windows = as_strided(
            padded,
            shape=(frames, cfg.win_length),
            strides=(stride * cfg.hop_length, stride),
            writeable=False,
        )
        windowed = windows * self._window
        if cfg.win_length < cfg.n_fft:
            windowed = np.pad(windowed, ((0, 0), (0, cfg.n_fft - cfg.win_length)))
        spec = np.abs(rfft(windowed, axis=1)) ** 2
        return spec.T.astype(np.float32)


def _preemphasis(audio: NDArray[np.float32], coefficient: float) -> NDArray[np.float32]:
    if len(audio) == 0:
        return audio
    out = np.empty_like(audio)
    out[0] = audio[0]
    out[1:] = audio[1:] - coefficient * audio[:-1]
    out[~np.isfinite(out)] = 0.0
    return out


def _mel_filterbank(cfg: ParakeetConfig) -> NDArray[np.float32]:
    def hz_to_mel(hz: float) -> float:
        return 2595.0 * math.log10(1 + hz / 700.0)

    def mel_to_hz(mel: float) -> float:
        return 700.0 * (10 ** (mel / 2595.0) - 1.0)

    mel_min, mel_max = hz_to_mel(0.0), hz_to_mel(cfg.fmax)
    points = [
        mel_to_hz(mel_min + (mel_max - mel_min) * i / (cfg.n_mels + 1))
        for i in range(cfg.n_mels + 2)
    ]
    bins = cfg.n_fft // 2 + 1
    freqs = [(cfg.sample_rate / cfg.n_fft) * i for i in range(bins)]
    weights = np.zeros((cfg.n_mels, bins), dtype=np.float32)
    for i in range(cfg.n_mels):
        left, centre, right = points[i], points[i + 1], points[i + 2]
        for j, freq in enumerate(freqs):
            if left <= freq <= centre:
                weights[i, j] = (freq - left) / (centre - left)
            elif centre < freq <= right:
                weights[i, j] = (right - freq) / (right - centre)
        weights[i, :] *= 2.0 / (right - left)
    return weights
