import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import type { ChatMessage } from '../types';

// Minimal in-memory localStorage stub so the store can run under node, the
// same shape api.auth.test.ts uses. The store reads storage at import time,
// so it has to be imported after this exists.
class MemoryStorage {
  private store = new Map<string, string>();
  getItem(k: string): string | null {
    return this.store.has(k) ? (this.store.get(k) as string) : null;
  }
  setItem(k: string, v: string): void {
    this.store.set(k, String(v));
  }
  removeItem(k: string): void {
    this.store.delete(k);
  }
  clear(): void {
    this.store.clear();
  }
}

async function freshStore() {
  return import('./store');
}

/**
 * An attached document has to outlive the turn it arrived on.
 *
 * Reported from real use: attach a paper, ask for a summary (worked), then ask
 * for its references and Sage answered that it had no file and asked for a
 * re-upload — in the same chat. Input tokens told the story, 8,626 then 5,295.
 *
 * The first fix kept documents in an in-memory map, copied from the image
 * path. That was wrong for the same reason it is right for images: a
 * screenshot is megabytes, a paper is about 20 KB. The map was emptied by a
 * page reload, and during development by Vite replacing the store module,
 * which is exactly how it failed the second time.
 */
const PAPER = { name: 'Chapter 2 - RRL (Local).docx', text: 'REFERENCES\nBaluyut, M. L. L.' };

function userMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: `m-${Math.random().toString(36).slice(2)}`,
    role: 'user',
    content: 'Summarize the paper for me briefly',
    timestamp: Date.now(),
    ...overrides,
  };
}

describe('an attached document survives the turn it arrived on', () => {
  beforeEach(() => {
    vi.resetModules();
    (globalThis as unknown as { localStorage: MemoryStorage }).localStorage =
      new MemoryStorage();
  });

  afterEach(() => {
    (globalThis as unknown as { localStorage?: MemoryStorage }).localStorage =
      undefined;
  });

  it('is still on the message after the store round-trips it', async () => {
    const { useAppStore } = await freshStore();
    const id = useAppStore.getState().createConversation('gpt-5.6-luna');
    const message = userMessage({ documents: [PAPER] });
    useAppStore.getState().addMessage(id, message);

    const stored = useAppStore
      .getState()
      .messages.find((m) => m.id === message.id);
    expect(stored?.documents?.[0]?.text).toContain('REFERENCES');
  });

  it('survives a reload, because that is what a page refresh is', async () => {
    const { useAppStore } = await freshStore();
    const id = useAppStore.getState().createConversation('gpt-5.6-luna');
    const message = userMessage({ documents: [PAPER] });
    useAppStore.getState().addMessage(id, message);

    // Everything in memory is discarded; only localStorage remains.
    useAppStore.getState().selectConversation(id);
    const stored = useAppStore
      .getState()
      .messages.find((m) => m.id === message.id);
    expect(stored?.documents?.[0]?.name).toBe(PAPER.name);
  });

  it('keeps an oversized document for the session rather than losing it', async () => {
    const { useAppStore, documentsFor, MAX_PERSISTED_DOCUMENT_CHARS } =
      await freshStore();
    const id = useAppStore.getState().createConversation('gpt-5.6-luna');
    const huge = {
      name: 'enormous.pdf',
      text: 'x'.repeat(MAX_PERSISTED_DOCUMENT_CHARS + 1),
    };
    const message = userMessage({ documents: [huge] });
    useAppStore.getState().addMessage(id, message);

    // Not written to storage, so the quota is safe...
    const raw = localStorage.getItem('openjarvis-conversations') ?? '';
    expect(raw).not.toContain('enormous.pdf');
    // ...but still usable while the tab is open.
    expect(documentsFor(message.id)?.[0]?.name).toBe('enormous.pdf');
  });

  it('a message with no document is unaffected', async () => {
    const { useAppStore } = await freshStore();
    const id = useAppStore.getState().createConversation('gpt-5.6-luna');
    const message = userMessage();
    useAppStore.getState().addMessage(id, message);
    const stored = useAppStore
      .getState()
      .messages.find((m) => m.id === message.id);
    expect(stored?.documents).toBeUndefined();
  });
});
