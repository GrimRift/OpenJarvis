/**
 * The photo open over the app, and the others it can step to.
 *
 * Search photos, pictures in an answer's text and attached images used to
 * open in Opera: a click took the user out of Sage to look at a picture of
 * the thing they had just asked about. They open here instead, over the app
 * like a generated picture, and close only when the user closes them
 * (Esc, Close, or a click outside -- the user's choice, 6 October).
 */

import { create } from 'zustand';

export interface ViewerPhoto {
  src: string;
  description?: string;
  /** The page it came from; offered as "Open source page" in Opera. */
  page?: string;
}

/** *index* moved by *delta*, wrapping round at either end. */
export function stepIndex(index: number, delta: number, count: number): number {
  if (count <= 0) return 0;
  return (((index + delta) % count) + count) % count;
}

/** Remote pictures written into an answer's text as ![alt](url). */
export function markdownPhotos(content: string): ViewerPhoto[] {
  const photos: ViewerPhoto[] = [];
  const seen = new Set<string>();
  for (const match of content.matchAll(/!\[([^\]]*)\]\((https?:\/\/[^\s)]+)\)/g)) {
    const src = match[2];
    if (seen.has(src)) continue;
    seen.add(src);
    photos.push({ src, description: match[1].trim() || undefined });
  }
  return photos;
}

interface PhotoViewerState {
  photos: ViewerPhoto[];
  index: number;
  open: (photos: ViewerPhoto[], index: number) => void;
  step: (delta: number) => void;
  close: () => void;
}

export const usePhotoViewer = create<PhotoViewerState>((set, get) => ({
  photos: [],
  index: 0,
  open: (photos, index) => {
    if (photos.length === 0) return;
    set({ photos, index: Math.max(0, Math.min(index, photos.length - 1)) });
  },
  step: (delta) => {
    const { photos, index } = get();
    set({ index: stepIndex(index, delta, photos.length) });
  },
  close: () => set({ photos: [], index: 0 }),
}));
