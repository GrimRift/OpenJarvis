/**
 * Frame-rate-independent motion for the orb.
 *
 * The orb's constants were tuned per 60Hz frame and applied once per
 * requestAnimationFrame, so its speed was whatever the display happened to
 * run at — a 144Hz monitor spun it 2.4x faster than intended, and a busy tab
 * slower. Everything here works in "60Hz frames elapsed" so a second of wall
 * clock produces the same motion everywhere.
 */

/** Largest step applied in one frame, in 60Hz-frame units. */
export const MAX_FRAME_STEP = 3;

/**
 * Frames elapsed since the previous timestamp, normalised to 60Hz.
 *
 * Clamped so a backgrounded tab — where rAF stops firing entirely — eases
 * back in instead of applying a second of rotation on its first frame.
 */
export function frameDelta(now: number, previous: number): number {
  if (!Number.isFinite(now) || !Number.isFinite(previous)) return 1;
  const frames = (now - previous) / (1000 / 60);
  if (!(frames > 0)) return 0;
  return Math.min(frames, MAX_FRAME_STEP);
}

/**
 * Move a lerped value toward its target by *coef* per 60Hz frame.
 *
 * The coefficient is clamped at 1 so a long frame settles on the target
 * rather than overshooting past it and oscillating.
 */
export function approach(
  current: number,
  target: number,
  coef: number,
  dt: number,
): number {
  return current + (target - current) * Math.min(1, coef * dt);
}

/** Rotation-easing rate per 60Hz frame. */
export const SPEED_EASE = 0.05;

/**
 * Advance rotation, easing the rate toward its target rather than snapping.
 *
 * Idle to speaking is a 3.7x jump; applying it on a single frame reads as the
 * orb lurching rather than picking up speed.
 */
export function stepRotation(
  angle: number,
  speed: number,
  targetSpeed: number,
  dt: number,
): { angle: number; speed: number } {
  const next = approach(speed, targetSpeed, SPEED_EASE, dt);
  return { angle: angle + next * dt, speed: next };
}

/** Size the particle count was tuned at, and the count itself. */
export const PARTICLE_BASELINE_SIZE = 285;
export const PARTICLE_BASELINE_COUNT = 2200;

/**
 * Particle count that holds density constant as the orb grows.
 *
 * The count was a constant while the canvas was not, so enlarging the orb
 * 15% spread the same 2,200 particles over 32% more area and thinned it out.
 */
export function particleCountFor(size: number): number {
  if (!(size > 0)) return PARTICLE_BASELINE_COUNT;
  const scale = size / PARTICLE_BASELINE_SIZE;
  return Math.round(PARTICLE_BASELINE_COUNT * scale * scale);
}
