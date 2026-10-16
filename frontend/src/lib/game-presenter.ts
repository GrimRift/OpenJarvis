/**
 * The game panel over the app, like the weather panel (`weather-presenter`).
 *
 * Opened from the tool event in InputArea (which the Voice page mounts too),
 * so a game asked for by voice opens the panel; every later play_game result
 * moves the board. `seen` is keyed by the tool call, so scrolling the history
 * never reopens a game. The chat card reopens it, with the game as it stands
 * now (`/v1/games/current`), not as it stood when the card was written.
 *
 * The panel talks back through the chat: `sendToChat` is set by InputArea,
 * so a click is one more message, answered and spoken like a typed move.
 * `say` is for ping pong's big moments, which are spoken but not chat turns.
 */

import { create } from 'zustand';
import { apiFetch } from './api';
import type { GamePanel } from './game-panel';

interface GamePresenterState {
  current: GamePanel | null;
  /** The panel is showing (the game can be on with the panel closed). */
  visible: boolean;
  seen: Set<string>;
  open: (panel: GamePanel) => void;
  close: () => void;
  /** A play_game result arrived: show it if it is new. */
  showNew: (key: string, panel: GamePanel) => void;
  /** Ping pong's own updates (a point), not from a tool call. */
  update: (panel: GamePanel) => void;
}

export const useGamePresenter = create<GamePresenterState>((set, get) => ({
  current: null,
  visible: false,
  seen: new Set<string>(),

  open: (panel) => {
    set({ current: panel, visible: true });
    // The card may be old: show the game as it is now, if it is the same one.
    void apiFetch('/v1/games/current')
      .then((res) => (res.ok ? res.json() : null))
      .then((body) => {
        const now = body?.game_panel as GamePanel | null | undefined;
        const shown = get().current;
        if (now && shown && now.id === shown.id) set({ current: now });
      })
      .catch(() => {});
  },

  close: () => set({ visible: false }),

  showNew: (key, panel) => {
    if (get().seen.has(key)) return;
    const seen = new Set(get().seen);
    seen.add(key);
    set({ current: panel, visible: true, seen });
  },

  update: (panel) => set({ current: panel }),
}));

type Sender = (text: string) => void;
type Speaker = (line: string) => void;

let sender: Sender | null = null;
let speaker: Speaker | null = null;

/** InputArea hands over its send and speak; null when it unmounts. */
export function setGameChat(send: Sender | null, speak: Speaker | null): void {
  sender = send;
  speaker = speak;
}

/** Send what the panel did as a chat message ("Nc3"). False if no chat is mounted. */
export function sendToChat(text: string): boolean {
  if (!sender) return false;
  sender(text);
  return true;
}

/** Say a short line in Sage's voice (if voice replies are on). */
export function sayLine(line: string): void {
  if (line.trim()) speaker?.(line);
}
