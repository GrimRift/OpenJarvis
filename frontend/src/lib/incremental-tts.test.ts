import { describe, expect, it } from 'vitest';
import {
  type IncrementalTtsMessage,
  IncrementalTtsOutbox,
  shouldFallbackAfterTtsFailure,
} from './incremental-tts';

describe('IncrementalTtsOutbox', () => {
  it('delivers model text before the response is marked complete', () => {
    const sent: object[] = [];
    const outbox = new IncrementalTtsOutbox((message) => sent.push(message));

    outbox.markReady();
    outbox.push('First sentence. ');

    expect(sent).toEqual([{ type: 'text', delta: 'First sentence. ' }]);
    expect(outbox.finished).toBe(false);
  });

  it('preserves delta order and sends finish after the final tail', () => {
    const sent: object[] = [];
    const outbox = new IncrementalTtsOutbox((message) => sent.push(message));

    outbox.push('One. ');
    outbox.push('Tail');
    outbox.finish();
    outbox.markReady();

    expect(sent).toEqual([
      { type: 'text', delta: 'One. ' },
      { type: 'text', delta: 'Tail' },
      { type: 'finish' },
    ]);
  });

  it('cancels, discards queued text, and rejects later deltas', () => {
    const sent: object[] = [];
    const outbox = new IncrementalTtsOutbox((message) => sent.push(message));

    outbox.push('Never speak this.');
    outbox.cancel();
    outbox.markReady();

    expect(outbox.push('Late frame.')).toBe(false);
    expect(sent).toEqual([{ type: 'cancel' }]);
  });

  it('bounds text queued before the socket is ready', () => {
    const outbox = new IncrementalTtsOutbox(() => {}, 12);

    expect(outbox.push('123456')).toBe(true);
    expect(outbox.push('789012')).toBe(true);
    expect(outbox.push('x')).toBe(false);
    expect(outbox.overflowed).toBe(true);
  });
});

describe('TTS fallback', () => {
  it('falls back before audio but never replays after audio started', () => {
    expect(shouldFallbackAfterTtsFailure(false)).toBe(true);
    expect(shouldFallbackAfterTtsFailure(true)).toBe(false);
  });
});

describe('IncrementalTtsOutbox flush', () => {
  it('keeps a flush asked for before the socket opened in its place', () => {
    const sent: IncrementalTtsMessage[] = [];
    const outbox = new IncrementalTtsOutbox((message) => sent.push(message));
    outbox.push('Searching the web, Sir.');
    outbox.flush();
    outbox.push(' Found it.');
    outbox.markReady();
    expect(sent).toEqual([
      { type: 'text', delta: 'Searching the web, Sir.' },
      { type: 'flush' },
      { type: 'text', delta: ' Found it.' },
    ]);
  });

  it('sends a flush straight away once ready, and none after finish', () => {
    const sent: IncrementalTtsMessage[] = [];
    const outbox = new IncrementalTtsOutbox((message) => sent.push(message));
    outbox.markReady();
    outbox.flush();
    outbox.finish();
    outbox.flush();
    expect(sent).toEqual([{ type: 'flush' }, { type: 'finish' }]);
  });
});
