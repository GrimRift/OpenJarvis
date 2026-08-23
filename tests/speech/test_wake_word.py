

def test_reset_clears_audio_held_from_the_previous_session(tmp_path, monkeypatch):
    """A new listening session must not fire on the last one's wake word.

    One detector is shared by every session and scores a rolling window, so
    without a reset the window still ends with the "Hey Sage" that opened the
    previous exchange. Measured on the real model: two seconds of digital
    silence produced 8 detections when replayed straight after the wake word,
    and 0 after reset.
    """
    from openjarvis.speech.wake_word import WakeWordDetector

    detector = WakeWordDetector(model_path="unused.onnx")
    detector._consecutive_hits = 5

    class _Model:
        def __init__(self):
            self.reset_calls = 0

        def reset(self):
            self.reset_calls += 1

    model = _Model()
    detector._model = model

    detector.reset()

    assert detector._consecutive_hits == 0
    assert model.reset_calls == 1


def test_reset_is_safe_before_the_model_loads():
    """Sessions can open before the first frame forces a lazy load."""
    from openjarvis.speech.wake_word import WakeWordDetector

    detector = WakeWordDetector(model_path="unused.onnx")
    detector.reset()

    assert detector._consecutive_hits == 0
