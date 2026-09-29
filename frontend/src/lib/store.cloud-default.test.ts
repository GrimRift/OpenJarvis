import { beforeEach, describe, expect, it, vi } from 'vitest';

const SETTINGS_KEY = 'openjarvis-settings';

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

describe('Cloud model default change', () => {
  it('moves a saved former default to the new default', async () => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({ cloudModel: 'gpt-5.6-luna' }));
    const useAppStore = await loadStore();

    expect(useAppStore.getState().settings.cloudModel).toBe('gpt-6-luna');
    expect(JSON.parse(localStorage.getItem(SETTINGS_KEY)!).cloudModel).toBe('gpt-6-luna');
  });

  it('keeps a model the user chose that was never a default', async () => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({ cloudModel: 'gpt-4o' }));
    const useAppStore = await loadStore();

    expect(useAppStore.getState().settings.cloudModel).toBe('gpt-4o');
  });

  it('lets the old model be chosen again afterwards', async () => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({ cloudModel: 'gpt-5.6-luna' }));
    let useAppStore = await loadStore();
    useAppStore.getState().updateSettings({ cloudModel: 'gpt-5.6-luna' });

    vi.resetModules();
    useAppStore = await loadStore();

    expect(useAppStore.getState().settings.cloudModel).toBe('gpt-5.6-luna');
  });
});
