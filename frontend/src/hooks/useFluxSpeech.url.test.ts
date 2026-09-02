import { describe, it, expect, beforeAll } from 'vitest';
import { buildFluxWsUrl } from './useFluxSpeech';

// The builder falls back to the page origin when no API base is set, and
// this suite runs in node. Stub just that, rather than pulling in jsdom.
beforeAll(() => {
  (globalThis as unknown as { window: unknown }).window = {
    location: { origin: 'http://localhost:8000' },
  };
});

// Speculative drafting used the server's startup model -- always local -- so
// every voice turn loaded 3.6 GB onto an 8 GB GPU while the real answer came
// from the cloud. The socket carries the chat's model so drafting follows it.
describe('buildFluxWsUrl', () => {
  it('carries the chat model so drafting follows it', () => {
    expect(buildFluxWsUrl(true, 'gpt-5.6-luna')).toContain('model=gpt-5.6-luna');
  });

  it('carries a local model too, so prefer-cloud off still drafts on-device', () => {
    expect(buildFluxWsUrl(false, 'qwen3.5:4b')).toContain(
      `model=${encodeURIComponent('qwen3.5:4b')}`,
    );
  });

  it('omits the model when there is none, leaving the server its own default', () => {
    expect(buildFluxWsUrl(true)).not.toContain('model=');
  });
});
