/**
 * Which generated picture is open over the app (M40), mirroring
 * `diagram-presenter`.
 *
 * Opened from the tool event in InputArea, which the Voice page mounts too --
 * the Voice transcript has no chat bubble, so a card-only design would never
 * show a picture made by voice. `seen` is keyed by the image id, so a picture
 * auto-opens once and scrolling past it in the history never reopens it.
 */

import { create } from 'zustand';
import type { GeneratedImage } from './generated-image';

interface ImagePresenterState {
  current: GeneratedImage | null;
  seen: Set<string>;
  open: (image: GeneratedImage) => void;
  close: () => void;
  /** Show a just-made picture if it is new and auto-open is on. True if shown. */
  showNew: (image: GeneratedImage, autoOpen: boolean) => boolean;
}

export const useImagePresenter = create<ImagePresenterState>((set, get) => ({
  current: null,
  seen: new Set<string>(),

  open: (image) => {
    const seen = new Set(get().seen);
    seen.add(image.id);
    set({ current: image, seen });
  },

  close: () => set({ current: null }),

  showNew: (image, autoOpen) => {
    if (get().seen.has(image.id)) return false;
    const seen = new Set(get().seen);
    seen.add(image.id);
    if (!autoOpen) {
      set({ seen });
      return false;
    }
    set({ current: image, seen });
    return true;
  },
}));
