import { describe, expect, it } from 'vitest';

import { computeSpeechThreshold } from './useSpeech';

describe('computeSpeechThreshold', () => {
  it('floors a near-silent mic instead of collapsing to zero', () => {
    // A dead-silent room reporting noiseFloor 0 must not produce a
    // threshold so low that the faintest breath counts as speech.
    expect(computeSpeechThreshold(0)).toBe(0.02);
  });

  it('scales with a real ambient noise floor', () => {
    expect(computeSpeechThreshold(0.01)).toBeCloseTo(0.03, 5);
  });

  it('caps a threshold that would otherwise exceed real speech levels', () => {
    // Talking through the calibration window (no pause after "Hey Sage")
    // must degrade to the fixed-threshold ceiling, not to a threshold
    // speech can never clear.
    expect(computeSpeechThreshold(1)).toBe(0.15);
  });

  it('never returns a threshold outside [min, max] for any input', () => {
    for (const noiseFloor of [-1, 0, 0.005, 0.02, 0.1, 5]) {
      const threshold = computeSpeechThreshold(noiseFloor);
      expect(threshold).toBeGreaterThanOrEqual(0.02);
      expect(threshold).toBeLessThanOrEqual(0.15);
    }
  });
});
