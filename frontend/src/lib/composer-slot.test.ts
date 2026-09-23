import { describe, expect, it } from 'vitest';
import {
  clearComposerSlot,
  getComposerSize,
  getComposerSlot,
  setComposerSize,
  setComposerSlot,
  subscribeComposerSlot,
} from './composer-slot';

const el = (name: string) => ({ name }) as unknown as HTMLElement;

describe('the one message box and its slot', () => {
  it('a page leaving does not clear the slot the next page already took', () => {
    // React can mount Voice's slot before Chat's unmounts; clearing blindly
    // would hide the box on the page just opened.
    const chat = el('chat');
    const voice = el('voice');
    setComposerSlot({ el: chat, voice: false });
    setComposerSlot({ el: voice, voice: true });
    clearComposerSlot(chat);
    expect(getComposerSlot()?.el).toBe(voice);
    clearComposerSlot(voice);
    expect(getComposerSlot()).toBeNull();
  });

  it('reports size changes once, and not at all when nothing changed', () => {
    let calls = 0;
    const stop = subscribeComposerSlot(() => calls++);
    setComposerSize(500, 140);
    setComposerSize(500, 140);
    expect(calls).toBe(1);
    expect(getComposerSize()).toEqual({ width: 500, height: 140 });
    stop();
  });
});
