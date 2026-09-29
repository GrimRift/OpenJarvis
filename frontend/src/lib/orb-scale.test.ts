import { describe, expect, it } from 'vitest';
import { orbScaleFor } from './orb-scale';

describe('orbScaleFor', () => {
  it('is full size at the window the app opens with', () => {
    expect(orbScaleFor(1419, 866)).toBe(1);
  });

  it('never grows past full size in a larger window', () => {
    expect(orbScaleFor(1920, 1040)).toBe(1);
  });

  it('follows whichever side shrank more', () => {
    expect(orbScaleFor(1419, 433)).toBeCloseTo(0.5);
    expect(orbScaleFor(709.5, 866)).toBeCloseTo(0.5);
    expect(orbScaleFor(1000, 700)).toBeCloseTo(Math.min(1000 / 1419, 700 / 866));
  });

  it('stops at a floor in a tiny window and ignores a zero size', () => {
    expect(orbScaleFor(200, 150)).toBe(0.3);
    expect(orbScaleFor(0, 0)).toBe(1);
  });
});
