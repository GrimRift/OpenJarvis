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

describe('audio playback ownership', () => {
  it('stays active until every playback owner releases its claim', async () => {
    const useAppStore = await loadStore();

    useAppStore.getState().setAudioPlayback('streaming-tts', true);
    useAppStore.getState().setAudioPlayback('batch-player', true);
    useAppStore.getState().setAudioPlayback('streaming-tts', false);

    expect(useAppStore.getState().audioPlaying).toBe(true);

    useAppStore.getState().setAudioPlayback('batch-player', false);
    expect(useAppStore.getState().audioPlaying).toBe(false);
  });

  it('treats repeated claims from one owner as idempotent', async () => {
    const useAppStore = await loadStore();

    useAppStore.getState().setAudioPlayback('streaming-tts', true);
    useAppStore.getState().setAudioPlayback('streaming-tts', true);
    useAppStore.getState().setAudioPlayback('streaming-tts', false);

    expect(useAppStore.getState().audioPlaying).toBe(false);
  });
});

describe('retractLastExchange', () => {
  it('drops the last user turn and every reply to it, returning the text', async () => {
    const { useAppStore } = await import('./store');
    const store = useAppStore.getState();
    const id = store.createConversation('m');
    store.addMessage(id, { id: 'u1', role: 'user', content: 'earlier', timestamp: 1 });
    store.addMessage(id, { id: 'a1', role: 'assistant', content: 'ok', timestamp: 2 });
    store.addMessage(id, { id: 'u2', role: 'user', content: 'half a question', timestamp: 3 });
    store.addMessage(id, { id: 'a2', role: 'assistant', content: 'half an answer', timestamp: 4 });

    const text = useAppStore.getState().retractLastExchange(id);

    expect(text).toBe('half a question');
    const remaining = useAppStore.getState().messages.map((m) => m.id);
    expect(remaining).toEqual(['u1', 'a1']);
  });

  it('is a no-op on an empty conversation', async () => {
    const { useAppStore } = await import('./store');
    const store = useAppStore.getState();
    const id = store.createConversation('m');
    expect(useAppStore.getState().retractLastExchange(id)).toBeNull();
  });
});
