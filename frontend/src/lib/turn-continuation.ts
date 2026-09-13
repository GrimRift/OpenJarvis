/**
 * Turn continuation: treating "wait, I wasn't finished" as one question.
 *
 * Deepgram ends a turn on a pause. A pause to remember the second half of a
 * sentence looks the same as the end of it, so "What do you think about that
 * idea we talked about" was sent, Sage started answering, and "— do you think
 * it's possible?" arrived afterwards as a brand-new turn that got its own
 * reply. The user's chosen behaviour is the human one: cancel the answer to
 * the half-question and answer the whole one.
 *
 * The policy is a pure function over what the caller knows -- when the last
 * turn was submitted, whether Sage's audio has started, and the current
 * time -- so the decision is testable without a microphone. The window is
 * bounded by audio, not by a timer: once Sage is audibly speaking, more
 * speech is a new turn (or barge-in, which is deliberately out of scope), and
 * the transmit gate closes for the reason it always has.
 */

/**
 * Longest silence after a submitted turn during which resumed speech still
 * counts as the same sentence. Past this, Sage has either started speaking
 * (in which case audio, not this, ends the window) or the user has genuinely
 * moved on. Generous because the failure it guards against -- a half-question
 * answered twice -- is far worse than a rare merged pair.
 */
export const CONTINUATION_WINDOW_MS = 4000;

export interface ContinuationState {
  /** Wall-clock ms when the previous turn was submitted, or null. */
  submittedAt: number | null;
  /** The transcript that was submitted. */
  submittedText: string;
  /** Whether Sage's reply audio has started playing. */
  sageSpeaking: boolean;
}

/** Whether speech starting now continues the previous turn. */
export function isContinuation(state: ContinuationState, now: number): boolean {
  if (state.submittedAt === null) return false;
  if (state.sageSpeaking) return false;
  return now - state.submittedAt <= CONTINUATION_WINDOW_MS;
}

/**
 * Join the two halves into one question. A dash or trailing punctuation on
 * the first half is dropped so "idea we talked about —" + "do you think"
 * reads as one sentence rather than a quotation of two.
 */
export function mergeTurns(first: string, second: string): string {
  const head = first.trim().replace(/[\s—–\-,;:]+$/u, '');
  const tail = second.trim();
  if (!head) return tail;
  if (!tail) return head;
  return `${head} ${tail}`;
}
