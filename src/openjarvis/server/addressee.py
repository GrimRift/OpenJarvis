"""Whether a follow-up voice turn was meant for Sage at all.

After a reply the microphone stays open for a follow-up, and whatever it
hears becomes a question. With people talking nearby, or a friend who never
stops talking, that was a stream of fragments -- each answered, each answer
cut off by the next fragment, again and again (24 September; he rated it
5/10). A turn the user opened with the wake word or the mic button is
always for Sage; one heard in the follow-up window may not be.

The model that answers is the one best placed to tell, and asking it costs
nothing extra: the follow-up turn carries a note letting it decline with a
marker instead of answering, and the stream holds its first words until
they show whether they are that marker. A declined turn is never shown,
spoken or remembered; the browser just keeps listening.
"""

from __future__ import annotations

IGNORE_MARKER = "[[ignore]]"

FOLLOWUP_NOTE = (
    "Addressee check: this message was heard by the microphone after your "
    "last reply, without the user saying your name or pressing the mic "
    "button, so it may not be meant for you -- people nearby talking to "
    "each other, someone on the phone, a half-heard fragment, or speech too "
    "garbled to make sense of. If it is clearly not addressed to you, reply "
    f"with exactly {IGNORE_MARKER} and nothing else. If it could be for you "
    "-- a question, a request, a reply to what you just said -- answer it "
    "normally. When unsure, answer."
)


#: Between the two parts of an amended question (see AMEND_NOTE).
AMEND_SEPARATOR = "\n\n(Then, while you were answering:) "

AMEND_NOTE = (
    "Amended question: the user's message is what they first asked, then "
    "what they said while you were still preparing the answer, after "
    '"(Then, while you were answering:)". Nothing of the first answer was '
    "given. If the later words add to or correct the first request, answer "
    "the combined request. If they are a separate new request, answer only "
    "the new one. If they are garbled, a fragment, or plainly not said to "
    "you -- someone else talking, noise -- ignore them and answer the first "
    "request as if they had not been said. Do not mention this note."
)


class IgnoreWatch:
    """Hold a reply's first words until they are, or cannot be, the marker."""

    def __init__(self) -> None:
        self._held = ""
        self._decided = False
        self.ignored = False

    def feed(self, delta: str) -> str:
        """Text to release now: nothing while it may still be the marker."""
        if self._decided:
            return delta
        self._held += delta
        probe = self._held.lstrip()
        if probe.startswith(IGNORE_MARKER):
            self.ignored = True
            self._decided = True
            return ""
        if IGNORE_MARKER.startswith(probe):
            return ""  # "[[ig" so far: still undecided
        self._decided = True
        released, self._held = self._held, ""
        return released

    def finish(self) -> str:
        """What is still held when the round ends: a reply shorter than the
        marker, or all whitespace."""
        self._decided = True
        if self.ignored:
            return ""
        released, self._held = self._held, ""
        return released


__all__ = [
    "AMEND_NOTE",
    "AMEND_SEPARATOR",
    "FOLLOWUP_NOTE",
    "IGNORE_MARKER",
    "IgnoreWatch",
]
