import { describe, expect, it } from 'vitest';
import { retryUntilAnswered } from './retry';

/** A clock that only moves when the retry sleeps. */
function fakeClock() {
  let t = 0;
  return { now: () => t, sleep: async (ms: number) => { t += ms; } };
}

describe('retryUntilAnswered', () => {
  it('keeps asking while the server is still starting, then returns its answer', async () => {
    // 29 September: the page asked once during the server's ~20 s start and
    // showed "Select model" until reloaded by hand.
    const clock = fakeClock();
    let calls = 0;
    const models = await retryUntilAnswered(async () => {
      calls += 1;
      if (clock.now() < 20_000) throw new Error('connect ECONNREFUSED');
      return ['gpt-5.6-luna'];
    }, clock);
    expect(models).toEqual(['gpt-5.6-luna']);
    expect(calls).toBe(11);
  });

  it('answers at once when the server is up', async () => {
    const clock = fakeClock();
    expect(await retryUntilAnswered(async () => 'ok', clock)).toBe('ok');
    expect(clock.now()).toBe(0);
  });

  it('gives up with the last error after the time limit, as before', async () => {
    const clock = fakeClock();
    await expect(
      retryUntilAnswered(async () => { throw new Error('still down'); }, { ...clock, timeoutMs: 10_000 }),
    ).rejects.toThrow('still down');
    expect(clock.now()).toBeLessThanOrEqual(10_000);
  });

  it('stops quietly once cancelled', async () => {
    const clock = fakeClock();
    let calls = 0;
    const result = await retryUntilAnswered(async () => { calls += 1; throw new Error('down'); }, {
      ...clock,
      cancelled: () => calls >= 2,
    });
    expect(result).toBeUndefined();
    expect(calls).toBe(2);
  });
});
