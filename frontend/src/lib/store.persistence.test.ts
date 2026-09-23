import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// 24 September: the orb stuttered the moment the user finished talking, and
// when a reply began, ended or was stopped. Each was a chat change, and each
// parsed and rewrote every conversation in localStorage (2.35 MB, 50-86 ms).

const KEY = 'openjarvis-conversations';

class MemoryStorage {
  private store = new Map<string, string>();
  reads = 0;
  writes = 0;
  getItem(key: string): string | null {
    this.reads++;
    return this.store.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.writes++;
    this.store.set(key, String(value));
  }
  removeItem(key: string): void {
    this.store.delete(key);
  }
}

let storage: MemoryStorage;
let idle: Array<() => void>;

beforeEach(() => {
  vi.resetModules();
  storage = new MemoryStorage();
  idle = [];
  const g = globalThis as unknown as Record<string, unknown>;
  g.localStorage = storage;
  g.window = Object.assign(globalThis, {
    addEventListener: () => {},
    requestIdleCallback: (cb: () => void) => {
      idle.push(cb);
      return idle.length;
    },
  });
});

afterEach(() => {
  const g = globalThis as unknown as Record<string, unknown>;
  delete g.requestIdleCallback;
  g.localStorage = undefined;
});

async function freshStore() {
  const { useAppStore } = await import('./store');
  return useAppStore;
}

describe('saving the chats', () => {
  it('reads storage once, not on every change', async () => {
    const store = await freshStore();
    const id = store.getState().createConversation('m');
    const before = storage.reads;
    for (let i = 0; i < 5; i++) {
      store.getState().addMessage(id, { id: `u${i}`, role: 'user', content: 'q', timestamp: i });
    }
    expect(storage.reads - before).toBe(0);
  });

  it('writes once for a burst of changes, when the page is idle', async () => {
    const store = await freshStore();
    const id = store.getState().createConversation('m');
    const before = storage.writes;
    store.getState().addMessage(id, { id: 'u', role: 'user', content: 'q', timestamp: 1 });
    store.getState().addMessage(id, { id: 'a', role: 'assistant', content: '', timestamp: 2 });
    store.getState().updateLastAssistant(id, 'Hello');
    expect(storage.writes - before).toBe(0);
    for (const cb of idle.splice(0)) cb();
    expect(storage.writes - before).toBe(1);
    const saved = JSON.parse(storage.getItem(KEY) ?? '{}');
    expect(saved.conversations[id].messages.map((m: { content: string }) => m.content)).toEqual([
      'q',
      'Hello',
    ]);
  });

  it('gives a growing reply a new object, so the bubble redraws', async () => {
    const store = await freshStore();
    const id = store.getState().createConversation('m');
    store.getState().addMessage(id, { id: 'a', role: 'assistant', content: '', timestamp: 1 });
    const first = store.getState().messages[0];
    store.getState().updateLastAssistant(id, 'Hel');
    const second = store.getState().messages[0];
    expect(second).not.toBe(first);
    expect(second.content).toBe('Hel');
  });

  it('does not replay an earlier spoken reply on the next message', async () => {
    const store = await freshStore();
    const id = store.getState().createConversation('m');
    store.getState().addMessage(id, { id: 'a', role: 'assistant', content: '', timestamp: 1 });
    store.getState().updateLastAssistant(id, 'Hi', undefined, undefined, undefined, {
      url: '/a.wav',
      autoPlay: true,
    });
    expect(store.getState().messages[0].audio?.autoPlay).toBe(true);
    store.getState().addMessage(id, { id: 'u', role: 'user', content: 'next', timestamp: 2 });
    expect(store.getState().messages[0].audio?.autoPlay).toBe(false);
  });
});
