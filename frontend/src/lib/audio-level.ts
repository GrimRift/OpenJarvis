/**
 * Live loudness of whatever Sage is currently saying.
 *
 * Kept as a module-level value rather than store state on purpose: the orb
 * samples it once per animation frame, and pushing 60 updates a second through
 * React would re-render the page for something only a canvas consumes.
 */

let level = 0;

/** Latest smoothed level, 0..1. */
export function getSpeechLevel(): number {
  return level;
}

export function setSpeechLevel(next: number): void {
  level = Number.isFinite(next) ? Math.min(1, Math.max(0, next)) : 0;
}

/** Called when playback stops, so the orb settles instead of freezing lit. */
export function resetSpeechLevel(): void {
  level = 0;
}

/**
 * RMS of a time-domain byte buffer, scaled so ordinary speech lands near 1.
 *
 * AnalyserNode centres silence at 128. RMS rather than peak because peak
 * tracks single plosives and reads as twitching; RMS follows syllables.
 */
export function rmsFromTimeDomain(data: Uint8Array): number {
  if (data.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < data.length; i++) {
    const centred = (data[i] - 128) / 128;
    sum += centred * centred;
  }
  const rms = Math.sqrt(sum / data.length);
  // Speech RMS sits well below 1.0; this maps a normal speaking level onto
  // most of the range so the orb actually moves.
  return Math.min(1, rms * 3.2);
}

/** Attack and release per 60Hz frame — rises quickly, falls away gently. */
export const ATTACK = 0.45;
export const RELEASE = 0.12;

/**
 * Move the displayed level toward the measured one.
 *
 * Asymmetric on purpose: matching the rise makes speech feel responsive, while
 * a slower fall keeps the orb from strobing between syllables.
 */
export function smoothLevel(
  current: number,
  target: number,
  dt = 1,
  attack = ATTACK,
  release = RELEASE,
): number {
  const coef = target > current ? attack : release;
  return current + (target - current) * Math.min(1, coef * dt);
}
