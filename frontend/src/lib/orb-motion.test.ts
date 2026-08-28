import { describe, expect, it } from 'vitest';
import { MAX_FRAME_STEP, approach, frameDelta, stepRotation } from './orb-motion';

const SPEAKING = 0.013;
const IDLE = 0.0035;

/** Rotate for *seconds* of wall clock at a given refresh rate. */
function rotateFor(seconds: number, hz: number, targetSpeed: number): number {
  const step = 1000 / hz;
  let angle = 0;
  let speed = targetSpeed;
  let previous = 0;
  for (let now = step; now <= seconds * 1000 + 1e-9; now += step) {
    const next = stepRotation(angle, speed, targetSpeed, frameDelta(now, previous));
    angle = next.angle;
    speed = next.speed;
    previous = now;
  }
  return angle;
}

describe('frameDelta', () => {
  it('is 1 for a 60Hz frame', () => {
    expect(frameDelta(1000 / 60, 0)).toBeCloseTo(1, 6);
  });

  it('is about half a frame at 144Hz', () => {
    expect(frameDelta(1000 / 144, 0)).toBeCloseTo(60 / 144, 6);
  });

  it('clamps a long gap so a backgrounded tab does not lurch', () => {
    expect(frameDelta(10_000, 0)).toBe(MAX_FRAME_STEP);
  });

  it('returns zero for a repeated timestamp', () => {
    expect(frameDelta(500, 500)).toBe(0);
  });

  it('never goes negative if timestamps arrive out of order', () => {
    expect(frameDelta(100, 200)).toBe(0);
  });
});

describe('rotation is frame-rate independent', () => {
  it('covers the same angle in a second at 60Hz and 144Hz', () => {
    // The reported bug: constants tuned per 60Hz frame were applied once per
    // rAF, so a 144Hz display spun the orb 2.4x faster.
    const at60 = rotateFor(1, 60, SPEAKING);
    const at144 = rotateFor(1, 144, SPEAKING);
    expect(at144).toBeCloseTo(at60, 3);
  });

  it('covers the same angle at 30Hz as at 60Hz', () => {
    expect(rotateFor(1, 30, SPEAKING)).toBeCloseTo(rotateFor(1, 60, SPEAKING), 3);
  });

  it('is roughly targetSpeed * 60 radians per second at steady state', () => {
    expect(rotateFor(1, 60, IDLE)).toBeCloseTo(IDLE * 60, 2);
  });

  it('still spins faster when speaking than when idle', () => {
    expect(rotateFor(1, 60, SPEAKING)).toBeGreaterThan(rotateFor(1, 60, IDLE));
  });
});

describe('stepRotation easing', () => {
  it('does not jump to the new rate on a single frame', () => {
    const { speed } = stepRotation(0, IDLE, SPEAKING, 1);
    expect(speed).toBeGreaterThan(IDLE);
    // One frame covers ~5% of the gap, nowhere near the whole 3.7x jump.
    expect(speed).toBeLessThan(IDLE + (SPEAKING - IDLE) * 0.1);
  });

  it('reaches the new rate given enough frames', () => {
    let speed = IDLE;
    for (let i = 0; i < 300; i++) speed = stepRotation(0, speed, SPEAKING, 1).speed;
    expect(speed).toBeCloseTo(SPEAKING, 5);
  });

  it('advances the angle by the eased rate, not the target', () => {
    const { angle, speed } = stepRotation(0, IDLE, SPEAKING, 1);
    expect(angle).toBeCloseTo(speed, 10);
  });
});

describe('approach', () => {
  it('moves toward the target proportionally to dt', () => {
    expect(approach(0, 1, 0.1, 1)).toBeCloseTo(0.1, 6);
    expect(approach(0, 1, 0.1, 2)).toBeCloseTo(0.2, 6);
  });

  it('never overshoots on a long frame', () => {
    // Without the clamp, coef * dt > 1 sails past the target and oscillates.
    expect(approach(0, 1, 0.5, MAX_FRAME_STEP)).toBe(1);
  });

  it('is a no-op when already at the target', () => {
    expect(approach(0.88, 0.88, 0.08, 1)).toBeCloseTo(0.88, 10);
  });
});
