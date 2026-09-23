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

#: How long the page's "a reply is playing" lasts without being said again.
#: The page repeats it every few seconds while it plays, so a closed tab or
#: a lost "stopped" frees the floor within this.
BROWSER_PLAYING_TTL = 20.0
#: How long "the user is talking" lasts without a fresh turn event. Deepgram
#: sends an Update every few hundred ms while they speak; a lost EndOfTurn
#: must not hold a reminder back for good.
USER_TALKING_TTL = 10.0
#: How long a chat request counts as awaiting its reply's audio. The reply
#: to a spoken turn usually starts in 1-3 s; a text-only reply never starts
#: any, and must not hold a reminder back past this.
REPLY_PENDING_TTL = 20.0


@dataclass
class Activity:
    last_user_turn_at: Optional[float] = None
    last_reply_end_at: Optional[float] = None
    tts_streams: int = 0
    flux_transmitting: bool = False
    #: Until when the page says a reply is still being heard. The TTS stream
    #: ends when the audio is made, which is about twice as fast as it plays:
    #: the second half of every reply played with the server thinking it was
    #: over, and a reminder was spoken into one on 23 September.
    browser_playing_until: float = 0.0
    #: Until when the user is mid-turn, by Flux's turn events: talking, not
    #: merely with the microphone open in a silent listening window.
    user_talking_until: float = 0.0
    #: When the last chat request arrived and no reply audio has begun since.
    reply_pending_since: Optional[float] = None
    #: Whether the web interface has been open at any point since the server
    #: started. Sage autostarts with Windows, so without this it began making
    #: unprompted remarks into an empty room -- the user was elsewhere and had
    #: never opened the page. It says nothing about the window being open NOW:
    #: once seen, Sage may speak with the page closed.
    ui_seen: bool = False

    @property
    def reply_audible(self) -> bool:
        """A reply is being made or heard."""
        return self.tts_streams > 0 or time.time() < self.browser_playing_until

    @property
    def sage_mid_turn(self) -> bool:
        return self.reply_audible or self.flux_transmitting

    @property
    def exchange_live(self) -> bool:
        """Someone is actually saying something, or about to: the user is
        talking, their reply is being written, or it is being heard. Unlike
        sage_mid_turn, a listening window with nobody talking does not
        count -- a reminder waits for the reply, not for the window after
        it (asked for on 23 September: a task's answer waited out every
        follow-up window and was heard 32 s after it was ready)."""
        now = time.time()
        pending = (
            self.reply_pending_since is not None
            and now - self.reply_pending_since < REPLY_PENDING_TTL
        )
        return self.reply_audible or now < self.user_talking_until or pending


_state = Activity()


def note_user_turn(now: Optional[float] = None) -> None:
    with _lock:
        _state.last_user_turn_at = now if now is not None else time.time()
        _state.reply_pending_since = _state.last_user_turn_at


def user_talking(active: bool, now: Optional[float] = None) -> None:
    with _lock:
        now = now if now is not None else time.time()
        _state.user_talking_until = now + USER_TALKING_TTL if active else 0.0


def note_ui_seen() -> None:
    with _lock:
        _state.ui_seen = True


def tts_begin() -> None:
    with _lock:
        _state.tts_streams += 1
        _state.reply_pending_since = None


def tts_end(now: Optional[float] = None) -> None:
    with _lock:
        _state.tts_streams = max(0, _state.tts_streams - 1)
        _state.last_reply_end_at = now if now is not None else time.time()


def browser_playing(active: bool, now: Optional[float] = None) -> None:
    with _lock:
        now = now if now is not None else time.time()
        _state.browser_playing_until = now + BROWSER_PLAYING_TTL if active else 0.0


def flux_transmitting(active: bool) -> None:
    with _lock:
        _state.flux_transmitting = active
        if not active:
            _state.user_talking_until = 0.0


def snapshot() -> Activity:
    with _lock:
        return Activity(
            last_user_turn_at=_state.last_user_turn_at,
            last_reply_end_at=_state.last_reply_end_at,
            tts_streams=_state.tts_streams,
            flux_transmitting=_state.flux_transmitting,
            browser_playing_until=_state.browser_playing_until,
            user_talking_until=_state.user_talking_until,
            reply_pending_since=_state.reply_pending_since,
            ui_seen=_state.ui_seen,
        )


def reset() -> None:
    """For tests."""
    global _state
    with _lock:
        _state = Activity()


__all__ = [
    "Activity",
    "browser_playing",
    "flux_transmitting",
    "note_user_turn",
    "reset",
    "snapshot",
    "tts_begin",
    "user_talking",
    "tts_end",
]
