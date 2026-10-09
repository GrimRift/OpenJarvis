import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';

// 8 October: 279 chats filled localStorage's ~10 MB, every save threw and was
// swallowed, and a day of chats lived only in the open window. Chats now live
// in IndexedDB; localStorage is only read once, to move them over.

const KEY = 'openjarvis-conversations';

class MemoryStorage {
  private store = new Map<string, string>();
  getItem(key: string): string | null {
    return this.store.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.store.set(key, String(value));
  }
  removeItem(key: string): void {
    this.store.delete(key);
  }
}

let storage: MemoryStorage;
let factory: IDBFactory;

beforeEach(() => {
  vi.resetModules();
  storage = new MemoryStorage();
  factory = new IDBFactory();
  const g = globalThis as unknown as Record<string, unknown>;
  g.localStorage = storage;
  g.window = Object.assign(globalThis, { addEventListener: () => {} });
});

afterEach(() => {
  const g = globalThis as unknown as Record<string, unknown>;
  g.localStorage = undefined;
});

function chat(id: string, text: string) {
  return {
    id,
    title: text,
    createdAt: 1,
    updatedAt: 1,
    model: 'm',
    messages: [{ id: `${id}-1`, role: 'user', content: text, timestamp: 1 }],
  };
}

async function boot() {
  const db = await import('./chat-db');
  await db.preloadChats(factory);
  const { useAppStore } = await import('./store');
  return useAppStore;
}

async function settle() {
  for (let i = 0; i < 20; i++) await new Promise((r) => setTimeout(r, 0));
}

async function savedIds(): Promise<string[]> {
  const { openChatDb } = await import('./chat-db');
  const db = (await openChatDb(factory))!;
  const keys = await new Promise<IDBValidKey[]>((resolve) => {
    const req = db.transaction('conversations').objectStore('conversations').getAllKeys();
    req.onsuccess = () => resolve(req.result);
  });
  db.close();
  return keys.map(String).sort();
}

describe('chats in IndexedDB', () => {
  it('moves the localStorage chats over and frees localStorage', async () => {
    storage.setItem(
      KEY,
      JSON.stringify({ version: 1, activeId: null, conversations: { a: chat('a', 'hello') } }),
    );
    const store = await boot();
    expect(store.getState().conversations.map((c) => c.id)).toEqual(['a']);

    const id = store.getState().createConversation('m');
    store.getState().addMessage(id, { id: 'x', role: 'user', content: 'new', timestamp: 2 });
    await settle();

    expect(await savedIds()).toEqual(['a', id].sort());
    expect(storage.getItem(KEY)).toBeNull();
  });

  it('reads them back from IndexedDB on the next start', async () => {
    storage.setItem(
      KEY,
      JSON.stringify({ version: 1, activeId: null, conversations: { a: chat('a', 'hello') } }),
    );
    let store = await boot();
    const id = store.getState().createConversation('m');
    store.getState().addMessage(id, { id: 'x', role: 'user', content: 'kept', timestamp: 2 });
    await settle();
    (await import('./chat-db')).resetChatDbForTests();

    vi.resetModules();
    store = await boot();
    const texts = store
      .getState()
      .conversations.flatMap((c) => c.messages.map((m) => m.content))
      .sort();
    expect(texts).toEqual(['hello', 'kept']);
  });

  it('holds far more than localStorage could', async () => {
    const store = await boot();
    const big = 'x'.repeat(200_000);
    for (let i = 0; i < 60; i++) {
      const id = store.getState().createConversation('m');
      store.getState().addMessage(id, { id: `m${i}`, role: 'user', content: big, timestamp: i });
    }
    await settle();
    expect((await savedIds()).length).toBe(60);
  });
});
