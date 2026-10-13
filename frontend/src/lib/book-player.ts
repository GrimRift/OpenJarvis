/**
 * The audiobook player (M42): one per app, so "read me chapter 4" said on any
 * page plays here, and the Books page shows and drives the same state.
 *
 * Decisions (user, 2026-10-13): playing follows "where you are" automatically
 * (the rolling render mode renders ahead of it); the player steps aside while
 * Sage listens, thinks or speaks and comes back after, unless the user paused
 * it themselves.
 *
 * The <audio> element lives in BookPlayerHost; this store is what it plays and
 * what the user asked for. The pure helpers below hold the decisions so they
 * can be tested without rendering anything (no jsdom here).
 */

import { create } from 'zustand';
import type { BookChapter } from './books';

export interface NowPlaying {
  book: string;
  title: string;
  index: number;
  label: string;
  voice: string;
  /** Where to start, the first time this chapter loads. */
  startAt: number;
}

interface BookPlayerState {
  now: NowPlaying | null;
  /** The user wants it playing (their play/pause, or a tool's). */
  wanted: boolean;
  /** Playing right now (false while Sage talks, even if wanted). */
  playing: boolean;
  position: number;
  duration: number;
  /** Bumped to make the host seek to `seekTo`. */
  seekTick: number;
  seekTo: number;
  play: (now: NowPlaying) => void;
  setWanted: (wanted: boolean) => void;
  seek: (seconds: number) => void;
  report: (fields: Partial<Pick<BookPlayerState, 'playing' | 'position' | 'duration'>>) => void;
  stop: () => void;
}

export const useBookPlayer = create<BookPlayerState>((set) => ({
  now: null,
  wanted: false,
  playing: false,
  position: 0,
  duration: 0,
  seekTick: 0,
  seekTo: 0,
  // The chapter already loaded just resumes: the host reloads (and measures
  // the length) only when the chapter changes, so resetting `duration` here
  // left it at 0 -- the clock read --:-- and every slider click went to 0:00.
  play: (now) =>
    set((s) =>
      s.now && s.now.book === now.book && s.now.index === now.index && s.now.voice === now.voice
        ? { wanted: true }
        : { now, wanted: true, position: now.startAt, duration: 0 },
    ),
  setWanted: (wanted) => set({ wanted }),
  seek: (seconds) => set((s) => ({ seekTick: s.seekTick + 1, seekTo: Math.max(0, seconds) })),
  report: (fields) => set(fields),
  stop: () => set({ now: null, wanted: false, playing: false, position: 0, duration: 0 }),
}));

/** Sage needs the room: the user is talking, Sage is thinking or speaking. */
export function sageNeedsTheRoom(inputs: {
  voiceState: string;
  replyAudio: boolean;
  serverSpeaking: boolean;
  streaming: boolean;
}): boolean {
  return (
    inputs.voiceState !== 'idle' || inputs.replyAudio || inputs.serverSpeaking || inputs.streaming
  );
}

/** The chapters after `index` in reading order (the list's: an added
 * chapter has a higher index than the ones around it), skipped ones left out. */
export function chaptersAfter(chapters: BookChapter[], index: number): BookChapter[] {
  const at = chapters.findIndex((c) => c.index === index);
  return chapters.slice(at + 1).filter((c) => !c.skip);
}

/** The chapter after `index` that can be played, or null at the end. */
export function nextReadyChapter(chapters: BookChapter[], index: number): BookChapter | null {
  const next = chaptersAfter(chapters, index)[0];
  return next && next.state === 'ready' ? next : null;
}

/** Seconds between saves of "where you are" while playing. */
export const SAVE_EVERY_SECONDS = 15;

/** Whether to save the position now (every SAVE_EVERY_SECONDS of listening). */
export function shouldSavePosition(position: number, lastSaved: number): boolean {
  return Math.abs(position - lastSaved) >= SAVE_EVERY_SECONDS;
}

/** What an `audiobook_play` tool call asks the player to do, if anything. */
export type PlayCommand =
  | { action: 'play'; now: NowPlaying }
  | { action: 'pause' }
  | { action: 'resume' };

export function playCommandFromToolCall(tc: {
  tool?: string;
  status?: string;
  metadata?: Record<string, unknown>;
}): PlayCommand | null {
  if (tc.tool !== 'audiobook_play' || tc.status !== 'success') return null;
  const m = tc.metadata?.audiobook_play as Record<string, unknown> | undefined;
  if (!m || typeof m !== 'object') return null;
  if (m.action === 'pause' || m.action === 'resume') return { action: m.action };
  if (m.action !== 'play' || typeof m.book !== 'string' || typeof m.index !== 'number') {
    return null;
  }
  return {
    action: 'play',
    now: {
      book: m.book,
      title: String(m.title ?? ''),
      index: m.index,
      label: String(m.label ?? `Chapter ${m.index}`),
      voice: String(m.voice ?? ''),
      startAt: typeof m.seconds === 'number' ? m.seconds : 0,
    },
  };
}

/** Apply a tool's play command to the player. */
export function applyPlayCommand(cmd: PlayCommand): void {
  const player = useBookPlayer.getState();
  if (cmd.action === 'play') player.play(cmd.now);
  else player.setWanted(cmd.action === 'resume');
}
