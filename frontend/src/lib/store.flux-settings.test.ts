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

describe('speech providers', () => {
  it('reads settings saved before providers existed as their Flux choice', async () => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({ fluxEnabled: true, fluxEagerEnabled: true }));
    const useAppStore = await loadStore();
    const { settings } = useAppStore.getState();
    expect(settings.sttProvider).toBe('flux');
    // The default voice engine is local Nano since 24 September.
    expect(settings.ttsProvider).toBe('chatterbox');
    expect(settings.fluxEnabled).toBe(true);
    expect(settings.fluxEagerEnabled).toBe(true);
  });

  it('parakeet uses the streaming socket but never speculates', async () => {
    const useAppStore = await loadStore();
    useAppStore.getState().updateSettings({ sttProvider: 'parakeet', fluxEagerEnabled: true });
    const { settings } = useAppStore.getState();
    expect(settings.fluxEnabled).toBe(true);
    expect(settings.fluxEagerEnabled).toBe(false);
  });

  it('choosing whisper switches streaming off, and back on keeps the last provider', async () => {
    const useAppStore = await loadStore();
    useAppStore.getState().updateSettings({ sttProvider: 'parakeet' });
    useAppStore.getState().updateSettings({ fluxEnabled: false });
    expect(useAppStore.getState().settings.sttProvider).toBe('whisper');
    useAppStore.getState().updateSettings({ fluxEnabled: true });
    expect(useAppStore.getState().settings.sttProvider).toBe('flux');
  });

  it('rejects an unknown provider from storage', async () => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({ sttProvider: 'nemo', ttsProvider: 'kokoro' }));
    const useAppStore = await loadStore();
    const { settings } = useAppStore.getState();
    expect(settings.sttProvider).toBe('whisper');
    // The default voice engine is local Nano since 24 September.
    expect(settings.ttsProvider).toBe('chatterbox');
  });
});

describe('defaults and reset', () => {
  it('starts a new install on Jarvis (Local), the Nano voice', async () => {
    const { settings } = (await loadStore()).getState();
    expect(settings.ttsProvider).toBe('chatterbox');
    expect(settings.ttsVoiceId).toBe('chatterbox:jarvis');
  });

  it('keeps the last voice chosen, a Turbo one included, across a reload', async () => {
    localStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({ ttsProvider: 'chatterbox', ttsVoiceId: 'chatterbox:turbo-jarvis', bargeInEnabled: false }),
    );
    const { settings } = (await loadStore()).getState();
    expect(settings.ttsVoiceId).toBe('chatterbox:turbo-jarvis');
    // A switch turned off stays off.
    expect(settings.bargeInEnabled).toBe(false);
  });

  it('forgets settings on reset, and only settings', async () => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({ ttsVoiceId: 'chatterbox:turbo-jarvis' }));
    localStorage.setItem('openjarvis-memory-top-k', '9');
    localStorage.setItem('openjarvis-conversations', '{"c1":{}}');
    const { resetAllSettings, defaultSettings } = await import('./store');
    resetAllSettings();
    expect(localStorage.getItem(SETTINGS_KEY)).toBeNull();
    expect(localStorage.getItem('openjarvis-memory-top-k')).toBeNull();
    expect(localStorage.getItem('openjarvis-conversations')).toBe('{"c1":{}}');
    vi.resetModules();
    const { settings } = (await loadStore()).getState();
    expect(settings).toEqual(defaultSettings());
  });
});
