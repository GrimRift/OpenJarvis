"""Tests for the voice checks.

Both cases pinned here shipped wrong the first time and were caught by running
the check rather than reading it. Both reported a working voice pipeline as
broken, which is worse than not checking at all.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from openjarvis.core.health import _check_flux_streaming, _check_speech_device


def _config(**speech):
    return SimpleNamespace(speech=SimpleNamespace(**speech))


class TestSpeechDevice:
    def test_cuda_is_judged_by_the_runtime_the_backend_uses(self) -> None:
        # faster-whisper runs on ctranslate2. This machine has a CPU-only
        # torch build alongside a ctranslate2 that sees the GPU, so asking
        # torch reported a healthy pipeline as broken.
        config = _config(device="cuda", backend="faster-whisper")
        fake_ct = SimpleNamespace(get_cuda_device_count=lambda: 1)
        with (
            patch("openjarvis.core.health._get_config", return_value=config),
            patch.dict("sys.modules", {"ctranslate2": fake_ct}),
        ):
            result = _check_speech_device()
        assert result.status == "ok"
        assert "ctranslate2" in result.message

    def test_cuda_unavailable_to_that_runtime_fails(self) -> None:
        config = _config(device="cuda", backend="faster-whisper")
        fake_ct = SimpleNamespace(get_cuda_device_count=lambda: 0)
        with (
            patch("openjarvis.core.health._get_config", return_value=config),
            patch.dict("sys.modules", {"ctranslate2": fake_ct}),
        ):
            result = _check_speech_device()
        assert result.status == "fail"
        assert "slower" in (result.details or "")

    def test_cpu_and_auto_are_never_faults(self) -> None:
        for device in ("cpu", "auto", ""):
            config = _config(device=device, backend="faster-whisper")
            with patch("openjarvis.core.health._get_config", return_value=config):
                assert _check_speech_device().status == "ok"


class TestFluxStreaming:
    def _run(self, key, enabled=True):
        config = _config(flux_enabled=enabled, device="cpu", backend="faster-whisper")
        with (
            patch("openjarvis.core.health._get_config", return_value=config),
            patch("openjarvis.core.health._provider_key", return_value=key),
        ):
            return _check_flux_streaming()

    def test_a_key_from_the_credential_store_counts(self) -> None:
        # flux.is_available() reads os.environ only, so relying on it reported
        # a correctly configured key as missing outside the server process.
        assert self._run("a-key").status == "ok"

    def test_no_key_is_a_warning_with_the_reason(self) -> None:
        result = self._run("")
        assert result.status == "warn"
        assert "falls back" in (result.details or "")

    def test_the_server_kill_switch_is_reported(self) -> None:
        result = self._run("a-key", enabled=False)
        assert result.status == "warn"
