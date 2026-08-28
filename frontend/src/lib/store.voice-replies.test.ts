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

describe('Speak Replies setting', () => {
  it('defaults on — speaking is the point of asking by voice', async () => {
    const useAppStore = await loadStore();
    expect(useAppStore.getState().settings.voiceRepliesEnabled).toBe(true);
  });

  it('persists across a reload', async () => {
    let useAppStore = await loadStore();
    useAppStore.getState().updateSettings({ voiceRepliesEnabled: false });

    vi.resetModules();
    useAppStore = await loadStore();

    expect(useAppStore.getState().settings.voiceRepliesEnabled).toBe(false);
  });

  it('a stored false survives merging with defaults', async () => {
    // The bug this guards: a default of `true` merged over stored settings
    // would silently switch speech back on every reload.
    localStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({ voiceRepliesEnabled: false }),
    );

    const useAppStore = await loadStore();

    expect(useAppStore.getState().settings.voiceRepliesEnabled).toBe(false);
  });

  it('is unaffected by the speech-to-text toggle', async () => {
    // Input and output are separate: dictating to Sage should not force it to
    // talk back, and muting it should not stop the microphone.
    const useAppStore = await loadStore();
    useAppStore.getState().updateSettings({ speechEnabled: true });

    expect(useAppStore.getState().settings.voiceRepliesEnabled).toBe(true);

    useAppStore.getState().updateSettings({ voiceRepliesEnabled: false });

    expect(useAppStore.getState().settings.speechEnabled).toBe(true);
  });
});
