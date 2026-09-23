import { beforeAll, describe, expect, it } from 'vitest';

beforeAll(() => {
  // The store reads localStorage as it is created.
  const memory = new Map<string, string>();
  (globalThis as unknown as { localStorage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> }).localStorage = {
    getItem: (key) => memory.get(key) ?? null,
    setItem: (key, value) => void memory.set(key, String(value)),
    removeItem: (key) => void memory.delete(key),
  };
});

describe('the reply voice', () => {
  it('is one player for the whole app, so a page change cannot end it', async () => {
    // Chat and Voice each render their own message box. With a player per
    // box, switching pages unmounted one and closed its audio mid-sentence.
    const { streamingTtsPlayer, useStreamingTts } = await import('./useStreamingTts');
    expect(useStreamingTts()).toBe(useStreamingTts());
    expect(useStreamingTts()).toBe(streamingTtsPlayer());
  });
});
