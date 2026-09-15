/**
 * The pure half of the moments feed: which records on the server's record
 * are new to this tab. Kept apart from the hook because the hook reaches
 * the store, and the store reads localStorage at module load, which the
 * unit tests have no DOM for.
 */

import type { MomentRecord } from './api';

/** Kinds that invite an answer: a question, or a greeting one might return. */
export const REPLY_KINDS: ReadonlySet<string> = new Set(['initiative', 'greeting', 'welcome_back']);
/** How recently the audio must have ended for the mic to open for it. */
export const REPLY_WINDOW_FRESH_MS = 15_000;

/**
 * Whether one of the fresh records is worth opening the microphone for:
 * spoken, of a kind that invites an answer, ended within the last few
 * seconds (a poll's worth, not a while ago), and not a follow-up line.
 */
export function replyWindowFor(fresh: MomentRecord[], nowMs: number): number | null {
  for (const record of [...fresh].reverse()) {
    if (!record.spoken || !REPLY_KINDS.has(record.kind)) continue;
    if (record.detail === 'follow-up') continue;
    if (!record.ended_at) continue;
    const endedMs = record.ended_at * 1000;
    if (nowMs - endedMs <= REPLY_WINDOW_FRESH_MS) return endedMs;
  }
  return null;
}

/** Pure: which records are new, and the watermark after them. */
export function newMoments(
  history: MomentRecord[],
  seen: number | null,
  now: number,
): { fresh: MomentRecord[]; seen: number } {
  // A tab that has never seen anything starts from now: dumping a month of
  // old greetings into the current chat helps nobody.
  const floor = seen ?? now;
  // A watch dropped as stale is on the server's record with why, but it was
  // never said, so it has no place in a transcript of what was.
  // Only what was actually said belongs in a transcript: the record also
  // carries bookkeeping -- declines, answers, held lines -- for the Logs.
  const fresh = history
    .filter((h) => h.at > floor && h.spoken)
    .sort((a, b) => a.at - b.at);
  const last = history.reduce((m, h) => Math.max(m, h.at), floor);
  return { fresh, seen: last };
}
