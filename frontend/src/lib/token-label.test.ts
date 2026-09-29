import { describe, expect, it } from 'vitest';
import { inputTokensLabel } from './token-label';

describe('inputTokensLabel', () => {
  it('splits a many-round total into new and cached', () => {
    expect(
      inputTokensLabel({
        prompt_tokens: 137203,
        completion_tokens: 686,
        total_tokens: 137889,
        cached_tokens: 127800,
      }),
    ).toBe('9.4k new · 128k cached input tokens');
  });

  it('keeps the plain count when nothing was cached or it is unknown', () => {
    expect(
      inputTokensLabel({ prompt_tokens: 529, completion_tokens: 10, total_tokens: 539 }),
    ).toBe('529 input tokens');
  });
});
