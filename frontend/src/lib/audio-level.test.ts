import { beforeEach, describe, expect, it } from 'vitest';
import {
  ATTACK,
  RELEASE,
  getSpeechLevel,
  ORB_NEUTRAL,
  resetSpeechLevel,
  setOrbShaping,
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

  it('puts real speech mid-range, with headroom for a loud syllable', () => {
    // Measured on five real lines in the local voice, before the volume:
    // RMS median 0.134, p90 0.289. A sine's RMS is its amplitude / sqrt 2.
    const typical = rmsFromTimeDomain(tone(0.134 * Math.SQRT2));
    const loud = rmsFromTimeDomain(tone(0.25 * Math.SQRT2));
    expect(typical).toBeGreaterThan(0.4);
    expect(typical).toBeLessThan(0.55);
    // A loud syllable still reads louder rather than pinned at the ceiling.
    expect(loud).toBeGreaterThan(typical);
    expect(loud).toBeLessThan(1);
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

describe('per-voice orb shaping', () => {
  const tone = (amplitude: number) =>
    Uint8Array.from({ length: 512 }, (_, i) => Math.round(128 + amplitude * 127 * Math.sin(i / 3)));

  beforeEach(() => setOrbShaping(ORB_NEUTRAL));

  it('leaves the level alone when neutral, as for Nano Jarvis', () => {
    const plain = rmsFromTimeDomain(tone(0.15));
    setOrbShaping({ gain: 1, contrast: 1 });
    expect(rmsFromTimeDomain(tone(0.15))).toBeCloseTo(plain, 6);
  });

  it('deepens the dips more than it lifts the peaks', () => {
    const quiet = rmsFromTimeDomain(tone(0.08));
    const loud = rmsFromTimeDomain(tone(0.2));
    setOrbShaping({ gain: 1.2, contrast: 1.5 });
    const quietShaped = rmsFromTimeDomain(tone(0.08));
    const loudShaped = rmsFromTimeDomain(tone(0.2));
    // A wider swing between syllable peaks and the dips between them.
    expect(loudShaped - quietShaped).toBeGreaterThan(loud - quiet);
    expect(loudShaped).toBeLessThanOrEqual(1);
  });

  it('bounds a bad value instead of trusting it', () => {
    setOrbShaping({ gain: Number.NaN, contrast: 99 });
    const shaped = rmsFromTimeDomain(tone(0.15));
    expect(shaped).toBeGreaterThanOrEqual(0);
    expect(shaped).toBeLessThanOrEqual(1);
  });
});
