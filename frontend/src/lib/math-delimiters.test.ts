import { describe, expect, it } from 'vitest';
import { normaliseMathDelimiters as n } from './math-delimiters';

describe('LaTeX delimiters the model writes become ones remark-math reads', () => {
  it('converts display and inline TeX delimiters', () => {
    expect(n('Equilibrium: \\[ \\sum F_x=0 \\]')).toBe('Equilibrium: $$\\sum F_x=0$$');
    expect(n('from \\( 0^\\circ \\) to \\( 360^\\circ \\)')).toBe('from $0^\\circ$ to $360^\\circ$');
  });

  it('recovers brackets whose backslash markdown already ate', () => {
    // 22 September: the chat showed "[ \sum F_x=0,\quad \sum F_y=0 ]" verbatim.
    expect(n('Moment: [ M=Fd\\perp ]')).toBe('Moment: $$M=Fd\\perp$$');
    expect(n('north from (0^\\circ) to (360^\\circ).')).toBe('north from $0^\\circ$ to $360^\\circ$.');
  });

  it('leaves ordinary brackets, parentheses and code alone', () => {
    expect(n('see [the docs] and (maybe) more')).toBe('see [the docs] and (maybe) more');
    expect(n('`\\[ raw \\]` stays')).toBe('`\\[ raw \\]` stays');
    expect(n('no math here')).toBe('no math here');
  });
});
