import { describe, expect, it } from 'vitest';
import { formatShortcut, shortcutFromKey } from './desktop-app';

const key = (code: string, mods: Partial<Record<'ctrlKey' | 'altKey' | 'shiftKey' | 'metaKey', boolean>> = {}) => ({
  code,
  ctrlKey: false,
  altKey: false,
  shiftKey: false,
  metaKey: false,
  ...mods,
});

describe('shortcutFromKey', () => {
  it('spells modifiers in a fixed order before the key', () => {
    expect(shortcutFromKey(key('KeyS', { shiftKey: true, altKey: true, ctrlKey: true }))).toBe('Ctrl+Alt+Shift+KeyS');
    expect(shortcutFromKey(key('Space', { metaKey: true, altKey: true }))).toBe('Alt+Super+Space');
  });

  it('waits while only modifiers are held', () => {
    expect(shortcutFromKey(key('ControlLeft', { ctrlKey: true }))).toBeNull();
    expect(shortcutFromKey(key('AltRight', { ctrlKey: true, altKey: true }))).toBeNull();
    expect(shortcutFromKey(key('MetaLeft', { metaKey: true }))).toBeNull();
  });

  it('refuses a key with no Ctrl, Alt or Win, so typing is never stolen', () => {
    expect(shortcutFromKey(key('KeyS'))).toBeNull();
    expect(shortcutFromKey(key('KeyS', { shiftKey: true }))).toBeNull();
  });
});

describe('formatShortcut', () => {
  it('reads like a keyboard', () => {
    expect(formatShortcut('Ctrl+Alt+KeyS')).toBe('Ctrl + Alt + S');
    expect(formatShortcut('Alt+Super+Digit1')).toBe('Alt + Win + 1');
    expect(formatShortcut('Ctrl+Shift+Space')).toBe('Ctrl + Shift + Space');
    expect(formatShortcut('Ctrl+F12')).toBe('Ctrl + F12');
  });
});
