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

function seedHistory() {
  localStorage.setItem(
    CONVERSATIONS_KEY,
    JSON.stringify({
      version: 1,
      activeId: 'c1',
      conversations: {
        c1: {
          id: 'c1',
          title: 'Yesterday’s chat',
          createdAt: Date.now(),
          updatedAt: Date.now(),
          messages: [
            { id: 'm1', role: 'user', content: 'hello' },
            { id: 'm2', role: 'assistant', content: 'hi there' },
          ],
        },
      },
    }),
  );
}

describe('opening the app', () => {
  it('lands on a fresh chat, not the last one', async () => {
    seedHistory();

    const useAppStore = await loadStore();
    const { activeId, messages } = useAppStore.getState();

    expect(activeId).toBeNull();
    expect(messages).toEqual([]);
  });

  it('still keeps the old conversation in the sidebar', async () => {
    // Starting fresh must not look like the history was deleted.
    seedHistory();

    const useAppStore = await loadStore();

    expect(
      useAppStore.getState().conversations.some((c) => c.id === 'c1'),
    ).toBe(true);
  });

  it('is unbothered by an empty history', async () => {
    const useAppStore = await loadStore();

    expect(useAppStore.getState().activeId).toBeNull();
    expect(useAppStore.getState().messages).toEqual([]);
  });
});
