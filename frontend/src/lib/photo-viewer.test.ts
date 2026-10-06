import { describe, expect, it } from 'vitest';
import { markdownPhotos, stepIndex, usePhotoViewer } from './photo-viewer';

describe('photos in a reply open inside Sage (6 October)', () => {
  it('steps through the photos and wraps at both ends', () => {
    expect(stepIndex(0, 1, 6)).toBe(1);
    expect(stepIndex(5, 1, 6)).toBe(0);
    expect(stepIndex(0, -1, 6)).toBe(5);
    expect(stepIndex(0, 1, 0)).toBe(0);
  });

  it("finds the pictures written into an answer's text", () => {
    const content =
      "Here's the bottle:\n\n![Bujairami Hectic bottle](https://zaoud.it/cdn/hectic.jpg)\n" +
      '[a link](https://example.com) and ![](https://img.example/b.png) ' +
      '![dup](https://zaoud.it/cdn/hectic.jpg)';
    expect(markdownPhotos(content)).toEqual([
      { src: 'https://zaoud.it/cdn/hectic.jpg', description: 'Bujairami Hectic bottle' },
      { src: 'https://img.example/b.png', description: undefined },
    ]);
  });

  it('opens at the clicked photo and closes empty', () => {
    const viewer = usePhotoViewer.getState();
    viewer.open([{ src: 'a' }, { src: 'b' }, { src: 'c' }], 2);
    expect(usePhotoViewer.getState().index).toBe(2);
    usePhotoViewer.getState().step(1);
    expect(usePhotoViewer.getState().index).toBe(0);
    usePhotoViewer.getState().close();
    expect(usePhotoViewer.getState().photos).toEqual([]);
    usePhotoViewer.getState().open([], 0);
    expect(usePhotoViewer.getState().photos).toEqual([]);
  });
});
