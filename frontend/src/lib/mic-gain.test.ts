import { describe, expect, it } from 'vitest';
import {
  MAX_GAIN,
  MAX_TOTAL_GAIN,
  SILENCE_RMS,
  TARGET_RMS,
  WAKE_MAX_GAIN,
  applyGain,
  nextGain,
  totalGain,
} from './mic-gain';

describe('nextGain', () => {
  it('climbs toward the target when the speaker is quiet', () => {
    // A laptop mic across the room: far under target, so the gain rises.
    let gain = 1;
    for (let i = 0; i < 200; i++) gain = nextGain(gain, 400);
    expect(gain).toBeGreaterThan(3);
    expect(gain).toBeCloseTo(TARGET_RMS / 400, 1);
  });

  it('climbs slowly and falls fast', () => {
    // One loud frame must pull the gain down at once (clipping is
    // immediate); one quiet frame must not shove it up (that is pumping).
    const up = nextGain(1, 400);
    expect(up - 1).toBeLessThan(0.5);
    const down = nextGain(6, 6000);
    expect(6 - down).toBeGreaterThan(1);
  });

  it('never winds up on a silent room', () => {
    // The fan must not become a voice.
    expect(nextGain(2, SILENCE_RMS - 1)).toBe(2);
    expect(nextGain(2, 0)).toBe(2);
    let gain = 1;
    for (let i = 0; i < 500; i++) gain = nextGain(gain, 30);
    expect(gain).toBe(1);
  });

  it('stops at the ceiling, and a gentler one for the wake word', () => {
    let gain = 1;
    for (let i = 0; i < 2000; i++) gain = nextGain(gain, 5);
    expect(gain).toBeLessThanOrEqual(MAX_GAIN);

    let wake = 1;
    for (let i = 0; i < 2000; i++) {
      wake = nextGain(wake, 200, { max: WAKE_MAX_GAIN });
    }
    expect(wake).toBeLessThanOrEqual(WAKE_MAX_GAIN);
  });

  it('never attenuates, and survives nonsense', () => {
    expect(nextGain(1, 30000)).toBe(1);
    expect(nextGain(Number.NaN, 400)).toBeGreaterThanOrEqual(1);
    expect(nextGain(-3, 400)).toBeGreaterThanOrEqual(1);
    expect(nextGain(2, Number.NaN)).toBe(2);
  });
});

describe('applyGain', () => {
  it('scales the samples', () => {
    const out = applyGain(Int16Array.from([100, -200, 0]), 3);
    expect(Array.from(out)).toEqual([300, -600, 0]);
  });

  it('clips instead of wrapping around', () => {
    // Wrapping turns a loud vowel into a burst of noise.
    const out = applyGain(Int16Array.from([20000, -20000]), 4);
    expect(Array.from(out)).toEqual([32767, -32768]);
  });

  it('hands back the same array when there is nothing to do', () => {
    const samples = Int16Array.from([1, 2, 3]);
    expect(applyGain(samples, 1)).toBe(samples);
    expect(applyGain(samples, 0.5)).toBe(samples);
  });
});

describe('totalGain', () => {
  it('multiplies the slider into the automatic gain, within a ceiling', () => {
    expect(totalGain(4, 2)).toBe(8);
    expect(totalGain(8, 4)).toBe(MAX_TOTAL_GAIN);
    expect(totalGain(1, 1)).toBe(1);
  });

  it('treats missing or absurd values as no change', () => {
    expect(totalGain(Number.NaN, 2)).toBe(2);
    expect(totalGain(3, 0)).toBe(3);
    expect(totalGain(-1, -1)).toBe(1);
  });
});

describe('what a real microphone gets', () => {
  it('lifts a laptop mic across the room to a usable level', () => {
    // A built-in mic at arm's length measures in the low hundreds where a
    // desk mic gives thousands; that gap is why the user had to shout.
    let gain = 1;
    for (let i = 0; i < 300; i++) gain = nextGain(gain, 380);
    const heard = applyGain(Int16Array.from([380, -380]), gain);
    expect(Math.abs(heard[0])).toBeGreaterThan(1500);
    expect(Math.abs(heard[0])).toBeLessThanOrEqual(TARGET_RMS * 1.2);
  });

  it('leaves a desk microphone essentially alone', () => {
    let gain = 1;
    for (let i = 0; i < 300; i++) gain = nextGain(gain, 2400);
    expect(gain).toBeLessThan(1.2);
  });

  it('does not turn a fan into speech', () => {
    // Room tone never drives the gain, so after speech stops the level it
    // was left at still keeps the fan far below a speaking voice.
    let gain = 1;
    for (let i = 0; i < 300; i++) gain = nextGain(gain, 380);
    const fan = applyGain(Int16Array.from([70, -70]), gain);
    const voice = applyGain(Int16Array.from([380, -380]), gain);
    expect(Math.abs(fan[0]) * 3).toBeLessThan(Math.abs(voice[0]));
  });
});
