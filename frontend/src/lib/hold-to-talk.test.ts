import { describe, expect, it } from 'vitest';
import { CANCEL_SLIDE_PX, HOLD_AFTER_MS, joinHeld, pressEnd, slidingToCancel } from './hold-to-talk';

describe('pressEnd', () => {
  it('a quick press is a tap, wherever the finger went', () => {
    expect(pressEnd(100, 0)).toBe('tap');
    expect(pressEnd(HOLD_AFTER_MS - 1, CANCEL_SLIDE_PX * 2)).toBe('tap');
  });

  it('a hold released in place sends', () => {
    expect(pressEnd(HOLD_AFTER_MS, 0)).toBe('send');
    expect(pressEnd(4000, CANCEL_SLIDE_PX - 1)).toBe('send');
    // Sliding down is not cancelling.
    expect(pressEnd(4000, -200)).toBe('send');
  });

  it('a hold slid up far enough cancels', () => {
    expect(pressEnd(4000, CANCEL_SLIDE_PX)).toBe('cancel');
    expect(slidingToCancel(CANCEL_SLIDE_PX)).toBe(true);
    expect(slidingToCancel(CANCEL_SLIDE_PX - 1)).toBe(false);
  });
});

describe('joinHeld', () => {
  it('joins every piece heard while held, in order', () => {
    expect(joinHeld(['remind me to call Scharlab', ' at nine '], 'tomorrow')).toBe(
      'remind me to call Scharlab at nine tomorrow',
    );
  });

  it('skips empty pieces and works with nothing still coming', () => {
    expect(joinHeld(['', 'hello', '  '])).toBe('hello');
    expect(joinHeld([])).toBe('');
  });
});
