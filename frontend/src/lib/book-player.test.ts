import { beforeEach, describe, expect, it } from 'vitest';
import {
  applyPlayCommand,
  nextReadyChapter,
  playCommandFromToolCall,
  sageNeedsTheRoom,
  shouldSavePosition,
  useBookPlayer,
} from './book-player';
import { clock, workerLine, type BookChapter } from './books';

const ch = (index: number, state: BookChapter['state'], skip = false): BookChapter => ({
  index,
  label: `Chapter ${index}`,
  number: String(index),
  title: `Chapter ${index}`,
  words: 2000,
  skip,
  skip_reason: skip ? 'front matter' : null,
  state,
  seconds: state === 'ready' ? 1300 : null,
  estimate_seconds: 784,
  flags: 0,
});

describe('book player decisions', () => {
  beforeEach(() => useBookPlayer.getState().stop());

  it('steps aside whenever Sage is listening, thinking or speaking', () => {
    const idle = { voiceState: 'idle', replyAudio: false, serverSpeaking: false, streaming: false };
    expect(sageNeedsTheRoom(idle)).toBe(false);
    expect(sageNeedsTheRoom({ ...idle, voiceState: 'recording' })).toBe(true);
    expect(sageNeedsTheRoom({ ...idle, streaming: true })).toBe(true);
    expect(sageNeedsTheRoom({ ...idle, replyAudio: true })).toBe(true);
    expect(sageNeedsTheRoom({ ...idle, serverSpeaking: true })).toBe(true);
  });

  it('plays on to the next made chapter, skipping front matter, and stops at a gap', () => {
    const chapters = [ch(1, 'skipped', true), ch(2, 'ready'), ch(3, 'ready'), ch(4, 'queued')];
    expect(nextReadyChapter(chapters, 2)?.index).toBe(3);
    expect(nextReadyChapter(chapters, 3)).toBeNull();
    expect(nextReadyChapter(chapters, 4)).toBeNull();
  });

  it('saves where you are every 15 s of listening', () => {
    expect(shouldSavePosition(10, 0)).toBe(false);
    expect(shouldSavePosition(15, 0)).toBe(true);
    expect(shouldSavePosition(0, 40)).toBe(true); // a seek back
  });

  it('turns an audiobook_play tool call into a player command', () => {
    const tc = {
      tool: 'audiobook_play',
      status: 'success',
      metadata: {
        audiobook_play: { action: 'play', book: 'b-1', title: 'T', index: 4, label: 'Chapter 4', voice: 'lotm-narrator', seconds: 90 },
      },
    };
    const cmd = playCommandFromToolCall(tc);
    expect(cmd).toEqual({
      action: 'play',
      now: { book: 'b-1', title: 'T', index: 4, label: 'Chapter 4', voice: 'lotm-narrator', startAt: 90 },
    });
    applyPlayCommand(cmd!);
    expect(useBookPlayer.getState().now?.index).toBe(4);
    expect(useBookPlayer.getState().wanted).toBe(true);
    applyPlayCommand(playCommandFromToolCall({ ...tc, metadata: { audiobook_play: { action: 'pause' } } })!);
    expect(useBookPlayer.getState().wanted).toBe(false);
    expect(playCommandFromToolCall({ ...tc, status: 'error' })).toBeNull();
    expect(playCommandFromToolCall({ ...tc, tool: 'weather' })).toBeNull();
  });
});

describe('books helpers', () => {
  it('formats clocks', () => {
    expect(clock(1348.6)).toBe('22:29');
    expect(clock(3849)).toBe('1:04:09');
    expect(clock(null)).toBe('--:--');
  });

  it('says what the worker is doing', () => {
    const chapters = [ch(4, 'rendering')];
    expect(workerLine({ state: 'rendering', book: 'b', chapter: 4 }, 'b', chapters)).toBe('Rendering Chapter 4');
    expect(workerLine({ state: 'waiting', reason: 'Sage is speaking' }, 'b', chapters)).toBe('Waiting: Sage is speaking');
    expect(workerLine({ state: 'unknown' }, 'b', chapters)).toBe('Render worker not running');
  });
});

describe('playing the chapter already loaded', () => {
  beforeEach(() => useBookPlayer.getState().stop());

  it('resumes it and keeps its measured length (slider clicks went to 0:00)', () => {
    const now = { book: 'b', title: 'T', index: 1, label: 'Chapter 1', voice: 'v', startAt: 0 };
    const player = useBookPlayer.getState();
    player.play(now);
    player.report({ duration: 1348.6, position: 2 });
    player.setWanted(false);
    useBookPlayer.getState().play({ ...now, startAt: 2 });
    const s = useBookPlayer.getState();
    expect(s.duration).toBe(1348.6);
    expect(s.wanted).toBe(true);
    useBookPlayer.getState().play({ ...now, index: 2, label: 'Chapter 2' });
    expect(useBookPlayer.getState().duration).toBe(0); // a new chapter is measured afresh
  });
});
