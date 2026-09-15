"""The chime is a wav the player can read, written once and reused."""

from __future__ import annotations

import wave
from unittest.mock import patch

from openjarvis.speech import chime


def test_chime_is_a_short_wav_written_once(tmp_path):
    with patch.object(chime.tempfile, "gettempdir", return_value=str(tmp_path)):
        path = chime.chime_path()
        first = path.stat().st_mtime_ns
        with wave.open(str(path), "rb") as w:
            seconds = w.getnframes() / w.getframerate()
        assert 0.3 <= seconds <= 0.5
        assert w.getnchannels() == 1
        assert chime.chime_path().stat().st_mtime_ns == first
