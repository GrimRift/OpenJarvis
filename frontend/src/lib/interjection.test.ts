import { describe, expect, it } from 'vitest';
import {
  findPause,
  heardText,
  INTERJECTION_FALLBACK,
  PAUSE_MIN_S,
  playedSeconds,
  readDecision,
  type ScheduledChunk,
} from './interjection';

const RATE = 1000;

/** A chunk from a pattern: 'v' = 10 ms of voice, '.' = 10 ms of silence. */
function chunk(startAt: number, pattern: string): ScheduledChunk {
  const samples = new Float32Array(pattern.length * 10);
  for (let i = 0; i < pattern.length; i++) {
    if (pattern[i] === 'v') samples.fill(0.3, i * 10, i * 10 + 10);
  }
  return { startAt, samples, rate: RATE };
}

describe('readDecision', () => {
  it('reads each kind', () => {
    expect(readDecision({ kind: 'noise' })).toEqual({ kind: 'noise', line: '' });
    expect(readDecision({ kind: 'now', line: 'ignored' })).toEqual({ kind: 'now', line: '' });
    expect(readDecision({ kind: 'new' })).toEqual({ kind: 'new', line: '' });
    expect(readDecision({ kind: 'after', line: ' Glad to hear it. ' })).toEqual({
      kind: 'after',
      line: 'Glad to hear it.',
    });
  });

  it('lets an "after" with nothing to say pass', () => {
    expect(readDecision({ kind: 'after', line: '  ' })).toEqual({ kind: 'noise', line: '' });
  });

  it('sends the words on when the answer is unreadable', () => {
    expect(readDecision(null)).toEqual(INTERJECTION_FALLBACK);
    expect(readDecision({ kind: 'add' })).toEqual(INTERJECTION_FALLBACK);
    expect(INTERJECTION_FALLBACK.kind).toBe('now');
  });
});

describe('findPause', () => {
  it('stops at the gap between two sentences, not at a breath', () => {
    // 0.5 s voice, a 50 ms breath, 0.3 s voice, a 300 ms sentence gap, more.
    const voice = `${'v'.repeat(50)}${'.'.repeat(5)}${'v'.repeat(30)}${'.'.repeat(30)}${'v'.repeat(40)}`;
    const at = findPause([chunk(0, voice)], 0.1);
    expect(at).not.toBeNull();
    expect(at!).toBeGreaterThanOrEqual(0.84);
    expect(at!).toBeLessThan(0.86 + PAUSE_MIN_S);
  });

  it('counts the silence between two chunks', () => {
    const at = findPause([chunk(0, 'v'.repeat(40)), chunk(0.7, 'v'.repeat(40))], 0.1);
    expect(at).toBeCloseTo(0.4, 2);
  });

  it('ends where the queue ends when no pause comes first', () => {
    expect(findPause([chunk(0, 'v'.repeat(100))], 0.2)).toBeCloseTo(1.0, 2);
  });

  it('gives up when nothing quiet comes in time', () => {
    expect(findPause([chunk(0, 'v'.repeat(800))], 0, 2)).toBeNull();
  });

  it('ignores what has already played', () => {
    // The gap at 0.2-0.5 s is behind the playhead.
    const voice = `${'v'.repeat(20)}${'.'.repeat(30)}${'v'.repeat(60)}`;
    expect(findPause([chunk(0, voice)], 0.6)).toBeCloseTo(1.1, 2);
  });
});

describe('playedSeconds', () => {
  it('counts only what has played', () => {
    const chunks = [chunk(0, 'v'.repeat(50)), chunk(0.5, 'v'.repeat(50))];
    expect(playedSeconds(chunks, 0.25)).toBeCloseTo(0.25);
    expect(playedSeconds(chunks, 0.75)).toBeCloseTo(0.75);
    expect(playedSeconds(chunks, 3)).toBeCloseTo(1);
  });
});

describe('heardText', () => {
  const answer =
    'Glad it seems to be easing, Sir. Take it slowly: rest and sip fluids. ' +
    'If you feel worse, seek help.';

  it('keeps the sentences heard, cut at the nearest sentence end', () => {
    // All synthesised: 100 chars over 10 s, 3.5 s heard ~ 35 chars.
    expect(heardText(answer, 3.5, 10, true)).toBe('Glad it seems to be easing, Sir.');
  });

  it('keeps everything once it has all played', () => {
    expect(heardText(answer, 10, 10, true)).toBe(answer);
  });

  it('keeps nothing when nothing was heard', () => {
    expect(heardText(answer, 0, 10, true)).toBe('');
  });

  it('estimates by speaking rate before the whole answer is synthesised', () => {
    // ~15 characters a second: 4.5 s is about 68 characters.
    expect(heardText(answer, 4.5, 6, false)).toBe(
      'Glad it seems to be easing, Sir. Take it slowly: rest and sip fluids.',
    );
  });
});
