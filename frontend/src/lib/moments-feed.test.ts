import { describe, expect, it } from 'vitest';
import { newMoments } from './moments-feed';
import type { MomentRecord } from '../lib/api';

const rec = (at: number, text = 'Good morning, sir.'): MomentRecord => ({
  at,
  kind: 'greeting',
  text,
  spoken: true,
  detail: '',
});

describe('newMoments', () => {
  it('starts from now on a tab that has never seen anything', () => {
    const { fresh, seen } = newMoments([rec(10), rec(20)], null, 100);
    expect(fresh).toEqual([]);
    expect(seen).toBe(100);
  });

  it('returns only what is newer than the watermark, oldest first', () => {
    const { fresh, seen } = newMoments([rec(30), rec(10), rec(20)], 15, 100);
    expect(fresh.map((r) => r.at)).toEqual([20, 30]);
    expect(seen).toBe(30);
  });

  it('leaves out what was never said but still moves past it', () => {
    const { fresh, seen } = newMoments([rec(20, '(not said) your class starts')], 15, 100);
    expect(fresh).toEqual([]);
    expect(seen).toBe(20);
  });
});
