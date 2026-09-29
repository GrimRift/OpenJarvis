import { afterEach, describe, expect, it } from 'vitest';
import { appWindowHidden } from './app-window';

// Tests run in Node: a bare object stands in for the page's window.
const g = globalThis as { window?: { __sageWindowHidden?: boolean } };

describe('appWindowHidden', () => {
  afterEach(() => { delete g.window; });

  it('is false outside a page and in a browser, where the flag is never set', () => {
    expect(appWindowHidden()).toBe(false);
    g.window = {};
    expect(appWindowHidden()).toBe(false);
  });

  it('follows the flag the app sets', () => {
    g.window = { __sageWindowHidden: true };
    expect(appWindowHidden()).toBe(true);
    g.window.__sageWindowHidden = false;
    expect(appWindowHidden()).toBe(false);
  });
});
