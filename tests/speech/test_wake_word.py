

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


def test_clone_shares_config_but_not_audio_history():
    """Each listening session needs its own rolling buffer.

    Two overlapping sessions (the moment of a page refresh: the closing
    socket's in-flight frames still arriving as the new one opens) sharing
    one detector interleave audio into a single rolling window, and the
    hit counter carries across — so the new session can fire on audio that
    is partly the old session's.
    """
    from openjarvis.speech.wake_word import WakeWordDetector

    original = WakeWordDetector(model_path="unused.onnx", threshold=0.83)
    original._consecutive_hits = 4
    original._model = object()

    clone = original.clone()

    assert clone._model_path == original._model_path
    assert clone._threshold == original._threshold
    assert clone._consecutive_hits == 0
    assert clone._model is None
    # Mutating the clone must not disturb the session it was cloned from.
    clone._consecutive_hits = 9
    assert original._consecutive_hits == 4


def test_detections_are_suppressed_until_the_buffer_warms_up():
    """A just-reset detector must not fire, no matter how it scores.

    A fresh detector's rolling window needs WARMUP_FRAMES of real history
    before it means anything, and the underlying model's score spikes right
    at that fill point regardless of audio content -- measured on this
    project's own recordings, every quiet-room negative clip peaked there,
    and in production every page refresh (a fresh WebSocket -> fresh
    detector) fired on nothing but ordinary room noise.
    """
    from openjarvis.speech.wake_word import WARMUP_FRAMES, WakeWordDetector

    detector = WakeWordDetector(model_path="unused.onnx", threshold=0.5)
    detector._consecutive_hits = 5  # patience already satisfied

    detector._frames_since_reset = WARMUP_FRAMES
    assert detector.is_detection(0.99) is False

    detector._frames_since_reset = WARMUP_FRAMES + 1
    assert detector.is_detection(0.99) is True


def test_warmup_counter_resets_with_everything_else():
    from openjarvis.speech.wake_word import WARMUP_FRAMES, WakeWordDetector

    detector = WakeWordDetector(model_path="unused.onnx")
    detector._frames_since_reset = WARMUP_FRAMES + 10

    detector.reset()

    assert detector._frames_since_reset == 0


def test_detection_consumes_the_utterance():
    """A single spoken wake word must not report itself several times.

    The model scores a rolling window, so one "Hey Sage" stays above
    threshold for several consecutive frames. Every one of those frames is
    a detection, and a real trigger delivered three in a row — each one
    starting its own greeting. Resetting after a detection clears the
    window (and re-arms the warm-up gate) so the same utterance cannot be
    detected twice.
    """
    from openjarvis.speech.wake_word import WARMUP_FRAMES, WakeWordDetector

    detector = WakeWordDetector(model_path="unused.onnx", threshold=0.5)
    detector._frames_since_reset = WARMUP_FRAMES + 1
    detector._consecutive_hits = 5

    assert detector.is_detection(0.99) is True

    detector.reset()

    # Straight after a detection the next loud frame must not re-fire: the
    # counters are back to zero and the warm-up gate is closed again.
    assert detector.is_detection(0.99) is False
    assert detector._frames_since_reset == 0
    assert detector._consecutive_hits == 0
