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
 *
 * When the model then says nothing, the server gives the last preamble back
 * as the answer (`text_restore`); its opening was said already, so only the
 * part that was held is spoken (13 October).
 *
 * "Generation runs faster than speech" fails for a long answer: a story from
 * gpt-6-luna kept writing for 17 s after its 3 s opening, and the user heard
 * 14 s of silence (13 October, `tts.underrun gapMs=13900`). So only a local
 * model's rounds are held -- a cloud model writes a line before a tool, not
 * the answer -- and a hold ends by itself after MAX_HOLD_MS: a round still
 * writing that long without a tool call is the answer.
 */

/** About two short sentences: a lead-in is said whole, an answer is not. */
export const SPEAK_AHEAD_CHARS = 160;

/** Held this long with no tool call, a round is taken to be the answer. */
export const MAX_HOLD_MS = 2000;

export interface RoundSpeechOptions {
  /** Hold each round past its opening (local models). Default true. */
  holdDrafts?: boolean;
  /** Clock, for tests. */
  now?: () => number;
}

/** A sentence or line ends here (a delta may end right after the mark). */
const SENTENCE_END = /[.!?](?=\s|$)|\n/;

export class RoundSpeech {
  private length = 0;
  private held = '';
  private holding = false;
  private released = false;
  /** What this round has said aloud so far. */
  private spoken = '';
  /** What the last retracted round had said aloud. */
  private retractedSpoken = '';
  /** Characters of the coming text that were said already. */
  private skip = 0;
  /** When the current hold began. */
  private holdStartedAt = 0;
  private readonly holdDrafts: boolean;
  private readonly now: () => number;

  constructor(
    private readonly speak: (delta: string) => void,
    options: RoundSpeechOptions = {},
  ) {
    this.holdDrafts = options.holdDrafts ?? true;
    this.now = options.now ?? Date.now;
  }

  /** A piece of the current round's text. */
  push(delta: string): void {
    if (this.skip > 0) {
      const dropped = Math.min(this.skip, delta.length);
      this.skip -= dropped;
      this.length += dropped;
      this.spoken += delta.slice(0, dropped);
      delta = delta.slice(dropped);
    }
    if (!delta) return;
    if (this.holding) {
      this.held += delta;
      if (this.now() - this.holdStartedAt >= MAX_HOLD_MS) this.release();
      return;
    }
    const before = this.length;
    this.length += delta.length;
    if (this.released || !this.holdDrafts || this.length < SPEAK_AHEAD_CHARS) {
      this.say(delta);
      return;
    }
    const from = Math.max(0, SPEAK_AHEAD_CHARS - before);
    const end = SENTENCE_END.exec(delta.slice(from));
    if (!end) {
      // Mid-sentence: finish it before holding.
      this.say(delta);
      return;
    }
    const cut = from + end.index + 1;
    if (cut > 0) this.say(delta.slice(0, cut));
    this.held = delta.slice(cut);
    this.holding = true;
    this.holdStartedAt = this.now();
  }

  /** The round is the answer: say what was held, and the rest as it comes. */
  release(): void {
    this.released = true;
    this.holding = false;
    const held = this.held;
    this.held = '';
    if (held) this.say(held);
  }

  /** The round was a preamble to a tool: forget what it held, start anew. */
  retract(): void {
    this.retractedSpoken = this.spoken;
    this.spoken = '';
    this.skip = 0;
    this.length = 0;
    this.held = '';
    this.holding = false;
    this.released = false;
  }

  /**
   * The server is giving *text*, the last retracted preamble, back as the
   * answer. It arrives next as ordinary content: skip what was said of it.
   */
  restore(text: string): void {
    const said = this.retractedSpoken.trim();
    const body = text.trimStart();
    const lead = text.length - body.length;
    this.skip = said && body.startsWith(said) ? lead + said.length : 0;
    this.retractedSpoken = '';
    this.released = true;
  }

  private say(delta: string): void {
    this.spoken += delta;
    this.speak(delta);
  }
}
