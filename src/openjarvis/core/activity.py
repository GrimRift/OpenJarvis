"""What Sage is doing right now, as seen from the server (M37).

Initiative must not speak into a conversation that is already happening.
The browser owns most of that state, but three things are visible here:
a chat request arriving, a reply being spoken through the TTS stream, and
the microphone transmitting to Flux. Recorded as timestamps and counters
by the routes that own them; read by the initiative policy.

Module-level on purpose: the routes and the moment engine are wired
separately in ``jarvis serve`` (the standing trap), and a module is the one
thing both can reach without injection.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

_lock = threading.Lock()


@dataclass
class Activity:
    last_user_turn_at: Optional[float] = None
    last_reply_end_at: Optional[float] = None
    tts_streams: int = 0
    flux_transmitting: bool = False

    @property
    def sage_mid_turn(self) -> bool:
        return self.tts_streams > 0 or self.flux_transmitting


_state = Activity()


def note_user_turn(now: Optional[float] = None) -> None:
    with _lock:
        _state.last_user_turn_at = now if now is not None else time.time()


def tts_begin() -> None:
    with _lock:
        _state.tts_streams += 1


def tts_end(now: Optional[float] = None) -> None:
    with _lock:
        _state.tts_streams = max(0, _state.tts_streams - 1)
        _state.last_reply_end_at = now if now is not None else time.time()


def flux_transmitting(active: bool) -> None:
    with _lock:
        _state.flux_transmitting = active


def snapshot() -> Activity:
    with _lock:
        return Activity(
            last_user_turn_at=_state.last_user_turn_at,
            last_reply_end_at=_state.last_reply_end_at,
            tts_streams=_state.tts_streams,
            flux_transmitting=_state.flux_transmitting,
        )


def reset() -> None:
    """For tests."""
    global _state
    with _lock:
        _state = Activity()


__all__ = [
    "Activity",
    "flux_transmitting",
    "note_user_turn",
    "reset",
    "snapshot",
    "tts_begin",
    "tts_end",
]
