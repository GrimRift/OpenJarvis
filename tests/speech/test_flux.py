"""Tests for the Deepgram Flux client — config validation and event parsing."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from openjarvis.speech.flux import (
    EVENT_EAGER_END_OF_TURN,
    EVENT_END_OF_TURN,
    EVENT_TURN_RESUMED,
    FluxConfigError,
    TurnEvent,
    build_url,
    is_available,
    validate_thresholds,
)


class TestThresholdValidation:
    """Deepgram rejects the connection for these, so catch them locally."""

    def test_accepts_documented_defaults(self):
        validate_thresholds(0.7, None, 5000)
        validate_thresholds(0.7, 0.6, 5000)

    def test_eager_above_eot_is_rejected(self):
        """Called out explicitly in Deepgram's docs as an error."""
        with pytest.raises(FluxConfigError, match="must be <="):
            validate_thresholds(0.7, 0.8, 5000)

    def test_eager_equal_to_eot_is_allowed(self):
        validate_thresholds(0.7, 0.7, 5000)

    @pytest.mark.parametrize("value", [0.4, 0.95])
    def test_eot_threshold_out_of_range(self, value):
        with pytest.raises(FluxConfigError):
            validate_thresholds(value, None, 5000)

    @pytest.mark.parametrize("value", [0.2, 0.95])
    def test_eager_threshold_out_of_range(self, value):
        with pytest.raises(FluxConfigError):
            validate_thresholds(0.9, value, 5000)

    @pytest.mark.parametrize("value", [100, 60001])
    def test_timeout_out_of_range(self, value):
        with pytest.raises(FluxConfigError):
            validate_thresholds(0.7, None, value)


class TestUrl:
    def test_standard_mode_omits_eager_entirely(self):
        """Presence of the parameter is what enables speculation."""
        url = build_url(
            model="flux-general-en",
            eot_threshold=0.7,
            eager_eot_threshold=None,
            eot_timeout_ms=5000,
        )
        assert "eager_eot_threshold" not in url

    def test_ultra_mode_includes_eager(self):
        url = build_url(
            model="flux-general-en",
            eot_threshold=0.7,
            eager_eot_threshold=0.6,
            eot_timeout_ms=5000,
        )
        assert "eager_eot_threshold=0.6" in url

    def test_audio_format_matches_the_wake_word_capture(self):
        url = build_url(
            model="flux-general-en",
            eot_threshold=0.7,
            eager_eot_threshold=None,
            eot_timeout_ms=5000,
        )
        assert "encoding=linear16" in url
        assert "sample_rate=16000" in url

    def test_invalid_thresholds_fail_before_connecting(self):
        with pytest.raises(FluxConfigError):
            build_url(
                model="flux-general-en",
                eot_threshold=0.7,
                eager_eot_threshold=0.9,
                eot_timeout_ms=5000,
            )


class TestAvailability:
    def test_unavailable_without_a_key(self):
        """Fail closed: no key means local STT, not a broken socket."""
        with patch.dict("os.environ", {"DEEPGRAM_API_KEY": ""}, clear=False):
            assert is_available() is False

    def test_available_with_a_key(self):
        with patch.dict("os.environ", {"DEEPGRAM_API_KEY": "k"}, clear=False):
            assert is_available() is True


class TestTurnEventParsing:
    def _msg(self, event: str, **over):
        data = {
            "type": "TurnInfo",
            "event": event,
            "turn_index": 3,
            "transcript": "hello there",
            "end_of_turn_confidence": "0.85",
            "audio_window_start": "0.0",
            "audio_window_end": "2.5",
            "words": [{"word": "hello", "confidence": "0.95"}],
        }
        data.update(over)
        return json.loads(json.dumps(data))

    def test_parses_string_floats(self):
        """Deepgram sends confidences as strings, not numbers."""
        ev = TurnEvent.from_message(self._msg(EVENT_END_OF_TURN))
        assert ev.end_of_turn_confidence == 0.85
        assert ev.audio_window_end == 2.5
        assert ev.turn_index == 3

    def test_classifies_events(self):
        final = TurnEvent.from_message(self._msg(EVENT_END_OF_TURN))
        eager = TurnEvent.from_message(self._msg(EVENT_EAGER_END_OF_TURN))
        resumed = TurnEvent.from_message(self._msg(EVENT_TURN_RESUMED))

        assert final.is_final and not final.is_speculative
        assert eager.is_speculative and not eager.is_final
        assert resumed.cancels_speculation
        assert not resumed.is_final

    def test_missing_fields_do_not_raise(self):
        ev = TurnEvent.from_message({"type": "TurnInfo", "event": "Update"})
        assert ev.turn_index == 0
        assert ev.transcript == ""
        assert ev.end_of_turn_confidence == 0.0

    def test_unparseable_confidence_degrades_to_zero(self):
        ev = TurnEvent.from_message(
            self._msg(EVENT_END_OF_TURN, end_of_turn_confidence="not-a-number")
        )
        assert ev.end_of_turn_confidence == 0.0
