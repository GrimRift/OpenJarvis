/**
 * Speaking a model round before knowing whether it is the answer.
 *
 * Text the model writes in the same round as a tool call is retracted from
 * the chat (`text_retract`): the answer after the tool restates it. For a
 * cloud model that text is a line ("Let me check, Sir."). A small local
 * model can write the WHOLE answer first and only then decide to search --
 * qwen3.5-4b did on "can lewis still win?" (10 October) -- and the user
 * heard the answer read out twice, the second time as "Updated Analysis".
 *
 * So each round speaks its opening at once, up to the first sentence end
 * past SPEAK_AHEAD_CHARS, and holds the rest until the round turns out to be
 * the answer (the stream ends). A round retracted for its tool drops what it
 * held, unspoken. Generation runs many times faster than speech, so the hold
 * is normally over before the opening has finished playing.
 */

/** About two short sentences: a lead-in is said whole, an answer is not. */
export const SPEAK_AHEAD_CHARS = 160;

/** A sentence or line ends here (a delta may end right after the mark). */
const SENTENCE_END = /[.!?](?=\s|$)|\n/;

export class RoundSpeech {
  private length = 0;
  private held = '';
  private holding = false;
  private released = false;

  constructor(private readonly speak: (delta: string) => void) {}

  /** A piece of the current round's text. */
  push(delta: string): void {
    if (!delta) return;
    if (this.holding) {
      this.held += delta;
      return;
    }
    const before = this.length;
    this.length += delta.length;
    if (this.released || this.length < SPEAK_AHEAD_CHARS) {
      this.speak(delta);
      return;
    }
    const from = Math.max(0, SPEAK_AHEAD_CHARS - before);
    const end = SENTENCE_END.exec(delta.slice(from));
    if (!end) {
      // Mid-sentence: finish it before holding.
      this.speak(delta);
      return;
    }
    const cut = from + end.index + 1;
    if (cut > 0) this.speak(delta.slice(0, cut));
    this.held = delta.slice(cut);
    this.holding = true;
  }

  /** The round is the answer: say what was held, and the rest as it comes. */
  release(): void {
    this.released = true;
    this.holding = false;
    const held = this.held;
    this.held = '';
    if (held) this.speak(held);
  }

  /** The round was a preamble to a tool: forget what it held, start anew. */
  retract(): void {
    this.length = 0;
    this.held = '';
    this.holding = false;
    this.released = false;
  }
}
