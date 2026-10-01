/**
 * Which weather report is open over the app (M41), mirroring `image-presenter`.
 *
 * Opened from the tool event in InputArea, which the Voice page mounts too.
 * `seen` is keyed by the tool call, so a forecast auto-opens once and
 * scrolling past it in the history never reopens it. The panel stays until
 * the user closes it (their choice, 2026-10-01): a week's forecast is read,
 * not glanced at while Sage says one line.
 */

import { create } from 'zustand';
import type { WeatherReport } from './weather-report';

interface WeatherPresenterState {
  current: WeatherReport | null;
  seen: Set<string>;
  open: (report: WeatherReport) => void;
  close: () => void;
  /** Show a just-fetched report if it is new and auto-open is on. True if shown. */
  showNew: (report: WeatherReport, autoOpen: boolean) => boolean;
}

export const useWeatherPresenter = create<WeatherPresenterState>((set, get) => ({
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
