import { describe, expect, it } from 'vitest';
import { FILLER_AFTER_MS, fillerDue, initialFillerState } from './filler';

describe('fillerDue', () => {
  const started = (spoken = true) => ({ ...initialFillerState(spoken), toolStartedAt: 1000 });

  it('plays once a tool has run for the wait, on a spoken turn', () => {
    expect(fillerDue(started(), 1000 + FILLER_AFTER_MS - 1)).toBe(false);
    expect(fillerDue(started(), 1000 + FILLER_AFTER_MS)).toBe(true);
  });

  it('never on a turn that is not spoken', () => {
    expect(fillerDue(started(false), 10_000)).toBe(false);
  });

  it('never once the answer has started, ended, or already played', () => {
    expect(fillerDue({ ...started(), contentStarted: true }, 10_000)).toBe(false);
    expect(fillerDue({ ...started(), ended: true }, 10_000)).toBe(false);
    expect(fillerDue({ ...started(), played: true }, 10_000)).toBe(false);
  });

  it('never without a tool call', () => {
    expect(fillerDue(initialFillerState(true), 10_000)).toBe(false);
  });
});
