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


#: What words heard while Sage answered turned out to be (decide_interjection).
#: noise: let pass. after: the answer stands; a short line follows it.
#: now: the answer changes; Sage stops at its next pause and carries on from
#: there. new: a different request, answered instead.
INTERJECTION_KINDS = ("noise", "after", "now", "new")

INTERJECTION_PROMPT = (
    "You are Sage, a voice assistant. While you were answering the user, the "
    "microphone heard them say more. Decide what to do. Start your reply "
    "with exactly one of these markers:\n"
    "[[noise]] -- garbled, filler (um, uh), a fragment that means nothing, or "
    "plainly someone else talking. Write nothing after it.\n"
    "[[after]] -- your answer still stands: they react, agree, thank you, or "
    "tell you something that needs only a brief reply. After the marker "
    "write that one short spoken line (under 20 words, plain words, no "
    "markdown); it is said once your current answer ends.\n"
    "[[now]] -- it changes what you should be saying: a correction, a detail "
    "that matters to the answer, or a request to say it differently. Write "
    "nothing after it: you will stop at the end of your sentence and carry on "
    "from there.\n"
    "[[new]] -- a different request. Write nothing after it."
)

#: When the decision cannot be made: the words are sent on, and the model
#: is told to finish its point if they turn out to be noise (CONTINUE_NOTE).
INTERJECTION_FALLBACK = "now"

def parse_interjection(reply: str) -> tuple[str, str]:
    """The marker's kind and, for "after", the line to say."""
    text = (reply or "").strip()
    for kind in INTERJECTION_KINDS:
        marker = f"[[{kind}]]"
        if text.lower().startswith(marker):
            line = text[len(marker) :].strip() if kind == "after" else ""
            if kind == "after" and not line:
                return "noise", ""
            return kind, line
    return INTERJECTION_FALLBACK, ""


def decide_interjection(
    engine, model: str, question: str, said: str, added: str
) -> tuple[str, str]:
    """noise, after (with its line), now or new for *added*, heard while Sage
    answered *question* and had said *said* so far."""
    from openjarvis.core.types import Message, Role

    heard = said.strip() or "(nothing yet)"
    try:
        result = engine.generate(
            [
                Message(role=Role.SYSTEM, content=INTERJECTION_PROMPT),
                Message(
                    role=Role.USER,
                    content=(
                        f"Their question: {question}\n"
                        f"What you have said so far: {heard}\n"
                        f"Then they said: {added}"
                    ),
                ),
            ],
            model=model,
            temperature=0.0,
            # A reasoning model spends tokens before its marker; a small
            # budget comes back empty (see memory: reasoning headroom).
            max_tokens=600,
        )
    except Exception:  # noqa: BLE001 -- the decision is advisory
        return INTERJECTION_FALLBACK, ""
    return parse_interjection(str((result or {}).get("content") or ""))


#: The turn after a "now": Sage stopped at a pause and carries on.
CONTINUE_NOTE = (
    "Interjection: your previous message is what you had said out loud when "
    "the user spoke over you, and you stopped there -- they did not hear "
    "anything you had written after it. Their new message came while you "
    "were talking. Carry on from where you stopped, taking what they said "
    "into account: do not repeat or restart what they already heard, and do "
    "not apologise for stopping. If their words turn out to be noise or not "
    "meant for you, just finish the point you were making. Do not mention "
    "this note."
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
    "CONTINUE_NOTE",
    "decide_interjection",
    "INTERJECTION_KINDS",
    "FOLLOWUP_NOTE",
    "IGNORE_MARKER",
    "IgnoreWatch",
]
