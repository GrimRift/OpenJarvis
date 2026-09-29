import type { TokenUsage } from '../types';

function formatCount(n: number): string {
  return n < 1000 ? `${n}` : `${(n / 1000).toFixed(n < 10000 ? 1 : 0)}k`;
}

/**
 * Input tokens as "new · cached". The total adds up every model round, each
 * resending the whole conversation, so a search answer read "137203 input
 * tokens" when 127,800 of them were cached (29 September).
 */
export function inputTokensLabel(usage: TokenUsage): string {
  const cached = usage.cached_tokens ?? 0;
  if (cached <= 0) return `${usage.prompt_tokens} input tokens`;
  const fresh = Math.max(0, usage.prompt_tokens - cached);
  return `${formatCount(fresh)} new · ${formatCount(cached)} cached input tokens`;
}
