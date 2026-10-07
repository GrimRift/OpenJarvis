/**
 * Making a quiet microphone loud enough for Deepgram.
 *
 * The conversation stream opens the microphone with `autoGainControl: false`
 * and sends the samples on untouched. At a desk mic that is clean raw audio;
 * on a laptop's built-in mic at arm's length it is simply too quiet, and the
 * user had to shout or lean into the screen to be heard after the wake word.
 *
 * Browser AGC was the other option and was not taken: it rides the level up
 * during pauses, so room noise and the fan swell into the gaps, and it adapts
 * on its own schedule. This is the same idea with the decisions visible --
 * aim at a target level, never amplify a silent room, move down fast and up
 * slowly, and stop at a ceiling.
 */

/** The level speech should arrive at, on the int16 scale Deepgram is fed. */
export const TARGET_RMS = 2200;

/** Never amplify past this, however quiet the speaker. */
export const MAX_GAIN = 8;

/** Below this the frame is room tone, not speech: hold the gain where it is
 * rather than winding it up until the fan is a voice. */
export const SILENCE_RMS = 120;

/** A gentler ceiling for the wake word. Its threshold was tuned on
 * unboosted audio, so a big boost buys range at the cost of false fires. */
export const WAKE_MAX_GAIN = 3;

/** Everything above the automatic gain, from the Settings slider. */
export const MAX_TOTAL_GAIN = 16;

/** The wake word's ceiling with the Settings slider at its top (4x). */
export const WAKE_MAX_TOTAL_GAIN = 12;

/**
 * How far the wake word's automatic gain may climb, given the Settings
 * "Extra boost".
 *
 * A raised ceiling, not a multiplier: the automatic gain still backs off on
 * loud speech, so a desk mic is untouched and only a quiet one gets more.
 * Multiplying the frames instead clipped loud takes into rejections when it
 * was tried on recorded clips (6 October). Needed because a dynamic USB mic
 * (PD100X, 6 October) reached the detector at a 0.016-0.05 peak RMS even at
 * 3x, under the verifier's QUIET_MUFFLED_RMS, so every "Hey Sage" it heard as
 * muffled was dropped as a faint sound. Replayed through the real verifier,
 * those takes passed from 2x up; no take that held no words did, at any boost.
 */
export function wakeGainCeiling(boost: number): number {
  const extra = Number.isFinite(boost) && boost > 1 ? boost : 1;
  return clamp(WAKE_MAX_GAIN * extra, WAKE_MAX_GAIN, WAKE_MAX_TOTAL_GAIN);
}

/**
 * The most the microphone is boosted while Sage's own voice is playing.
 *
 * Gain cannot tell the user from Sage leaking through the speakers -- it
 * lifts both -- so this is a compromise with two failures on either side.
 * At full gain Sage's own reply came back transcribed at 0.96-1.00
 * confidence and barge-in cut the answer as if someone had spoken; at unity
 * nothing amplifies the user either, and interrupting from a normal distance
 * stopped working. Three carries a voice across a desk while leaving the
 * leakage far below where the recogniser commits to words.
 */
export const SPEAKING_MAX_GAIN = 3;

const WAKE_GAIN_KEY = 'sage-wake-gain:';

/**
 * The wake word's gain as last settled on this microphone, or null.
 *
 * It started at 1x on every page load and, on a quiet raw mic (PD100X,
 * speech ~100-300 RMS), climbs only on the few frames over SILENCE_RMS:
 * still 1.9x of 6x four minutes after a reload (8 October), while the
 * user's "Hey Sage" reached the detector at RMS ~50. Remembered per mic,
 * not started at the ceiling: a loud mic's first "Hey Sage" at the ceiling
 * would clip before the gain came down.
 */
export function loadWakeGain(micLabel: string): number | null {
  if (!micLabel) return null;
  try {
    const value = Number(localStorage.getItem(WAKE_GAIN_KEY + micLabel));
    return Number.isFinite(value) && value >= 1 ? value : null;
  } catch {
    return null;
  }
}

export function saveWakeGain(micLabel: string, gain: number): void {
  if (!micLabel || !Number.isFinite(gain)) return;
  try {
    localStorage.setItem(WAKE_GAIN_KEY + micLabel, gain.toFixed(2));
  } catch {
    // Storage blocked: the gain just starts at 1x next time.
  }
}

