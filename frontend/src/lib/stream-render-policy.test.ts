import { describe, expect, it } from 'vitest';
import {
  shouldFlushStreamRender,
  streamRenderIntervalMs,
} from './stream-render-policy';

describe('stream render throttling', () => {
  it('renders the first model delta immediately', () => {
    expect(shouldFlushStreamRender(1000, 0, 20)).toBe(true);
  });

  it('coalesces rapid token deltas instead of rendering every token', () => {
    expect(shouldFlushStreamRender(1040, 1000, 1000)).toBe(false);
    expect(shouldFlushStreamRender(1080, 1000, 1000)).toBe(true);
  });

  it('reduces render frequency as the Markdown document grows', () => {
    expect(streamRenderIntervalMs(1000)).toBe(80);
    expect(streamRenderIntervalMs(5000)).toBe(160);
    expect(streamRenderIntervalMs(12000)).toBe(300);
  });
});
