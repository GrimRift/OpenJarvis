import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The browser's LockManager, as far as this uses it: one holder, a queue,
// `steal` taking the lock from the holder (whose request then rejects), and
// the lock passing to the next in the queue when the holder lets go -- which
// the browser does however a page ends.
class FakeLocks {
  holder: { release: () => void; reject: (e: Error) => void } | null = null;
  queue: Array<{ grant: () => void; signal?: AbortSignal }> = [];

  request(_name: string, options: { steal?: boolean; signal?: AbortSignal }, callback: () => Promise<void>) {
    return new Promise<void>((resolve, reject) => {
      const grant = () => {
        let done = false;
        const finish = (fn: () => void) => {
          if (done) return;
          done = true;
          fn();
        };
        const held = callback();
        this.holder = {
          release: () => finish(() => { this.holder = null; resolve(); this.next(); }),
          reject: (e) => finish(() => reject(e)),
        };
        void held.then(() => this.holder?.release());
      };
      if (options.steal) {
        const previous = this.holder;
        this.holder = null;
        previous?.reject(new Error('AbortError'));
        grant();
      } else if (!this.holder) {
        grant();
      } else {
        const entry = { grant, signal: options.signal };
        options.signal?.addEventListener('abort', () => {
          this.queue = this.queue.filter((q) => q !== entry);
          reject(new Error('AbortError'));
        });
        this.queue.push(entry);
      }
    });
  }

  next() {
    const entry = this.queue.shift();
    entry?.grant();
  }
}

const flush = () => new Promise((r) => setTimeout(r, 0));

async function openPage() {
  vi.resetModules();
  const page = await import('./voice-owner');
  const stop = page.startVoiceOwnership();
  await flush();
  return { ...page, stop };
}

beforeEach(() => {
  vi.stubGlobal('navigator', { locks: new FakeLocks() });
  vi.stubGlobal('window', { addEventListener: () => {}, removeEventListener: () => {} });
  vi.stubGlobal('document', {
    visibilityState: 'visible',
    hasFocus: () => true,
    addEventListener: () => {},
    removeEventListener: () => {},
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('one Sage page listens at a time', () => {
  it('the page opened last takes the voice from the one before', async () => {
    // 23 September: two pages both heard the microphone and answered every
    // turn twice, until one was closed.
    const first = await openPage();
    expect(first.isVoiceOwner()).toBe(true);
    const second = await openPage();
    await flush();
    expect(second.isVoiceOwner()).toBe(true);
    expect(first.isVoiceOwner()).toBe(false);
  });

  it('using a page again takes the voice back', async () => {
    const first = await openPage();
    const second = await openPage();
    first.claimVoice();
    await flush();
    expect(first.isVoiceOwner()).toBe(true);
    expect(second.isVoiceOwner()).toBe(false);
  });

  it('when the page with the voice goes away, the one waiting takes it', async () => {
    // The first version handed over with a message from the closing page;
    // the browser could tear it down first, and nobody was left listening.
    const first = await openPage();
    const second = await openPage();
    await flush();
    expect(first.isVoiceOwner()).toBe(false);
    second.stop();
    await flush();
    await flush();
    expect(first.isVoiceOwner()).toBe(true);
  });

  it('a lone page keeps the voice however it is used', async () => {
    const only = await openPage();
    only.claimVoice();
    await flush();
    expect(only.isVoiceOwner()).toBe(true);
  });
});
