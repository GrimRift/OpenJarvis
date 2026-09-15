/**
 * When to say "one moment" (M36 phase 4).
 *
 * Silence between a question and its answer is the most robotic part of a
 * voice turn, whether Sage is generating or waiting on a tool. The rule is
 * small and pure so it can be tested without a stream: only on a turn that
 * will be spoken, only while nothing has been said back, first after the
 * wait has gone on long enough to feel like one, then again at a slower
 * cadence for as long as the silence lasts.
 */

export const FILLER_AFTER_MS = 5000;
export const FILLER_REPEAT_MS = 20_000;

export interface FillerState {
  /** Whether this turn's reply is spoken at all (voice, or typed with the setting on). */
  spoken: boolean;
  /** When the turn was submitted. */
  turnStartedAt: number;
  /** Whether any reply text has arrived. */
  contentStarted: boolean;
  /** When the last filler played this turn, or null. */
  lastFillerAt: number | null;
  /** Whether the turn has ended (done, error, or stopped). */
  ended: boolean;
}

export function initialFillerState(spoken: boolean, now: number): FillerState {
  return { spoken, turnStartedAt: now, contentStarted: false, lastFillerAt: null, ended: false };
}

/** Whether a filler should play right now; 'first' or 'again' says which line. */
export function fillerDue(state: FillerState, now: number): 'first' | 'again' | null {
  if (!state.spoken || state.ended || state.contentStarted) return null;
  if (state.lastFillerAt === null) {
    return now - state.turnStartedAt >= FILLER_AFTER_MS ? 'first' : null;
  }
  return now - state.lastFillerAt >= FILLER_REPEAT_MS ? 'again' : null;
}

/** Milliseconds until the next check is worth making, from the current state. */
export function nextFillerCheckMs(state: FillerState, now: number): number {
  const at =
    state.lastFillerAt === null
      ? state.turnStartedAt + FILLER_AFTER_MS
      : state.lastFillerAt + FILLER_REPEAT_MS;
  return Math.max(0, at - now);
}
