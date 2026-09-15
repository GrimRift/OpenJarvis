import { describe, expect, it } from 'vitest';
import { newMoments, replyWindowFor } from './moments-feed';
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

describe('replyWindowFor', () => {
  const spoken = (kind: string, endedAgoMs: number, detail = ''): MomentRecord => ({
    at: 100,
    kind: kind as MomentRecord['kind'],
    text: 'x',
    spoken: true,
    detail,
    ended_at: (50_000 - endedAgoMs) / 1000,
  });

  it('opens for a fresh question or greeting', () => {
    expect(replyWindowFor([spoken('initiative', 3000)], 50_000)).toBe(47_000);
    expect(replyWindowFor([spoken('greeting', 3000)], 50_000)).toBe(47_000);
  });

  it('not for a tell-me-when, a follow-up, or something said a while ago', () => {
    expect(replyWindowFor([spoken('told', 3000)], 50_000)).toBeNull();
    expect(replyWindowFor([spoken('initiative', 3000, 'follow-up')], 50_000)).toBeNull();
    expect(replyWindowFor([spoken('initiative', 30_000)], 50_000)).toBeNull();
  });

  it('not for something that was never spoken', () => {
    expect(replyWindowFor([{ ...spoken('initiative', 1000), spoken: false }], 50_000)).toBeNull();
  });
});
