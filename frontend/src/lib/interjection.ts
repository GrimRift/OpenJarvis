/**
 * Words the user says over Sage's answer (15 October).
 *
 * Every addition used to throw the half-heard answer away and start over:
 * "I think my fever is going down" ... "think I'm okay" stopped a reply the
 * user was already listening to and wrote it again from the top. Now the
 * server decides (server/addressee.py decide_interjection):
 *
 * - noise: let pass;
 * - after: the answer stands, and a short line is said once it ends;
 * - now:   Sage finishes the sentence it is on, stops at the pause after it,
 *          and carries on from there with what the user added;
 * - new:   a different request, answered instead.
 *
 * The audio has no sentence marks, so "the end of the sentence" is found in
 * the sound itself: the first real pause in what is already queued.
 */

export type InterjectionKind = 'noise' | 'after' | 'now' | 'new';

export interface InterjectionDecision {
  kind: InterjectionKind;
  /** For "after": the line to say once the answer ends. */
  line: string;
}

/** What an unclear or failed decision becomes (server INTERJECTION_FALLBACK). */
export const INTERJECTION_FALLBACK: InterjectionDecision = { kind: 'now', line: '' };

export function readDecision(data: unknown): InterjectionDecision {
  if (!data || typeof data !== 'object') return INTERJECTION_FALLBACK;
  const { kind, line } = data as { kind?: unknown; line?: unknown };
  const text = typeof line === 'string' ? line.trim() : '';
  if (kind === 'noise' || kind === 'now' || kind === 'new') return { kind, line: '' };
  if (kind === 'after') return text ? { kind, line: text } : { kind: 'noise', line: '' };
  return INTERJECTION_FALLBACK;
}

/** One chunk of Sage's voice as it was scheduled on the audio clock. */
export interface ScheduledChunk {
  startAt: number;
  samples: Float32Array;
  rate: number;
}

/** A gap between sentences, not a breath inside one. */
export const PAUSE_MIN_S = 0.2;
/** Below this RMS (float PCM) a 20 ms slice is silence. */
export const PAUSE_RMS = 0.01;
/** How long Sage may carry on to reach a pause before it fades out anyway. */
export const PAUSE_MAX_WAIT_S = 4;
/** The fade at the stop, so it never ends on a click. */
export const STOP_FADE_S = 0.08;

const SLICE_S = 0.02;

/**
 * Seconds on the audio clock at which to stop: the start of the first pause
 * of at least PAUSE_MIN_S after `now`, within `maxWait`. A gap between two
 * chunks counts as silence, and so does the end of what is queued. Null when
 * nothing quiet comes in time.
 */
export function findPause(
  chunks: readonly ScheduledChunk[],
  now: number,
  maxWait = PAUSE_MAX_WAIT_S,
): number | null {
  const limit = now + maxWait;
  const ordered = [...chunks].sort((a, b) => a.startAt - b.startAt);
  let quietSince: number | null = null;
  let cursor = now;
  const quiet = (at: number) => {
    if (quietSince === null) quietSince = at;
  };
  const loud = () => {
    quietSince = null;
  };
  const longEnough = (at: number) => quietSince !== null && at - quietSince >= PAUSE_MIN_S;

  for (const chunk of ordered) {
    const end = chunk.startAt + chunk.samples.length / chunk.rate;
    if (end <= now) continue;
    if (chunk.startAt > cursor) {
      // Nothing scheduled in between: silence.
      quiet(cursor);
      if (longEnough(Math.min(chunk.startAt, limit))) return quietSince;
      if (chunk.startAt >= limit) return null;
    }
    const slice = Math.max(1, Math.round(SLICE_S * chunk.rate));
    const from = Math.max(0, Math.floor((Math.max(now, chunk.startAt) - chunk.startAt) * chunk.rate));
    for (let i = from; i < chunk.samples.length; i += slice) {
      const at = chunk.startAt + i / chunk.rate;
      if (at >= limit) return null;
      const stop = Math.min(i + slice, chunk.samples.length);
      let sum = 0;
      for (let j = i; j < stop; j++) sum += chunk.samples[j] * chunk.samples[j];
      if (Math.sqrt(sum / (stop - i)) < PAUSE_RMS) {
        quiet(at);
        if (longEnough(at + (stop - i) / chunk.rate)) return quietSince;
      } else {
        loud();
      }
    }
    cursor = Math.max(cursor, end);
  }
  // The queue ends here: what follows has not been synthesised, so this is
  // as far as Sage can go without starting a word it cannot finish.
  if (cursor < limit) return quietSince ?? cursor;
  return null;
}

/** Seconds of the reply that will have played by `at` on the audio clock. */
export function playedSeconds(chunks: readonly ScheduledChunk[], at: number): number {
  let total = 0;
  for (const chunk of chunks) {
    const length = chunk.samples.length / chunk.rate;
    total += Math.min(length, Math.max(0, at - chunk.startAt));
  }
  return total;
}

/** Characters a second of Sage's voice carries, when the whole reply has
 * not been synthesised yet and the ratio cannot be read off it. */
export const SPOKEN_CHARS_PER_S = 15;

/**
 * The part of `text` heard after `heard` seconds of its audio: an estimate
 * by speaking rate (the exact ratio when all of it was synthesised), moved
 * to the nearest sentence end, since Sage stops at a pause.
 */
export function heardText(
  text: string,
  heard: number,
  total: number,
  complete: boolean,
): string {
  if (!text) return '';
  const estimate =
    complete && total > 0 ? (text.length * heard) / total : heard * SPOKEN_CHARS_PER_S;
  if (estimate >= text.length) return text;
  const ends = [0];
  const pattern = /[.!?…]["')\]*_]*(?=\s|$)|\n\s*\n/g;
  for (let match = pattern.exec(text); match; match = pattern.exec(text)) {
    ends.push(match.index + match[0].length);
  }
  ends.push(text.length);
  let best = ends[0];
  for (const end of ends) {
    if (Math.abs(end - estimate) < Math.abs(best - estimate)) best = end;
  }
  return text.slice(0, best).trimEnd();
}
