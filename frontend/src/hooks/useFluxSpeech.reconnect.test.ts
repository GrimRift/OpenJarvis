import { describe, it, expect } from 'vitest';
import { MAX_FLUX_RECONNECTS, reconnectDelay } from './useFluxSpeech';

// A dropped Flux socket used to leave the page on local transcription until it
// was reloaded: nothing reconnected outside the `enabled` effect, so the toast
// "Cloud transcription unavailable - using local. Flux connection closed" was
// permanent for the session and the wake word never reached Flux again.
describe('reconnectDelay', () => {
  it('backs off between attempts', () => {
    expect(reconnectDelay(0)).toBe(500);
    expect(reconnectDelay(1)).toBe(1000);
    expect(reconnectDelay(2)).toBe(2000);
  });

  it('retries at least once, so one blip is not fatal', () => {
    expect(reconnectDelay(0)).not.toBeNull();
  });

  it('gives up rather than reconnecting forever', () => {
    expect(reconnectDelay(MAX_FLUX_RECONNECTS)).toBeNull();
    expect(reconnectDelay(MAX_FLUX_RECONNECTS + 5)).toBeNull();
  });
});
