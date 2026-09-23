import { beforeEach, describe, expect, it, vi } from 'vitest';

// A reply's audio arriving while the page's AudioContext is suspended: after
// any reload, until the page is clicked. It used to be dropped unheard, with
// the orb on standing by and follow-up listening never re-armed.

class FakeSocket {
  static last: FakeSocket | null = null;
  readyState = 1;
  bufferedAmount = 0;
  binaryType = '';
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  constructor() {
    FakeSocket.last = this;
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.readyState = 3;
  }
}

vi.mock('../lib/speech-transport', () => ({ openTtsSocket: () => new FakeSocket() }));
vi.mock('../lib/speech-analyser', () => ({ analyseInto: () => () => {} }));
vi.mock('../lib/voice-trace', () => ({ voiceTrace: vi.fn() }));
vi.mock('../lib/audio-out', () => ({
  limiter: () => ({ connect: () => {} }),
  outputContext: () => null,
}));

let resumes = true;
const started: number[] = [];

class FakeAudioContext {
  state: AudioContextState = 'suspended';
  currentTime = 0;
  baseLatency = 0;
  destination = {};
  // Asynchronous, as a browser's is: the state changes only later.
  resume() {
    if (!resumes) return Promise.reject(new Error('not allowed'));
    return new Promise<void>((resolve) =>
      setTimeout(() => {
        this.state = 'running';
        resolve();
      }, 5),
    );
  }
  createGain() {
    return { gain: { value: 1 }, connect: () => {} };
  }
  createBuffer(_channels: number, length: number) {
    return { length, copyToChannel: () => {} };
  }
  createBufferSource() {
    return {
      buffer: null,
      connect: () => {},
      start: (at: number) => started.push(at),
      stop: () => {},
      onended: null,
    };
  }
}

beforeEach(() => {
  const memory = new Map<string, string>();
  const g = globalThis as unknown as Record<string, unknown>;
  g.localStorage = {
    getItem: (key: string) => memory.get(key) ?? null,
    setItem: (key: string, value: string) => void memory.set(key, String(value)),
    removeItem: (key: string) => void memory.delete(key),
  };
  g.window = Object.assign(globalThis, {
    AudioContext: FakeAudioContext,
    addEventListener: () => {},
    setTimeout,
    clearTimeout,
  });
  started.length = 0;
  vi.resetModules();
});

async function reply() {
  const { streamingTtsPlayer } = await import('./useStreamingTts');
  const session = streamingTtsPlayer().begin({ id: 'v', provider: 'chatterbox' } as never);
  const socket = FakeSocket.last!;
  socket.onopen?.();
  socket.onmessage?.({ data: JSON.stringify({ type: 'ready', sample_rate: 24000 }) });
  session.push('Hello, Sir.');
  session.finish();
  socket.onmessage?.({ data: JSON.stringify({ type: 'start', sample_rate: 24000 }) });
  socket.onmessage?.({ data: new Float32Array(240).buffer });
  return { session, socket };
}

describe('a reply arriving on a suspended page', () => {
  it('resumes the audio and plays what arrived meanwhile', async () => {
    resumes = true;
    const { session } = await reply();
    const { useAppStore } = await import('../lib/store');
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(started.length).toBe(1);
    expect(useAppStore.getState().audioPlaying).toBe(true);
    session.cancel();
  });

  it('reports it as blocked, not silently failed, when the browser refuses', async () => {
    resumes = false;
    const { session } = await reply();
    const outcome = await session.finish();
    expect(outcome).toBe('blocked');
    expect(started.length).toBe(0);
  });
});
