import { describe, expect, it } from 'vitest';
import { DUCK_VOICE_RMS, DUCK_WINDOW_SAMPLES, voiceOverSage } from './voice-duck';

/** A frame of 20 ms slices, each a square wave at the given RMS. */
function frame(levels: number[]): Int16Array {
  const out = new Int16Array(levels.length * DUCK_WINDOW_SAMPLES);
  levels.forEach((level, slice) => {
    for (let i = 0; i < DUCK_WINDOW_SAMPLES; i++) {
      out[slice * DUCK_WINDOW_SAMPLES + i] = i % 2 ? level : -level;
    }
  });
  return out;
}

describe('voiceOverSage', () => {
  it('hears a chopped "stop": two loud slices are enough', () => {
    expect(voiceOverSage(frame([0, 3400, 4700, 0]), 1)).toBe(true);
  });

  it("lets Sage's own leak pass, however many slices it fills", () => {
    expect(voiceOverSage(frame([1800, 1800, 1800, 1800]), 1)).toBe(false);
  });

  it('ignores a single loud slice (a click, one syllable of leak)', () => {
    expect(voiceOverSage(frame([0, 6000, 0, 0]), 1)).toBe(false);
  });

  it("judges a quiet microphone at the automatic gain's level, up to the speaking cap", () => {
    expect(voiceOverSage(frame([0, 900, 880, 0]), 3)).toBe(true);
    expect(voiceOverSage(frame([600, 600, 600, 600]), 3)).toBe(false);
  });

  it("does not multiply Sage's leak by a gain raised for a quiet question", () => {
    // 13 October, gain 6.83: leak syllables of ~390-425 raw read 2,644-2,897
    // uncapped and dipped the reply at nothing.
    expect(voiceOverSage(frame([0, 425, 400, 0]), 6.83)).toBe(false);
    // A real "stop" over the reply still dips (16:12:20, ~3,700 raw).
    expect(voiceOverSage(frame([0, 3700, 3200, 0]), 6.83)).toBe(true);
  });

  it('treats a missing gain as unity', () => {
    expect(voiceOverSage(frame([DUCK_VOICE_RMS, DUCK_VOICE_RMS]), Number.NaN)).toBe(true);
  });
});
