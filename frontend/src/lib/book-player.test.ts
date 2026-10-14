import { beforeEach, describe, expect, it } from 'vitest';
import {
  applyPlayCommand,
  nextReadyChapter,
  playCommandFromToolCall,
  readerAfterChapterEnd,
  sageNeedsTheRoom,
  shouldSavePosition,
  useBookPlayer,
} from './book-player';
import { clock, paragraphAt, workerLine, type BookChapter } from './books';

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

describe('added chapters and follow-along (2026-10-14)', () => {
  it('goes on in list order, so an added chapter 3 (index 9) comes after 2', () => {
    const chapters = [ch(1, 'ready'), ch(2, 'ready'), ch(9, 'ready'), ch(3, 'ready')];
    expect(nextReadyChapter(chapters, 2)?.index).toBe(9);
    expect(nextReadyChapter(chapters, 9)?.index).toBe(3);
  });

  it('lights the last paragraph that has started', () => {
    const starts: [number, number][] = [[0, 1.8], [1, 4.4], [3, 6.9]];
    expect(paragraphAt(starts, 0.5)).toBeNull();
    expect(paragraphAt(starts, 4.4)).toBe(1);
    expect(paragraphAt(starts, 6)).toBe(1);
    expect(paragraphAt(starts, 100)).toBe(3);
    expect(paragraphAt(null, 5)).toBeNull();
  });
});

describe('the reader turns the page with the voice (2026-10-14)', () => {
  beforeEach(() => useBookPlayer.getState().stop());

  const end = (from: number, next: number | null, playing: boolean) => {
    useBookPlayer.getState().chapterEnded({
      book: 'b',
      from,
      next: next == null ? null : { index: next, label: `Chapter ${next}` },
      playing,
    });
    return useBookPlayer.getState().ended;
  };

  it('turns to the next chapter when it is playing', () => {
    expect(readerAfterChapterEnd(end(66, 67, true), 'b', 66)).toEqual({ turnTo: 67 });
  });

  it('stays and says so when the next one is not narrated yet', () => {
    expect(readerAfterChapterEnd(end(66, 67, false), 'b', 66)).toEqual({ notMade: 'Chapter 67' });
  });

  it('leaves a reader on another chapter or book alone', () => {
    const e = end(66, 67, true);
    expect(readerAfterChapterEnd(e, 'b', 40)).toBeNull();
    expect(readerAfterChapterEnd(e, 'other', 66)).toBeNull();
    expect(readerAfterChapterEnd(end(372, null, false), 'b', 372)).toBeNull();
    expect(readerAfterChapterEnd(null, 'b', 66)).toBeNull();
  });

  it('counts every end, so the same end twice is news again', () => {
    const first = end(66, 67, true)!.tick;
    expect(end(66, 67, true)!.tick).toBe(first + 1);
  });
});
