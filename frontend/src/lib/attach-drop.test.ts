import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { ATTACH_ACCEPT, splitAttachFiles } from './image-attach';

const here = dirname(fileURLToPath(import.meta.url));
const source = (path: string) => readFileSync(resolve(here, path), 'utf8');

const file = (name: string, type: string) => ({ name, type, size: 10 });

describe('one Attach button for pictures and documents (29 September)', () => {
  it('the picker offers images as well as documents', () => {
    for (const ext of ['.png', '.jpg', '.jpeg', '.webp', '.gif', '.pdf', '.docx', '.txt', '.md', '.csv']) {
      expect(ATTACH_ACCEPT.split(','), ext).toContain(ext);
    }
  });

  it('pictures go to the image path, documents to the reader', () => {
    const split = splitAttachFiles([
      file('photo.PNG', 'image/png'),
      file('paper.pdf', 'application/pdf'),
      file('notes.md', ''),
      file('shot.jpg', 'image/jpeg'),
      file('movie.mp4', 'video/mp4'),
    ]);
    expect(split.images.map((f) => f.name)).toEqual(['photo.PNG', 'shot.jpg']);
    expect(split.documents.map((f) => f.name)).toEqual(['paper.pdf', 'notes.md']);
    expect(split.unsupported.map((f) => f.name)).toEqual(['movie.mp4']);
  });

  it('the file input uses that list', () => {
    expect(source('../components/Chat/InputArea.tsx')).toMatch(/accept=\{ATTACH_ACCEPT\}/);
  });
});

describe('dropping a file reaches the page (29 September)', () => {
  // Tauri 2 handles file drops natively by default, and the page's drop
  // events never fire: dragging a picture into the app did nothing.
  it('the app window turns off the native drag-drop handler', () => {
    expect(source('../../src-tauri/src/lib.rs')).toMatch(/\.disable_drag_drop_handler\(\)/);
  });

  it('a drop anywhere on the page is caught, not only on the message box', () => {
    const text = source('../components/Chat/InputArea.tsx');
    expect(text).toMatch(/window\.addEventListener\('drop'/);
    expect(text).toMatch(/window\.addEventListener\('dragover'/);
  });
});
