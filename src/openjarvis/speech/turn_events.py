"""The streaming-turn vocabulary every live STT provider speaks.

Named after Deepgram Flux's events because Flux came first and the browser
already understands them; a provider that cannot produce an event (Parakeet
has no eager end or resumption) simply never emits it, and everything
downstream that keys on those events stays idle. Keeping one vocabulary is
what lets ``server/flux_routes.py`` proxy either provider through the same
socket and the browser's turn handling stay untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

# Kept as constants because a typo in one of these silently means "turn
# never ends".
EVENT_START_OF_TURN = "StartOfTurn"
EVENT_UPDATE = "Update"
EVENT_EAGER_END_OF_TURN = "EagerEndOfTurn"
EVENT_TURN_RESUMED = "TurnResumed"
EVENT_END_OF_TURN = "EndOfTurn"


@dataclass
class TurnEvent:
    """One turn message, normalised.

    ``turn_index`` is the identity everything downstream keys on: speculative
    work started for one turn must never be released against another.
    """

    event: str
    turn_index: int
    transcript: str = ""
    end_of_turn_confidence: float = 0.0
    audio_window_start: float = 0.0
    audio_window_end: float = 0.0
    words: List[Dict[str, Any]] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_final(self) -> bool:
        return self.event == EVENT_END_OF_TURN

    @property
    def is_speculative(self) -> bool:
        return self.event == EVENT_EAGER_END_OF_TURN

    @property
    def cancels_speculation(self) -> bool:
        return self.event == EVENT_TURN_RESUMED

    @classmethod
    def from_message(cls, data: Dict[str, Any]) -> "TurnEvent":
        def _f(key: str) -> float:
            # Deepgram sends these as strings ("0.85"), not numbers.
            try:
                return float(data.get(key) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        return cls(
            event=str(data.get("event") or ""),
            turn_index=int(data.get("turn_index") or 0),
            transcript=str(data.get("transcript") or ""),
            end_of_turn_confidence=_f("end_of_turn_confidence"),
            audio_window_start=_f("audio_window_start"),
            audio_window_end=_f("audio_window_end"),
            words=list(data.get("words") or []),
            raw=data,
        )


__all__ = [
    "EVENT_EAGER_END_OF_TURN",
    "EVENT_END_OF_TURN",
    "EVENT_START_OF_TURN",
    "EVENT_TURN_RESUMED",
    "EVENT_UPDATE",
    "TurnEvent",
]
