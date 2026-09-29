import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { isBackdropClick } from './overlay-backdrop';

const here = dirname(fileURLToPath(import.meta.url));
const source = (path: string) => readFileSync(resolve(here, path), 'utf8');

describe('isBackdropClick', () => {
  it('a click on the full-screen layer itself is the backdrop', () => {
    const layer = {};
    expect(isBackdropClick({ target: layer, currentTarget: layer })).toBe(true);
  });

  it('a click on the content inside it is not', () => {
    expect(isBackdropClick({ target: {}, currentTarget: {} })).toBe(false);
  });
});

// The user asked (2026-09-29) that clicking the dimmed background closes the
// diagram and the image overlays, with the Close button kept. The diagram's
// content layer covered the whole screen and stopped every click, so the
// backdrop never closed it.
describe('overlays close on the backdrop', () => {
  for (const file of ['../components/Diagram/DiagramOverlay.tsx', '../components/Image/ImageOverlay.tsx']) {
    it(`${file} uses isBackdropClick and keeps a Close button`, () => {
      const text = source(file);
      expect(text).toMatch(/isBackdropClick\(/);
      expect(text).toMatch(/aria-label="Close (diagram|image)"/);
    });
  }
});
