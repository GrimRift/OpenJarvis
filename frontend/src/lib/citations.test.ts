import { describe, expect, it } from 'vitest';
import { cleanCitations } from './citations';

describe('cleanCitations', () => {
  it('turns a leaked URL citation into a source link', () => {
    const raw =
      'about 6% faster. citehttps://www.xda-developers.com/rtx-5060-vs-rx-9060-xt\n\nChoose';
    expect(cleanCitations(raw)).toBe(
      'about 6% faster.  ([xda-developers.com](https://www.xda-developers.com/rtx-5060-vs-rx-9060-xt))\n\nChoose',
    );
  });

  it('drops an internal turn id', () => {
    expect(cleanCitations('Done.citeturn0search0')).toBe('Done.');
  });

  it('hides a marker that is still streaming in', () => {
    expect(cleanCitations('Faster. citehttps://exa')).toBe('Faster. ');
  });

  it('leaves ordinary text alone', () => {
    expect(cleanCitations('Plain answer, ₱9,490.')).toBe('Plain answer, ₱9,490.');
  });
});
