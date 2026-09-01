import { describe, expect, it } from 'vitest';
import {
  shouldRestartWakeWord,
  wakeWordSessionOwnsSocket,
} from './useWakeWord';

describe('wake-word liveness', () => {
  it('restarts an open detector that stopped receiving server responses', () => {
    expect(
      shouldRestartWakeWord({ now: 25_001, lastResponseAt: 10_000 }),
    ).toBe(true);
  });

  it('leaves a detector alone while responses are still arriving', () => {
    expect(
      shouldRestartWakeWord({ now: 20_000, lastResponseAt: 10_000 }),
    ).toBe(false);
  });

  it('restarts when there is no live socket at all', () => {
    /**
     * The bug behind "after the morning digest the wake word never came
     * back". The watchdog used to require an *open* socket, so the one state
     * it could not recover from was having no socket — which is precisely the
     * dead end reachable here.
     *
     * `onclose` only schedules its retry when the closing socket still
     * belongs to the current session. Every flip of `enabled` bumps that
     * session, and `audioPlaying` flips twice around the digest's handover
     * from streaming TTS to the batch AudioPlayer. A close landing across one
     * of those flips is disowned, its reconnect is dropped, and nothing else
     * ever calls connectSocket again: no socket, no error, no recovery.
     */
    expect(
      shouldRestartWakeWord({ now: 25_001, lastResponseAt: 10_000 }),
    ).toBe(true);
  });

  it('does not retry a permanent failure', () => {
    /**
     * Mic permission denied, or the server refusing with 1008/1011. Retrying
     * those on a 3s watchdog is a loop that re-asks the browser for a device
     * it has already been told it cannot have; the error is surfaced instead.
     */
    expect(
      shouldRestartWakeWord({ now: 999_999, lastResponseAt: 0, fatal: true }),
    ).toBe(false);
  });

  it('rejects callbacks from a socket belonging to an older rearm session', () => {
    expect(wakeWordSessionOwnsSocket(8, 7, true)).toBe(false);
    expect(wakeWordSessionOwnsSocket(8, 8, false)).toBe(false);
    expect(wakeWordSessionOwnsSocket(8, 8, true)).toBe(true);
  });
});
