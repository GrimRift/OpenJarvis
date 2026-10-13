import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, it, expect } from 'vitest';
import { MAX_FLUX_RECONNECTS, reconnectDelay, shouldRetryFlux } from './useFluxSpeech';

// 13 October: a server restart (~25 s) outlasted the quick reconnects
// (3.5 s), and "Hey Sage" then opened turns into no socket until the mic was
// switched off and on.
describe('after the quick reconnects gave up', () => {
  const gaveUp = { enabled: true, hasSocket: false, status: 'error' as const };

  it('starts over', () => {
    expect(shouldRetryFlux(gaveUp)).toBe(true);
  });

  it('not while a socket exists or Flux is off', () => {
    expect(shouldRetryFlux({ ...gaveUp, hasSocket: true })).toBe(false);
    expect(shouldRetryFlux({ ...gaveUp, enabled: false })).toBe(false);
  });

  it("never against the server's verdict, nor while connecting", () => {
    expect(shouldRetryFlux({ ...gaveUp, status: 'unavailable' })).toBe(false);
    expect(shouldRetryFlux({ ...gaveUp, status: 'connecting' })).toBe(false);
    expect(shouldRetryFlux({ ...gaveUp, status: 'connected' })).toBe(false);
  });

  it('tries when the server is back and when a turn has no socket', () => {
    const hooks = join(__dirname);
    const flux = readFileSync(join(hooks, 'useFluxSpeech.ts'), 'utf8');
    expect(flux).toMatch(/addEventListener\(SERVER_BACK_EVENT/);
    expect(flux).toMatch(/if \(!wsRef\.current\) retryNow\(/);
    const voice = readFileSync(join(hooks, 'useServerVoice.ts'), 'utf8');
    expect(voice).toMatch(
      /voiceTrace\('serverVoice\.connected', \{\}\);\s*window\.dispatchEvent\(new Event\(SERVER_BACK_EVENT\)\)/,
    );
  });
});

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

