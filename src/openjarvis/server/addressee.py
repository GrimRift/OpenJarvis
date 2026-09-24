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


#: What words said while Sage was preparing an answer turned out to be.
ADDITION_KINDS = ("noise", "add", "new")

ADDITION_PROMPT = (
    "The user asked a voice assistant a question, and while it was preparing "
    "the answer the microphone heard more words. Classify those words.\n"
    "noise: garbled, filler (blah, um), a fragment that means nothing, "
    "or plainly someone else talking.\n"
    "add: adds to, corrects or narrows the question.\n"
    "new: a different request.\n"
    "Reply with exactly one word: noise, add or new."
)


def classify_addition(engine, model: str, question: str, added: str) -> str:
    """noise, add or new for *added*, said after *question*. When the check
    cannot run, "add": the words then go to the model with the question,
    which is told to ignore them if they are noise (AMEND_NOTE)."""
    from openjarvis.core.types import Message, Role

    try:
        result = engine.generate(
            [
                Message(role=Role.SYSTEM, content=ADDITION_PROMPT),
                Message(
                    role=Role.USER,
                    content=f"Question: {question}\nThen heard: {added}",
                ),
            ],
            model=model,
            temperature=0.0,
            # A reasoning model spends tokens before its one word; a small
            # budget comes back empty (see memory: reasoning headroom).
            max_tokens=600,
        )
    except Exception:  # noqa: BLE001 -- the check is advisory
        return "add"
    reply = str((result or {}).get("content") or "").strip().lower()
    for kind in ADDITION_KINDS:
        if reply.startswith(kind):
            return kind
    return "add"


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
    "ADDITION_KINDS",
    "AMEND_NOTE",
    "classify_addition",
    "AMEND_SEPARATOR",
    "FOLLOWUP_NOTE",
    "IGNORE_MARKER",
    "IgnoreWatch",
]
