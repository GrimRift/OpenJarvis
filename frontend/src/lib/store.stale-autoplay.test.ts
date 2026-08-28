import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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

afterEach(() => {
  (globalThis as unknown as { localStorage?: MemoryStorage }).localStorage =
    undefined;
});

// Reproduces the reported bug directly: an old chat with several past voice
// replies (autoPlay: true persisted from when each one originally played,
// never actually cleared in storage — only ever hidden from the in-memory
// view) gets a new voice message sent in it. addMessage/updateLastAssistant
// read straight from raw storage and install that as the new `messages`
// state, so every old reply's autoPlay resurfaced at once and all of them
// played back together with the new one.
function chatWithPastVoiceReplies() {
  return {
    version: 1,
    activeId: 'old-chat',
    conversations: {
      'old-chat': {
        id: 'old-chat',
        title: 'Old voice chat',
        createdAt: 1,
        updatedAt: 1,
        model: 'test-model',
        messages: [
          {
            id: 'assistant-1',
            role: 'assistant',
            content: 'First reply',
            timestamp: 1,
            audio: { url: '/v1/speech/audio/one', autoPlay: true },
          },
          {
            id: 'assistant-2',
            role: 'assistant',
            content: 'Second reply',
            timestamp: 2,
            audio: { url: '/v1/speech/audio/two', autoPlay: true },
          },
          {
            id: 'assistant-3',
            role: 'assistant',
            content: 'Third reply',
            timestamp: 3,
            audio: { url: '/v1/speech/audio/three', autoPlay: true },
          },
        ],
      },
    },
  };
}

describe('stale audio.autoPlay on old voice replies', () => {
  it('is cleared in storage on load, not just hidden in memory', async () => {
    localStorage.setItem(CONVERSATIONS_KEY, JSON.stringify(chatWithPastVoiceReplies()));

    const { useAppStore } = await import('./store');

    for (const message of useAppStore.getState().messages) {
      expect(message.audio?.autoPlay).toBe(false);
    }
    const persisted = JSON.parse(localStorage.getItem(CONVERSATIONS_KEY) ?? '{}');
    for (const message of persisted.conversations['old-chat'].messages) {
      expect(message.audio.autoPlay).toBe(false);
    }
  });

  it('does not resurrect old autoPlay flags when a new message is added', async () => {
    localStorage.setItem(CONVERSATIONS_KEY, JSON.stringify(chatWithPastVoiceReplies()));

    const { useAppStore } = await import('./store');

    // Sending a new voice message — the exact trigger from the report.
    useAppStore.getState().addMessage('old-chat', {
      id: 'user-2',
      role: 'user',
      content: 'play a song',
      timestamp: 4,
    });

    const messages = useAppStore.getState().messages;
    const stillAutoplaying = messages.filter((m) => m.audio?.autoPlay);
    expect(stillAutoplaying).toHaveLength(0);
  });

  it('still lets the current turn autoplay its own fresh reply', async () => {
    localStorage.setItem(CONVERSATIONS_KEY, JSON.stringify(chatWithPastVoiceReplies()));

    const { useAppStore } = await import('./store');
    useAppStore.getState().selectConversation('old-chat');

    useAppStore.getState().addMessage('old-chat', {
      id: 'assistant-4',
      role: 'assistant',
      content: '',
      timestamp: 5,
    });
    useAppStore
      .getState()
      .updateLastAssistant(
        'old-chat',
        'Playing your song now.',
        undefined,
        undefined,
        undefined,
        { url: '/v1/speech/audio/four', autoPlay: true },
      );

    const messages = useAppStore.getState().messages;
    const last = messages[messages.length - 1];
    expect(last.audio?.autoPlay).toBe(true);

    const others = messages.slice(0, -1);
    for (const message of others) {
      expect(message.audio?.autoPlay).toBe(false);
    }
  });
});
