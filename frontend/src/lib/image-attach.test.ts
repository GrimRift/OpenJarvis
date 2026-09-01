import { describe, it, expect } from 'vitest';
import {
  MAX_IMAGES,
  MAX_IMAGE_BYTES,
  imageFilesFrom,
  isAcceptedImage,
  planAttachments,
  withoutImages,
} from './image-attach';

const png = (name: string, size = 1024) => ({ name, type: 'image/png', size });

describe('planAttachments', () => {
  it('accepts images', () => {
    const { accepted, rejected } = planAttachments([], [png('a.png')]);
    expect(accepted).toEqual([0]);
    expect(rejected).toBeNull();
  });

  it('caps the total, counting what is already attached', () => {
    const existing = Array.from({ length: 3 }, (_, i) => ({
      id: `${i}`,
      dataUrl: 'data:image/png;base64,x',
      name: `${i}.png`,
      bytes: 10,
    }));
    const { accepted, rejected } = planAttachments(existing, [
      png('d.png'),
      png('e.png'),
    ]);
    expect(accepted).toEqual([0]);
    expect(rejected).toContain(String(MAX_IMAGES));
  });

  it('refuses a non-image rather than dropping it silently', () => {
    const { accepted, rejected } = planAttachments(
      [],
      [{ name: 'notes.pdf', type: 'application/pdf', size: 10 }],
    );
    expect(accepted).toEqual([]);
    expect(rejected).toContain('notes.pdf');
  });

  it('refuses an oversized image and says how big it was', () => {
    const { accepted, rejected } = planAttachments(
      [],
      [png('huge.png', MAX_IMAGE_BYTES + 1)],
    );
    expect(accepted).toEqual([]);
    expect(rejected).toMatch(/huge\.png/);
    expect(rejected).toMatch(/limit/);
  });

  it('collapses repeated reasons from a multi-select', () => {
    const { rejected } = planAttachments(
      [],
      [
        { name: 'a.txt', type: 'text/plain', size: 1 },
        { name: 'b.txt', type: 'text/plain', size: 1 },
      ],
    );
    // Two problems, but "is not an image" should not be printed twice per file
    // in a way that buries the message.
    expect(rejected?.split(';').length).toBe(2);
  });

  it('keeps the good files from a mixed drop', () => {
    const { accepted, rejected } = planAttachments([], [
      png('good.png'),
      { name: 'bad.pdf', type: 'application/pdf', size: 1 },
    ]);
    expect(accepted).toEqual([0]);
    expect(rejected).toContain('bad.pdf');
  });
});

describe('isAcceptedImage', () => {
  it.each(['image/png', 'image/jpeg', 'image/webp', 'image/gif'])(
    'accepts %s',
    (type) => expect(isAcceptedImage(type)).toBe(true),
  );

  it.each(['application/pdf', 'text/plain', 'image/svg+xml', ''])(
    'rejects %s',
    (type) => expect(isAcceptedImage(type)).toBe(false),
  );
});

describe('imageFilesFrom', () => {
  const asFile = (name: string) => new File(['x'], name, { type: 'image/png' });

  it('takes image items from a paste', () => {
    const files = imageFilesFrom([
      { kind: 'file', type: 'image/png', getAsFile: () => asFile('shot.png') },
    ]);
    expect(files).toHaveLength(1);
  });

  it('ignores the text that rides along with a screenshot paste', () => {
    const files = imageFilesFrom([
      { kind: 'string', type: 'text/plain', getAsFile: () => null },
      { kind: 'file', type: 'image/png', getAsFile: () => asFile('shot.png') },
    ]);
    expect(files).toHaveLength(1);
  });

  it('returns nothing for a plain text paste', () => {
    expect(
      imageFilesFrom([{ kind: 'string', type: 'text/plain', getAsFile: () => null }]),
    ).toEqual([]);
  });
});

describe('withoutImages', () => {
  it('strips images before storage', () => {
    const stored = withoutImages({
      messages: [
        { id: '1', content: 'look', images: ['data:image/png;base64,AAA'] },
        { id: '2', content: 'a red square' },
      ],
    });
    expect(stored.messages[0]).not.toHaveProperty('images');
    expect(stored.messages[0].content).toBe('look');
  });

  it('leaves everything else alone', () => {
    const original = { messages: [{ id: '1', content: 'hi', images: undefined }] };
    expect(withoutImages(original)).toEqual(original);
  });

  it('handles a conversation with no messages', () => {
    expect(withoutImages({ messages: [] })).toEqual({ messages: [] });
  });
});
