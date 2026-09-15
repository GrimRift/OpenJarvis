/**
 * When to say "one moment" (M36 phase 4).
 *
 * A tool call that runs on leaves the user in silence with nothing to show
 * for the question, and silence is the most robotic part of a voice turn.
 * The rule is small and pure so it can be tested without a stream: once per
 * turn, only on a turn that will be spoken, only while a tool is running,
 * only if nothing has been said back yet, and only after the wait has gone
 * on long enough to feel like a wait.
 */

export const FILLER_AFTER_MS = 3000;

export interface FillerState {
  /** Whether this turn's reply is spoken at all (voice, or typed with the setting on). */
  spoken: boolean;
  /** When the first tool call of the turn started, or null. */
  toolStartedAt: number | null;
  /** Whether any reply text has arrived. */
  contentStarted: boolean;
  /** Whether the filler has already played this turn. */
  played: boolean;
  /** Whether the turn has ended (done, error, or stopped). */
  ended: boolean;
}

export function initialFillerState(spoken: boolean): FillerState {
  return { spoken, toolStartedAt: null, contentStarted: false, played: false, ended: false };
}

/** Whether the filler should play right now. */
export function fillerDue(state: FillerState, now: number): boolean {
  return (
    state.spoken &&
    !state.played &&
    !state.ended &&
    !state.contentStarted &&
    state.toolStartedAt !== null &&
    now - state.toolStartedAt >= FILLER_AFTER_MS
  );
}
