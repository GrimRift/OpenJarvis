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
 * How loud the voice has to be to fill the orb's range: level = RMS x this.
 *
 * The same number scales the server's voice (SPEECH_LEVEL_SCALE in
 * speech/player.py), so a reminder the server speaks moves the orb exactly
 * as a chat reply at the same loudness does; an architecture test holds the
 * two equal.
 *
 * It was 8, set for the cloud voice at its 1.9 volume and measured after the
 * volume slider. The local voice is louder: replayed through the page's own
 * chain, real replies sat at a median level of 0.83 with the top tenth
 * pinned at 1.0 -- no headroom, so a louder syllable could not show -- and
 * the server's reminders, scaled differently, sat at 0.50. Measured on the
 * raw voice (see speech-analyser.ts), five real lines in the chosen voice
 * give an RMS median of 0.134 and p90 of 0.289; at 3.5 that is a median
 * level of 0.60 and a p90 of 0.90, on both paths.
 */
export const SPEECH_LEVEL_SCALE = 3.5;

/**
 * How the orb reads the voice speaking now: level = min(1, RMS x scale x
 * gain) ** contrast. Neutral (1, 1) for Nano Jarvis, the voice every other
 * one is fitted against: Turbo speaks more smoothly, with shallower dips
 * between syllables, and its orb "moved too little" (24 September). The pair
 * comes from the voice's meta.json; speech/player.py applies the same one
 * to what the server speaks.
 */
export interface OrbShaping {
  gain: number;
  contrast: number;
  /** How fast the speaking orb draws in after a syllable, per 60Hz frame.
   * Nano Jarvis pauses crisply between phrases and its orb breathes at
   * 0.035; Frieren on Turbo runs her words together, never dipped long
   * enough for that slow release to show, and held one size ("mostly didn't
   * move", 25 September). A faster release lets the size follow her
   * syllables instead of only her pauses. */
  release: number;
  /** How hard a syllable's onset lights the patches and swells the body:
   * Turbo's onsets are softer than Nano's (mean rise 0.16 against 0.19). */
  kick: number;
}
export const ORB_NEUTRAL: OrbShaping = { gain: 1, contrast: 1, release: 0.035, kick: 1 };
let shaping: OrbShaping = ORB_NEUTRAL;

export function setOrbShaping(next?: Partial<OrbShaping> | null): void {
  const clamp = (v: unknown, fallback: number, low = 0.5, high = 3) =>
    typeof v === 'number' && Number.isFinite(v) ? Math.min(high, Math.max(low, v)) : fallback;
  shaping = {
    gain: clamp(next?.gain, 1),
    contrast: clamp(next?.contrast, 1),
    release: clamp(next?.release, ORB_NEUTRAL.release, 0.02, 0.2),
    kick: clamp(next?.kick, 1),
  };
}

export function getOrbShaping(): OrbShaping {
  return shaping;
}

/**
 * RMS of a time-domain byte buffer, scaled by SPEECH_LEVEL_SCALE and shaped
 * for the voice speaking (setOrbShaping).
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
  return Math.min(1, rms * SPEECH_LEVEL_SCALE * shaping.gain) ** shaping.contrast;
}

/** Attack and release per 60Hz frame — articulate syllables without jitter. */
export const ATTACK = 0.65;
export const RELEASE = 0.22;

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

// Module-level state (the level, the shaping) read by the orb and written
// by the analyser: a hot-swapped copy splits them, so reload instead.
if (import.meta.hot) import.meta.hot.accept(() => window.location.reload());
