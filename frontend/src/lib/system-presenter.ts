/**
 * Which system panel is open over the app (M41), mirroring `weather-presenter`.
 * Opened from the tool event in InputArea (Chat and Voice pages), from the
 * chat card, or from the Dashboard tile. Stays until the user closes it.
 */

import { create } from 'zustand';
import type { SystemReport } from './system-report';

interface SystemPresenterState {
  current: SystemReport | null;
  seen: Set<string>;
  open: (report: SystemReport) => void;
  close: () => void;
  showNew: (report: SystemReport, autoOpen: boolean) => boolean;
}

export const useSystemPresenter = create<SystemPresenterState>((set, get) => ({
  current: null,
  seen: new Set<string>(),

  open: (report) => {
    const seen = new Set(get().seen);
    seen.add(report.key);
    set({ current: report, seen });
  },

  close: () => set({ current: null }),

  showNew: (report, autoOpen) => {
    if (get().seen.has(report.key)) return false;
    const seen = new Set(get().seen);
    seen.add(report.key);
    if (!autoOpen) {
      set({ seen });
      return false;
    }
    set({ current: report, seen });
    return true;
  },
}));
