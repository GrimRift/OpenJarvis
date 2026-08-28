import { describe, expect, it } from 'vitest';
import {
  chunkDuration,
  decodePcmF32,
  interpretTtsMessage,
  nextStartTime,
} from './pcm-playback';

function pcm(values: number[]): ArrayBuffer {
  return new Float32Array(values).buffer;
}

describe('decodePcmF32', () => {
  it('reads little-endian float32 samples', () => {
    const out = decodePcmF32(pcm([0, 0.5, -0.5, 1]));
    expect(Array.from(out)).toEqual([0, 0.5, -0.5, 1]);
  });

  it('drops a trailing partial frame', () => {
    // A stray byte would shift every later sample and turn the rest of the
    // reply into noise.
    const full = pcm([0.25, 0.75]);
    const ragged = new Uint8Array(full.byteLength + 3);
    ragged.set(new Uint8Array(full), 0);

    const out = decodePcmF32(ragged.buffer);

    expect(Array.from(out)).toEqual([0.25, 0.75]);
  });

  it('returns nothing for a buffer shorter than one sample', () => {
    expect(decodePcmF32(new Uint8Array([1, 2, 3]).buffer).length).toBe(0);
  });
});

describe('nextStartTime', () => {
  it('queues back to back while generation stays ahead', () => {
    // Generation runs ~4.3x faster than playback, so this is the normal case.
    expect(nextStartTime(1.0, 3.5)).toBe(3.5);
  });

  it('never schedules in the past', () => {
    // Web Audio silently drops a buffer started before currentTime.
    expect(nextStartTime(5.0, 2.0)).toBeGreaterThan(5.0);
  });

  it('applies a safety margin to the first chunk', () => {
    expect(nextStartTime(0, 0, 0.02)).toBeCloseTo(0.02, 6);
  });
});

describe('chunkDuration', () => {
  it('converts samples to seconds', () => {
    expect(chunkDuration(24000, 24000)).toBe(1);
    expect(chunkDuration(12000, 24000)).toBe(0.5);
  });

  it('is zero for a nonsense sample rate rather than Infinity', () => {
    expect(chunkDuration(1000, 0)).toBe(0);
  });
});

describe('interpretTtsMessage', () => {
  it('reads the start event and its sample rate', () => {
    expect(
      interpretTtsMessage(JSON.stringify({ type: 'start', sample_rate: 24000 })),
    ).toEqual({ kind: 'start', sampleRate: 24000 });
  });

  it('falls back to 24kHz when the rate is missing', () => {
    expect(interpretTtsMessage(JSON.stringify({ type: 'start' }))).toEqual({
      kind: 'start',
      sampleRate: 24000,
    });
  });

  it('marks a pre-audio error as recoverable', () => {
    // The caller may fall back to the batch endpoint: nothing was heard yet.
    expect(
      interpretTtsMessage(
        JSON.stringify({ type: 'error', reason: 'boom', started: false }),
      ),
    ).toEqual({ kind: 'error', reason: 'boom', started: false });
  });

  it('marks a mid-audio error as already started', () => {
    // Falling back here would speak the opening of the reply twice.
    const out = interpretTtsMessage(
      JSON.stringify({ type: 'error', reason: 'dropped', started: true }),
    );
    expect(out).toEqual({ kind: 'error', reason: 'dropped', started: true });
  });

  it('reads done', () => {
    expect(interpretTtsMessage(JSON.stringify({ type: 'done' }))).toEqual({
      kind: 'done',
    });
  });

  it('ignores malformed json and unknown types', () => {
    expect(interpretTtsMessage('not json').kind).toBe('ignored');
    expect(interpretTtsMessage(JSON.stringify({ type: 'wat' })).kind).toBe(
      'ignored',
    );
  });
});
