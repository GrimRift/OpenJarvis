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
