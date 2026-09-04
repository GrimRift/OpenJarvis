import { describe, it, expect } from 'vitest';
import { carriesSound, MIN_FRAME_RMS } from './useWakeWord';

/**
 * The wake word fired while every microphone was muted at the laptop's
 * hardware key, most often while a video was playing, and heard nothing
 * afterwards.
 *
 * Measured against the real model, digital silence peaks at 0.15 and
 * plus/minus 2-LSB dither at 0.50, both well under the 0.79 threshold. A
 * genuinely silent frame cannot fire it, so something was reaching the
 * classifier that was not the room. Rather than depend on that diagnosis,
 * frames without real acoustic energy are no longer scored at all.
 */
function frame(fill: (i: number) => number, length = 1280): number[] {
  return Array.from({ length }, (_, i) => fill(i));
}

describe('only frames carrying real sound are scored', () => {
  it('a muted capture is silence and is never sent', () => {
    expect(carriesSound(frame(() => 0))).toBe(false);
  });

  it('the dither a muted device can still emit is not sound', () => {
    expect(carriesSound(frame((i) => (i % 2 ? 2 : -2)))).toBe(false);
  });

  it('speech at ordinary volume passes', () => {
    // A 300 Hz tone at roughly a quarter of full scale: far quieter than a
    // raised voice, far louder than a dead microphone.
    const speech = frame((i) => Math.round(8000 * Math.sin((i * 300 * 2 * Math.PI) / 16000)));
    expect(carriesSound(speech)).toBe(true);
  });

  it('a quiet room still reaches the classifier', () => {
    // Ordinary speech from across a room, well above the floor.
    const quiet = frame((i) => Math.round(400 * Math.sin((i * 220 * 2 * Math.PI) / 16000)));
    expect(carriesSound(quiet)).toBe(true);
  });

  it('the floor sits far below speech and far above a dead mic', () => {
    expect(MIN_FRAME_RMS).toBeGreaterThan(5);
    expect(MIN_FRAME_RMS).toBeLessThan(200);
  });
});
