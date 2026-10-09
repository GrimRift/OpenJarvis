import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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

const forget = vi.fn(async (turns: string[]) => ({ removed: turns.map((t) => `f-${t}`) }));
let lastToast: { action?: { onClick: () => void } } | undefined;

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
  forget.mockClear();
  lastToast = undefined;
  (globalThis as unknown as { localStorage: MemoryStorage }).localStorage = new MemoryStorage();
  vi.doMock('./api', async (original) => ({
    ...(await original<typeof import('./api')>()),
    forgetMemoryTurns: forget,
  }));
  vi.doMock('sonner', () => {
    const toast = Object.assign(
      (_title: string, options?: { action?: { onClick: () => void } }) => {
        lastToast = options;
        return 't1';
      },
      { dismiss: vi.fn() },
    );
    return { toast };
  });
});

afterEach(() => {
  vi.useRealTimers();
});

async function chat() {
  const { useAppStore } = await import('./store');
  const rewind = await import('./rewind');
  const store = useAppStore.getState();
  const id = store.createConversation('m');
  store.addMessage(id, { id: 'u1', role: 'user', content: 'how do you see me?', timestamp: 1 });
  store.addMessage(id, { id: 'a1', role: 'assistant', content: 'curious', timestamp: 2 });
  store.addMessage(id, { id: 'u2', role: 'user', content: 'steam sales?', timestamp: 3 });
  store.addMessage(id, { id: 'a2', role: 'assistant', content: 'chess board', timestamp: 4 });
  return { useAppStore, rewind, id };
}

const ids = (messages: { id: string }[]) => messages.map((m) => m.id);

describe('rewindChat', () => {
  it('cuts the message and everything after it, and puts its text in the box', async () => {
    const { useAppStore, rewind, id } = await chat();
    expect(rewind.rewindChat(id, 'u2')).toBe(true);
    expect(ids(useAppStore.getState().messages)).toEqual(['u1', 'a1']);
    expect(useAppStore.getState().composerDraft?.text).toBe('steam sales?');
  });

  it('rewinds from an earlier message too, and forgets every cut turn', async () => {
    const { useAppStore, rewind, id } = await chat();
    rewind.rewindChat(id, 'u1');
    expect(useAppStore.getState().messages).toEqual([]);
    vi.advanceTimersByTime(rewind.UNDO_MS);
    expect(forget).toHaveBeenCalledWith(['u1', 'u2']);
  });

  it('Undo puts the messages back and forgets nothing', async () => {
    const { useAppStore, rewind, id } = await chat();
    rewind.rewindChat(id, 'u2');
    lastToast?.action?.onClick();
    expect(ids(useAppStore.getState().messages)).toEqual(['u1', 'a1', 'u2', 'a2']);
    expect(useAppStore.getState().composerDraft).toMatchObject({
      text: '',
      unless: 'steam sales?',
    });
    vi.advanceTimersByTime(rewind.UNDO_MS * 2);
    expect(forget).not.toHaveBeenCalled();
  });

  it('sending a message settles the rewind at once', async () => {
    const { rewind, id } = await chat();
    rewind.rewindChat(id, 'u2');
    rewind.finalizePendingRewind();
    expect(forget).toHaveBeenCalledWith(['u2']);
    vi.advanceTimersByTime(rewind.UNDO_MS);
    expect(forget).toHaveBeenCalledTimes(1);
  });

  it('does nothing while Sage is answering, or for a reply', async () => {
    const { useAppStore, rewind, id } = await chat();
    expect(rewind.rewindChat(id, 'a2')).toBe(false);
    useAppStore.getState().setStreamState({ isStreaming: true });
    expect(rewind.rewindChat(id, 'u2')).toBe(false);
    expect(ids(useAppStore.getState().messages)).toHaveLength(4);
  });

  it('Undo is refused once a new message was sent', async () => {
    const { useAppStore, id } = await chat();
    const removed = useAppStore.getState().rewindTo(id, 'u2')!;
    useAppStore
      .getState()
      .addMessage(id, { id: 'u3', role: 'user', content: 'new', timestamp: Date.now() });
    expect(useAppStore.getState().restoreRewound(id, removed)).toBe(false);
  });
});
