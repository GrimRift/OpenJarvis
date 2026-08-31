import { describe, expect, it } from 'vitest';
import {
  shouldRestartWakeWord,
  wakeWordSessionOwnsSocket,
} from './useWakeWord';

describe('wake-word liveness', () => {
  it('restarts an open detector that stopped receiving server responses', () => {
    expect(
      shouldRestartWakeWord({
        now: 25_001,
        lastResponseAt: 10_000,
        socketOpen: true,
      }),
    ).toBe(true);
  });

  it('does not restart a healthy or already-closed detector', () => {
    expect(
      shouldRestartWakeWord({
        now: 20_000,
        lastResponseAt: 10_000,
        socketOpen: true,
      }),
    ).toBe(false);
    expect(
      shouldRestartWakeWord({
        now: 30_000,
        lastResponseAt: 10_000,
        socketOpen: false,
      }),
    ).toBe(false);
  });

  it('rejects callbacks from a socket belonging to an older rearm session', () => {
    expect(wakeWordSessionOwnsSocket(8, 7, true)).toBe(false);
    expect(wakeWordSessionOwnsSocket(8, 8, false)).toBe(false);
    expect(wakeWordSessionOwnsSocket(8, 8, true)).toBe(true);
  });
});