/** Coming down is urgent (it prevents clipping); going up is not. */
const FALL = 0.34;
const RISE = 0.06;

export interface GainLimits {
  target?: number;
  max?: number;
  silence?: number;
}

/**
 * The gain to use for the next frame, given the one just heard.
 *
 * Smoothed rather than snapped: a gain that jumps frame to frame is audible
 * as pumping and gives the recogniser a moving target.
 */
export function nextGain(current: number, rms: number, limits: GainLimits = {}): number {
  const target = limits.target ?? TARGET_RMS;
  const max = limits.max ?? MAX_GAIN;
  const silence = limits.silence ?? SILENCE_RMS;

  if (!Number.isFinite(current) || current <= 0) current = 1;
  // Silence carries no information about how loud the speaker is.
  if (!Number.isFinite(rms) || rms < silence) return clamp(current, 1, max);

  const wanted = clamp(target / rms, 1, max);
  const step = wanted < current ? FALL : RISE;
  return clamp(current + (wanted - current) * step, 1, max);
}

/**
 * Scale a frame, clipping at full scale.
 *
 * Returns the same array when there is nothing to do, so the common
 * already-loud-enough case costs nothing.
 */
export function applyGain(samples: Int16Array, gain: number): Int16Array {
  if (!Number.isFinite(gain) || gain <= 1.001) return samples;
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const scaled = Math.round(samples[i] * gain);
    out[i] = scaled > 32767 ? 32767 : scaled < -32768 ? -32768 : scaled;
  }
  return out;
}

/** The automatic gain and the user's slider, together and capped. */
export function totalGain(automatic: number, manual: number): number {
  const auto = Number.isFinite(automatic) && automatic > 0 ? automatic : 1;
  const hand = Number.isFinite(manual) && manual > 0 ? manual : 1;
  return clamp(auto * hand, 1, MAX_TOTAL_GAIN);
}

function clamp(value: number, low: number, high: number): number {
  return value < low ? low : value > high ? high : value;
}

/**
 * The level a frame must reach to count as someone speaking.
 *
 * Compared against RAW frames, so the room level needs no gain applied. The
 * FLOOR does: it was chosen for desk-mic levels, and on a boosted quiet
 * microphone it sat above the very speech it exists to admit. Scaling the
 * ambient term instead pinned the threshold at the ceiling -- measured
 * `speechRms=6000` on every turn, against boosted speech of about 2200, so
 * Sage heard a permanently silent room after the wake word.
 */
export function speechThreshold(
  ambientRms: number,
  gain: number,
  floor: number,
  ceiling: number,
): number {
  const ambient = Number.isFinite(ambientRms) && ambientRms > 0 ? ambientRms : 0;
  const applied = Number.isFinite(gain) && gain > 1 ? gain : 1;
  return Math.min(ceiling, Math.max(floor / applied, ambient * 4));
}

/**
 * A running estimate of the room's own noise, from the frames themselves.
 *
 * Measured on this machine: the laptop's fan sits inches from the built-in
 * microphone and holds a steady RMS of 433 with nobody speaking, where a desk
 * mic across the room reads near zero. Any fixed idea of "quiet" is therefore
 * wrong on one machine or the other, and a floor imported from a DIFFERENT
 * audio stream (the wake word's, which has no noise suppression) is wrong
 * whenever the two streams are processed differently.
 *
 * Falls quickly and rises slowly, so a pause in speech pulls the estimate
 * down to the true floor while a long sentence cannot drag it up to speech.
 */
export function trackNoise(current: number, rms: number): number {
  if (!Number.isFinite(rms) || rms < 0) return current;
  if (!Number.isFinite(current) || current <= 0) return rms;
  const rate = rms < current ? 0.2 : 0.004;
  return current + (rms - current) * rate;
}

/** Frames merely at room level must not drive the gain (the fan is not a
 * voice), so the adapt guard sits just above the measured floor. */
export function adaptFloor(noise: number): number {
  const room = Number.isFinite(noise) && noise > 0 ? noise : 0;
  return Math.max(SILENCE_RMS, room * 1.6);
}
