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
import { SPEAKING_MAX_GAIN } from './mic-gain';

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

/**
 * No dip this soon after Sage starts speaking. Echo cancellation is still
 * learning the room then: a quiet reply's leak was 40-53 per slice
 * throughout, except 1,933 inside its first 2 s (13 October), and on 12
 * October the unwanted dips came within a second of a reply starting. A
 * "stop" said then still stops Sage -- that is decided from the words --
 * it only isn't helped through.
 */
export const DUCK_SETTLE_MS = 1500;

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
  return sliceLevels(samples, autoGain).loud >= DUCK_MIN_WINDOWS;
}

/**
 * The frame's 20 ms slices as the duck sees them: the loudest one's RMS
 * after the automatic gain, and how many reach DUCK_VOICE_RMS. Traced while
 * Sage speaks, so the leak can be read off a reply instead of guessed (the
 * mic got 20 dB louder after the threshold was measured).
 *
 * The gain stops at SPEAKING_MAX_GAIN, as the audio sent while Sage speaks
 * does. Uncapped, a quiet question raised it to 6.8 and multiplied Sage's
 * own leak with it: unchanged at ~47 per slice raw, its loudest syllables
 * read 2,644-2,897 and the reply dipped at nothing (13 October).
 */
export function sliceLevels(samples: Int16Array, autoGain: number): { peak: number; loud: number } {
  const gain = Number.isFinite(autoGain) && autoGain > 0 ? Math.min(autoGain, SPEAKING_MAX_GAIN) : 1;
  const floor = (DUCK_VOICE_RMS / gain) ** 2;
  let loud = 0;
  let peak = 0;
  for (let start = 0; start + DUCK_WINDOW_SAMPLES <= samples.length; start += DUCK_WINDOW_SAMPLES) {
    let sum = 0;
    for (let i = start; i < start + DUCK_WINDOW_SAMPLES; i++) sum += samples[i] * samples[i];
    const mean = sum / DUCK_WINDOW_SAMPLES;
    if (mean >= floor) loud += 1;
    if (mean > peak) peak = mean;
  }
  return { peak: Math.sqrt(peak) * gain, loud };
}
