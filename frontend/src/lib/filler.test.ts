import { describe, expect, it } from 'vitest';
import {
  FILLER_AFTER_MS,
  FILLER_REPEAT_MS,
  fillerDue,
  initialFillerState,
  nextFillerCheckMs,
} from './filler';

describe('fillerDue', () => {
  const start = 1000;
  const fresh = (spoken = true) => initialFillerState(spoken, start);

  it('plays first once the wait has gone on, whatever Sage is doing', () => {
    expect(fillerDue(fresh(), start + FILLER_AFTER_MS - 1)).toBeNull();
    expect(fillerDue(fresh(), start + FILLER_AFTER_MS)).toBe('first');
  });

  it('plays again at the slower cadence while the silence lasts', () => {
    const played = { ...fresh(), lastFillerAt: start + FILLER_AFTER_MS };
    expect(fillerDue(played, start + FILLER_AFTER_MS + FILLER_REPEAT_MS - 1)).toBeNull();
    expect(fillerDue(played, start + FILLER_AFTER_MS + FILLER_REPEAT_MS)).toBe('again');
  });

  it('never on a turn that is not spoken', () => {
    expect(fillerDue(fresh(false), start + 60_000)).toBeNull();
  });

  it('never once words have started, or the turn has ended', () => {
    expect(fillerDue({ ...fresh(), contentStarted: true }, start + 60_000)).toBeNull();
    expect(fillerDue({ ...fresh(), ended: true }, start + 60_000)).toBeNull();
  });

  it('knows when to look next', () => {
    expect(nextFillerCheckMs(fresh(), start)).toBe(FILLER_AFTER_MS);
    expect(nextFillerCheckMs({ ...fresh(), lastFillerAt: start + 7000 }, start + 7000)).toBe(
      FILLER_REPEAT_MS,
    );
    expect(nextFillerCheckMs(fresh(), start + 60_000)).toBe(0);
  });
});
