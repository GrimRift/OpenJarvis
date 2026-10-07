/**
 * Hearing the user over Sage's own voice, so the reply can make room.
 *
 * While Sage speaks through the speakers, the browser's echo cancellation
 * mutes whatever it cannot tell from the echo -- and the start of a word
 * said over the reply is exactly that. A "stop" reached Deepgram as 100 ms
 * of "-op" between digital zeros, transcribed as nothing at all, and the
 * user had to say it three or four times (7 October). Captured side by side:
 * with Sage at full level 0 of 3 "stop"s came through whole; with Sage at
 * 30 % 2 of 3 did. Echo cancellation itself has to stay: without it Sage's
 * reply reaches Deepgram word for word.
 *
 * So the chopped word is still worth something: it is loud, even when it is
 * short, and Sage's leak past echo cancellation is not. It turns the reply
 * down, and the next word the user says arrives whole.
 */

/** 20 ms at the 16 kHz the stream is cut to. */
export const DUCK_WINDOW_SAMPLES = 320;

/**
 * A slice this loud (after the automatic gain) is the user, not Sage.
 *
 * Measured on the echo-cancelled stream, NVIDIA Broadcast and the raw PD100X
 * alike: Sage's leak peaked at 1.1-1.8k per 20 ms, the user's chopped "stop"s
 * at 1.9-4.7k. Set on the high side of the gap -- the user asked for dips
 * to be rare.
 */
export const DUCK_VOICE_RMS = 2500;

/** Slices that must reach it in one frame: a click or a single loud
 * syllable of leak is one slice; the shortest chopped "stop" was three. */
export const DUCK_MIN_WINDOWS = 2;

/** How far the reply drops, of its own volume (the user's choice). */
export const DUCK_LEVEL = 0.3;

/** How long it stays down after the user's voice was last heard. */
export const DUCK_HOLD_MS = 1500;

/**
 * Whether this frame of the echo-cancelled microphone holds the user's voice.
 *
 * `autoGain` is the automatic gain alone, not the Settings boost: it brings
 * a quiet microphone up to the level a loud one arrives at, so one
 * threshold serves both, while the boost is a taste that must not make Sage
 * duck at its own echo.
 */
export function voiceOverSage(samples: Int16Array, autoGain: number): boolean {
  const gain = Number.isFinite(autoGain) && autoGain > 0 ? autoGain : 1;
  const floor = (DUCK_VOICE_RMS / gain) ** 2;
  let loud = 0;
  for (let start = 0; start + DUCK_WINDOW_SAMPLES <= samples.length; start += DUCK_WINDOW_SAMPLES) {
    let sum = 0;
    for (let i = start; i < start + DUCK_WINDOW_SAMPLES; i++) sum += samples[i] * samples[i];
    if (sum / DUCK_WINDOW_SAMPLES >= floor) {
      loud += 1;
      if (loud >= DUCK_MIN_WINDOWS) return true;
    }
  }
  return false;
}
