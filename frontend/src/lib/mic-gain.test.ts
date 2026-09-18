import { describe, expect, it } from 'vitest';
import {
  MAX_GAIN,
  MAX_TOTAL_GAIN,
  SILENCE_RMS,
  adaptFloor,
  TARGET_RMS,
  WAKE_MAX_GAIN,
  applyGain,
  nextGain,
  speechThreshold,
  totalGain,
  trackNoise,
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

describe('speechThreshold', () => {
  const FLOOR = 350;
  const CEILING = 6000;

  it('never pins at the ceiling because of the gain', () => {
    // 18 September: the threshold was ambient x gain x 4, which at 16x gain
    // clamped to 6000 while boosted speech only reaches about 2200. Every
    // turn logged speechRms=6000 and Sage heard a silent room.
    expect(speechThreshold(40, 16, FLOOR, CEILING)).toBeLessThan(2200);
    expect(speechThreshold(150, 16, FLOOR, CEILING)).toBeLessThan(CEILING);
  });

  it('admits quiet speech on a boosted microphone', () => {
    // Laptop mic: room tone 40, speech 400 raw. The floor alone (350) would
    // have swallowed that speech, so it is divided by the gain.
    const threshold = speechThreshold(40, 16, FLOOR, CEILING);
    expect(400).toBeGreaterThan(threshold);
    expect(40).toBeLessThan(threshold);
  });

  it('leaves a desk microphone exactly as it was', () => {
    // Unboosted, the floor still rules a quiet room.
    expect(speechThreshold(80, 1, FLOOR, CEILING)).toBe(FLOOR);
    expect(speechThreshold(500, 1, FLOOR, CEILING)).toBe(2000);
  });

  it('survives nonsense', () => {
    expect(speechThreshold(Number.NaN, 4, FLOOR, CEILING)).toBe(FLOOR / 4);
    expect(speechThreshold(-5, 0, FLOOR, CEILING)).toBe(FLOOR);
  });
});

describe('the room level this stream actually hears', () => {
  it('settles on the floor, not on the speech above it', () => {
    // The laptop's fan holds 433 beside the built-in mic; a sentence on top
    // of it must not drag the estimate up to speech.
    let noise = 0;
    for (let i = 0; i < 400; i++) noise = trackNoise(noise, 433);
    expect(noise).toBeCloseTo(433, 0);
    for (let i = 0; i < 60; i++) noise = trackNoise(noise, 1800);
    expect(noise).toBeLessThan(800);
    // And a pause pulls it straight back down.
    for (let i = 0; i < 60; i++) noise = trackNoise(noise, 433);
    expect(noise).toBeCloseTo(433, 0);
  });

  it('drops quickly when the fan stops', () => {
    let noise = 433;
    for (let i = 0; i < 80; i++) noise = trackNoise(noise, 20);
    expect(noise).toBeLessThan(60);
  });

  it('keeps the fan from driving the gain', () => {
    // 120 was the old fixed guard and sits far below this room.
    expect(adaptFloor(433)).toBeGreaterThan(433);
    expect(adaptFloor(433)).toBeCloseTo(693, 0);
    // A genuinely quiet room still uses the fixed guard.
    expect(adaptFloor(10)).toBe(SILENCE_RMS);
  });

  it('a fan-level frame no longer moves the gain', () => {
    const guard = adaptFloor(433);
    expect(nextGain(3, 433, { silence: guard })).toBe(3);
    // Real speech above it still does.
    expect(nextGain(1, 1800, { silence: guard })).toBeGreaterThan(1);
  });
});
