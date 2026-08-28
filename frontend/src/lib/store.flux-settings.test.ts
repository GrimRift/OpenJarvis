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

describe('Flux settings', () => {
  it('defaults to local transcription with speculation off', async () => {
    const useAppStore = await loadStore();
    const { settings } = useAppStore.getState();

    expect(settings.fluxEnabled).toBe(false);
    expect(settings.fluxEagerEnabled).toBe(false);
  });

  it('persists both toggles across a reload', async () => {
    let useAppStore = await loadStore();
    useAppStore
      .getState()
      .updateSettings({ fluxEnabled: true, fluxEagerEnabled: true });

    vi.resetModules();
    useAppStore = await loadStore();

    expect(useAppStore.getState().settings.fluxEnabled).toBe(true);
    expect(useAppStore.getState().settings.fluxEagerEnabled).toBe(true);
  });

  it('never restores speculation when Flux itself is off', async () => {
    // Hand-edited storage, or Flux switched off while eager stayed set.
    localStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({ fluxEnabled: false, fluxEagerEnabled: true }),
    );

    const useAppStore = await loadStore();
    const { settings } = useAppStore.getState();

    expect(settings.fluxEnabled).toBe(false);
    expect(settings.fluxEagerEnabled).toBe(false);
  });

  it('leaves unrelated voice settings untouched', async () => {
    const useAppStore = await loadStore();
    useAppStore.getState().updateSettings({
      wakeWordEnabled: true,
      continuousConversationEnabled: true,
      fluxEnabled: true,
    });

    const { settings } = useAppStore.getState();
    expect(settings.wakeWordEnabled).toBe(true);
    expect(settings.continuousConversationEnabled).toBe(true);
    expect(settings.wakeWordGreetingEnabled).toBe(true);
  });

  it('tolerates settings saved before Flux existed', async () => {
    localStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({ speechEnabled: true, wakeWordEnabled: true }),
    );

    const useAppStore = await loadStore();
    const { settings } = useAppStore.getState();

    expect(settings.fluxEnabled).toBe(false);
    expect(settings.fluxEagerEnabled).toBe(false);
    expect(settings.speechEnabled).toBe(true);
  });
});
