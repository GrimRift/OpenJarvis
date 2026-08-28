import { describe, expect, it } from 'vitest';
import { protectCurrencyFromMath } from './currency-math';

describe('protectCurrencyFromMath', () => {
  it('escapes the two amounts that rendered as run-together math', () => {
    // The live transcript: remark-math paired these and KaTeX ate the spaces,
    // producing "200invoiceAIcreditswereactivated...1".
    const input =
      'Your $200 invoice for AI credits was activated, and $1 was refunded.';
    const out = protectCurrencyFromMath(input);

    expect(out).toBe(
      'Your \\$200 invoice for AI credits was activated, and \\$1 was refunded.',
    );
  });

  it('escapes amounts with thousands separators and decimals', () => {
    expect(protectCurrencyFromMath('It cost $1,234.56 total.')).toBe(
      'It cost \\$1,234.56 total.',
    );
  });

  it('leaves inline math alone', () => {
    const math = 'Given $x^2$ and $\\alpha$, solve.';
    expect(protectCurrencyFromMath(math)).toBe(math);
  });

  it('leaves display math alone', () => {
    const math = 'See $$\\frac{1}{2}$$ above.';
    expect(protectCurrencyFromMath(math)).toBe(math);
  });

  it('leaves math that merely starts with a digit alone', () => {
    // "$2x" is a digit followed by a word character, so it reads as math.
    const math = 'Let $2x + 1$ be the term.';
    expect(protectCurrencyFromMath(math)).toBe(math);
  });

  it('does not escape inside inline code', () => {
    const input = 'Run `echo $5` first.';
    expect(protectCurrencyFromMath(input)).toBe(input);
  });

  it('does not escape inside fenced code blocks', () => {
    const input = 'Before\n\n```sh\nPRICE=$200\n```\n\nafter $9 though.';
    expect(protectCurrencyFromMath(input)).toBe(
      'Before\n\n```sh\nPRICE=$200\n```\n\nafter \\$9 though.',
    );
  });

  it('does not double-escape an already-escaped amount', () => {
    expect(protectCurrencyFromMath('costs \\$5')).toBe('costs \\$5');
  });

  it('returns text without a dollar sign untouched', () => {
    const input = 'No money here at all.';
    expect(protectCurrencyFromMath(input)).toBe(input);
  });
});
