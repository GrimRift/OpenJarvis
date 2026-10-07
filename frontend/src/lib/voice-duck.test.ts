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

  it("judges a quiet microphone at the automatic gain's level", () => {
    // Raw PD100X: the user's stops 225-400, Sage's leak under 180, gain 8.
    expect(voiceOverSage(frame([0, 400, 380, 0]), 8)).toBe(true);
    expect(voiceOverSage(frame([180, 180, 180, 180]), 8)).toBe(false);
  });

  it('treats a missing gain as unity', () => {
    expect(voiceOverSage(frame([DUCK_VOICE_RMS, DUCK_VOICE_RMS]), Number.NaN)).toBe(true);
  });
});
