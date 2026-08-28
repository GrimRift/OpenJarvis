"""The lazy openwakeword load must survive two sockets opening at once.

Observed live: two /v1/speech/wake-word connections arriving together each
scored frames on their own thread via asyncio.to_thread, and one thread reached
``openwakeword.FEATURE_MODELS`` while the other was still executing the
package's ``__init__``:

    AttributeError: partially initialized module 'openwakeword' has no
    attribute 'FEATURE_MODELS' (most likely due to a circular import)

It recovered on reconnect, so it read as noise, but the first wake word after a
server restart could be lost.
"""

from __future__ import annotations

import sys
import threading
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

from openjarvis.speech.wake_word import WakeWordDetector


@pytest.fixture()
def model_file(tmp_path):
    path = tmp_path / "Hey_Sage.onnx"
    path.write_bytes(b"not a real model")
    return str(path)


class _FakeOpenWakeWord:
    """Stands in for the package, recording how many threads are inside the
    import/download at the same time."""

    def __init__(self, delay: float = 0.05):
        self.delay = delay
        self.concurrent = 0
        self.max_concurrent = 0
        self.loads = 0
        self._lock = threading.Lock()

    def download_models(self, model_names=None):
        with self._lock:
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
            self.loads += 1
        # Long enough that an unserialised second thread would overlap.
        threading.Event().wait(self.delay)
        with self._lock:
            self.concurrent -= 1

    def install(self):
        pkg = ModuleType("openwakeword")
        model_mod = ModuleType("openwakeword.model")
        utils_mod = ModuleType("openwakeword.utils")

        model_mod.Model = lambda **kw: SimpleNamespace(prediction_buffer={})
        utils_mod.download_models = self.download_models
        pkg.model = model_mod
        pkg.utils = utils_mod

        return patch.dict(
            sys.modules,
            {
                "openwakeword": pkg,
                "openwakeword.model": model_mod,
                "openwakeword.utils": utils_mod,
            },
        )


def test_two_detectors_loading_together_are_serialised(model_file):
    fake = _FakeOpenWakeWord()
    detectors = [WakeWordDetector(model_path=model_file) for _ in range(2)]
    errors: list[BaseException] = []

    def load(detector):
        try:
            detector._ensure_loaded()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    with fake.install():
        threads = [
            threading.Thread(target=load, args=(d,)) for d in detectors
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert errors == []
    # The point: never two threads inside the import at once.
    assert fake.max_concurrent == 1
    assert all(d._model is not None for d in detectors)


def test_a_loaded_detector_does_not_take_the_lock_again(model_file):
    """Every audio frame calls this, so the common path must stay lock-free."""
    fake = _FakeOpenWakeWord(delay=0)
    detector = WakeWordDetector(model_path=model_file)

    with fake.install():
        detector._ensure_loaded()
        detector._ensure_loaded()
        detector._ensure_loaded()

    assert fake.loads == 1


def test_a_missing_model_still_raises_before_any_import(model_file):
    detector = WakeWordDetector(model_path="")

    with pytest.raises(RuntimeError, match="not configured"):
        detector._ensure_loaded()
