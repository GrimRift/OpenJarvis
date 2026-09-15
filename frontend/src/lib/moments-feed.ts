/**
 * The pure half of the moments feed: which records on the server's record
 * are new to this tab. Kept apart from the hook because the hook reaches
 * the store, and the store reads localStorage at module load, which the
 * unit tests have no DOM for.
 */

import type { MomentRecord } from './api';

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
  const fresh = history
    .filter((h) => h.at > floor && !h.text.startsWith('(not said)'))
    .sort((a, b) => a.at - b.at);
  const last = history.reduce((m, h) => Math.max(m, h.at), floor);
  return { fresh, seen: last };
}
