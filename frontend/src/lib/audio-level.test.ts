import { beforeEach, describe, expect, it } from 'vitest';
import {
  ATTACK,
  RELEASE,
  getSpeechLevel,
  resetSpeechLevel,
  rmsFromTimeDomain,
  setSpeechLevel,
  smoothLevel,
} from './audio-level';

/** AnalyserNode centres silence at 128. */
function tone(amplitude: number, samples = 512): Uint8Array {
  const data = new Uint8Array(samples);
  for (let i = 0; i < samples; i++) {
    data[i] = Math.round(128 + Math.sin((i / samples) * Math.PI * 8) * 127 * amplitude);
  }
  return data;
}

beforeEach(() => resetSpeechLevel());

describe('rmsFromTimeDomain', () => {
  it('reads silence as zero', () => {
    expect(rmsFromTimeDomain(new Uint8Array(512).fill(128))).toBe(0);
  });

  it('grows with amplitude', () => {
    const quiet = rmsFromTimeDomain(tone(0.15));
    const loud = rmsFromTimeDomain(tone(0.8));
    expect(loud).toBeGreaterThan(quiet);
  });

  it('uses most of the orb range for measured Sonic 3.6 speech', () => {
    // A 0.14 sine has roughly 0.10 RMS, representative of active Jarvis
    // syllables at the configured 1.9 volume. This used to map to only 0.32.
    const level = rmsFromTimeDomain(tone(0.14));
    expect(level).toBeGreaterThan(0.7);
    expect(level).toBeLessThan(0.9);
  });

  it('never exceeds one, even on a clipped signal', () => {
    // Otherwise the orb would scale without bound on a loud passage.
    const square = new Uint8Array(512);
    square.fill(255);
    expect(rmsFromTimeDomain(square)).toBeLessThanOrEqual(1);
  });

  it('is unbothered by an empty buffer', () => {
    expect(rmsFromTimeDomain(new Uint8Array(0))).toBe(0);
  });
});

describe('smoothLevel', () => {
  it('rises faster than it falls', () => {
    // Speech should feel responsive on attack but not strobe between
    // syllables, so release is deliberately gentler.
    const rise = smoothLevel(0, 1) - 0;
    const fall = 1 - smoothLevel(1, 0);
    expect(rise).toBeGreaterThan(fall);
    expect(rise).toBeCloseTo(ATTACK, 6);
    expect(fall).toBeCloseTo(RELEASE, 6);
  });

  it('releases quickly enough to articulate gaps between syllables', () => {
    expect(1 - smoothLevel(1, 0)).toBeGreaterThan(0.2);
  });

  it('scales with frame time', () => {
    expect(smoothLevel(0, 1, 2)).toBeGreaterThan(smoothLevel(0, 1, 1));
  });

  it('never overshoots on a long frame', () => {
    expect(smoothLevel(0, 1, 10)).toBeLessThanOrEqual(1);
  });

  it('settles at the target', () => {
    let level = 0;
    for (let i = 0; i < 200; i++) level = smoothLevel(level, 0.6);
    expect(level).toBeCloseTo(0.6, 4);
  });
});

describe('the shared level', () => {
  it('clamps to 0..1', () => {
    setSpeechLevel(5);
    expect(getSpeechLevel()).toBe(1);
    setSpeechLevel(-3);
    expect(getSpeechLevel()).toBe(0);
  });

  it('treats a non-finite reading as silence', () => {
    setSpeechLevel(Number.NaN);
    expect(getSpeechLevel()).toBe(0);
  });

  it('resets to zero when playback stops', () => {
    // Otherwise the orb keeps pulsing at whatever was last measured.
    setSpeechLevel(0.8);
    resetSpeechLevel();
    expect(getSpeechLevel()).toBe(0);
  });
});
