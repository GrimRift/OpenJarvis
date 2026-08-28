import { beforeEach, describe, expect, it, vi } from 'vitest';

const CONVERSATIONS_KEY = 'openjarvis-conversations';

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

beforeEach(() => {
  vi.resetModules();
  (globalThis as unknown as { localStorage: MemoryStorage }).localStorage =
    new MemoryStorage();
});

async function loadStore() {
  return (await import('./store')).useAppStore;
}

function conv(id: string, messageCount: number) {
  return {
    id,
    title: messageCount ? `chat ${id}` : 'New chat',
    createdAt: 1,
    updatedAt: 1,
    messages: Array.from({ length: messageCount }, (_, i) => ({
      id: `${id}-m${i}`,
      role: 'user',
      content: 'hi',
    })),
  };
}

function seed(...convs: ReturnType<typeof conv>[]) {
  localStorage.setItem(
    CONVERSATIONS_KEY,
    JSON.stringify({
      version: 1,
      activeId: convs[0]?.id ?? null,
      conversations: Object.fromEntries(convs.map((c) => [c.id, c])),
    }),
  );
}

describe('empty conversations', () => {
  it('are pruned at load', async () => {
    // Switching between Chat and Voice used to leave one of these behind
    // every time, filling the sidebar with "New chat".
    seed(conv('empty-1', 0), conv('real', 2), conv('empty-2', 0));

    const useAppStore = await loadStore();
    const ids = useAppStore.getState().conversations.map((c) => c.id);

    expect(ids).toEqual(['real']);
  });

  it('are removed from storage, not just from memory', async () => {
    seed(conv('empty-1', 0), conv('real', 2));

    await loadStore();
    const saved = JSON.parse(localStorage.getItem(CONVERSATIONS_KEY) ?? '{}');

    expect(Object.keys(saved.conversations)).toEqual(['real']);
  });

  it('leave real conversations alone', async () => {
    seed(conv('a', 1), conv('b', 4));

    const useAppStore = await loadStore();

    expect(useAppStore.getState().conversations).toHaveLength(2);
  });
});

describe('startNewChat', () => {
  it('clears the thread without writing a row', async () => {
    seed(conv('real', 2));
    const useAppStore = await loadStore();
    const before = useAppStore.getState().conversations.length;

    useAppStore.getState().startNewChat();

    expect(useAppStore.getState().activeId).toBeNull();
    expect(useAppStore.getState().messages).toEqual([]);
    // The point: no "New chat" appears until something is actually said.
    expect(useAppStore.getState().conversations).toHaveLength(before);
  });

  it('is safe to call repeatedly', async () => {
    const useAppStore = await loadStore();
    useAppStore.getState().startNewChat();
    useAppStore.getState().startNewChat();
    useAppStore.getState().startNewChat();

    expect(useAppStore.getState().conversations).toHaveLength(0);
  });
});
