import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ChevronLeft, ChevronRight, Loader2, Minus, Pause, Play, Plus, RotateCcw, RotateCw } from 'lucide-react';
import { clock, fetchChapterText, paragraphAt, type BookChapter, type ChapterText } from '../../lib/books';
import { useBookPlayer } from '../../lib/book-player';
import { Scrubber } from './Scrubber';

/**
 * Reading a chapter on the page, no narrator needed (user, 2026-10-13). The
 * same cleaned text the voice reads: `* * *` is drawn as a scene break and
 * the translator's notes keep their "Notes." heading. Text size is a
 * per-viewer convenience kept in localStorage.
 *
 * 2026-10-14 (user): the book player is here too, and while this chapter
 * plays the paragraph being read is lit and kept in view ("follow along").
 * A click on a paragraph plays from it. Scrolling by hand pauses the
 * following for a few seconds so the page is not pulled away.
 */

const SIZES = [15, 17, 19, 21, 23];
const SIZE_KEY = 'sage.books.readerSize';
/** After the reader scrolls by hand, leave the page alone this long. */
const HANDS_OFF_MS = 6000;

function loadSize(): number {
  try {
    const v = Number(localStorage.getItem(SIZE_KEY));
    return SIZES.includes(v) ? v : 17;
  } catch {
    return 17;
  }
}

export function ChapterReader({
  bookId,
  bookTitle,
  voice,
  chapters,
  index,
  position,
  positionSeconds,
  onIndex,
  onClose,
}: {
  bookId: string;
  bookTitle: string;
  voice: string;
  chapters: BookChapter[];
  index: number;
  position: number | null;
  positionSeconds: number;
  onIndex: (index: number) => void;
  onClose: () => void;
}) {
  const [text, setText] = useState<ChapterText | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [size, setSize] = useState(loadSize);
  const topRef = useRef<HTMLDivElement | null>(null);
  const paraRefs = useRef<Map<number, HTMLElement>>(new Map());
  const handsOffUntil = useRef(0);
  const selfScrollUntil = useRef(0);
  const player = useBookPlayer();

  const chapter = chapters.find((c) => c.index === index);
  const playingThis = player.now?.book === bookId && player.now.index === index ? player.now : null;
  const playingOther = player.now?.book === bookId && !playingThis ? player.now : null;
  const ready = chapter?.state === 'ready';

  useEffect(() => {
    let cancelled = false;
    setText(null);
    setError(null);
    fetchChapterText(bookId, index)
      .then((t) => !cancelled && setText(t))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    selfScrollUntil.current = Date.now() + 500;
    topRef.current?.scrollIntoView({ block: 'start' });
    return () => {
      cancelled = true;
    };
    // `ready` turns true when the chapter is narrated: fetch again for its timings.
  }, [bookId, index, ready]);

  // Any scroll the follow-along did not make (wheel, touch, keys, the
  // scrollbar) holds it off for a bit. Scroll events do not bubble, so they
  // are caught on the way down.
  useEffect(() => {
    const off = () => {
      if (Date.now() > selfScrollUntil.current) handsOffUntil.current = Date.now() + HANDS_OFF_MS;
    };
    document.addEventListener('scroll', off, { capture: true, passive: true });
    return () => document.removeEventListener('scroll', off, { capture: true });
  }, []);

  const current = playingThis ? paragraphAt(text?.starts ?? null, player.position) : null;
  useEffect(() => {
    if (current == null || !player.playing || Date.now() < handsOffUntil.current) return;
    selfScrollUntil.current = Date.now() + 1200; // the smooth scroll's own events
    paraRefs.current.get(current)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }, [current, player.playing]);

  const changeSize = (dir: 1 | -1) => {
    const next = SIZES[Math.min(SIZES.length - 1, Math.max(0, SIZES.indexOf(size) + dir))];
    setSize(next);
    try {
      localStorage.setItem(SIZE_KEY, String(next));
    } catch {
      /* private mode: size just isn't remembered */
    }
  };

  const order = chapters.filter((c) => !c.skip || c.index === index);
  const at = order.findIndex((c) => c.index === index);
  const prev = at > 0 ? order[at - 1] : null;
  const next = at >= 0 && at + 1 < order.length ? order[at + 1] : null;
  const iconBtn = 'p-1.5 rounded-lg cursor-pointer disabled:opacity-30 disabled:cursor-default';
  const iconStyle = { color: 'var(--color-text-secondary)' };

  const play = (startAt: number) => {
    if (!chapter || !ready) return;
    handsOffUntil.current = 0;
    if (playingThis) {
      player.seek(startAt);
      player.setWanted(true);
    } else {
      player.play({ book: bookId, title: bookTitle, index, label: chapter.label, voice, startAt });
    }
  };
  const toggle = () => {
    if (playingThis) player.setWanted(!player.wanted);
    else play(index === position ? positionSeconds : 0);
  };
  const startOf = (para: number) => text?.starts?.find(([p]) => p === para)?.[1] ?? null;

  const posNow = playingThis ? player.position : index === position ? positionSeconds : 0;
  const duration = (playingThis && player.duration) || chapter?.seconds || 0;
  const pct = duration ? Math.min(100, (posNow / duration) * 100) : 0;

  const nav = (
    <div className="flex items-center gap-2">
      <button className={iconBtn} style={iconStyle} disabled={!prev} onClick={() => prev && onIndex(prev.index)} title={prev ? prev.label : ''}>
        <ChevronLeft size={18} />
      </button>
      <button className={iconBtn} style={iconStyle} disabled={!next} onClick={() => next && onIndex(next.index)} title={next ? next.label : ''}>
        <ChevronRight size={18} />
      </button>
    </div>
  );

  return (
    <div ref={topRef}>
      <div className="flex items-center gap-3 sticky top-0 py-2 z-10" style={{ background: 'var(--color-bg)' }}>
        <button className="inline-flex items-center gap-1.5 text-[13px] cursor-pointer" style={{ color: 'var(--color-text-secondary)' }} onClick={onClose}>
          <ArrowLeft size={15} /> {bookTitle}
        </button>
        <span className="ml-auto" />
        <button className={iconBtn} style={iconStyle} onClick={() => changeSize(-1)} title="Smaller text"><Minus size={16} /></button>
        <button className={iconBtn} style={iconStyle} onClick={() => changeSize(1)} title="Larger text"><Plus size={16} /></button>
        {nav}
      </div>

      <article className="mx-auto py-6" style={{ maxWidth: '40rem', color: 'var(--color-text)' }}>
        <h1 className="text-2xl font-semibold mb-1">{chapter?.label ?? text?.label}</h1>
        {text && text.title !== text.label && !text.label.includes(text.title) && (
          <div className="text-[15px] mb-1" style={{ color: 'var(--color-text-secondary)' }}>{text.title}</div>
        )}
        {text && (
          <div className="text-[12px] mb-8" style={{ color: 'var(--color-text-tertiary)' }}>
            {text.words.toLocaleString()} words · about {Math.max(1, Math.round(text.words / 230))} min to read
            {ready && text.starts && ' · click a paragraph to listen from there'}
          </div>
        )}
        {error && <div style={{ color: 'var(--color-error)' }}>{error}</div>}
        {!text && !error && <Loader2 className="animate-spin" size={18} style={{ color: 'var(--color-text-tertiary)' }} />}
        {text?.paragraphs.map((p, i) => {
          if (p === '* * *')
            return (
              <div key={i} className="text-center my-8 tracking-[0.6em]" style={{ color: 'var(--color-text-tertiary)' }}>
                * * *
              </div>
            );
          if (p === 'Notes.')
            return (
              <h2 key={i} className="text-[13px] font-semibold uppercase tracking-wide mt-10 mb-3" style={{ color: 'var(--color-text-secondary)' }}>
                Notes
              </h2>
            );
          const lit = i === current;
          const start = ready ? startOf(i) : null;
          return (
            <p
              key={i}
              ref={(el) => {
                if (el) paraRefs.current.set(i, el);
                else paraRefs.current.delete(i);
              }}
              className="mb-4 -mx-3 px-3 rounded-md transition-colors"
              style={{
                fontSize: size,
                lineHeight: 1.7,
                background: lit ? 'var(--color-accent-subtle)' : undefined,
                boxShadow: lit ? 'inset 2px 0 0 var(--color-accent)' : undefined,
                cursor: start != null ? 'pointer' : undefined,
              }}
              onClick={() => start != null && !window.getSelection()?.toString() && play(start)}
            >
              {p}
            </p>
          );
        })}
        {text && (
          <div className="flex justify-between items-center mt-12 pt-6 text-[13px]" style={{ borderTop: '1px solid var(--color-border)' }}>
            <button className="cursor-pointer disabled:opacity-30" style={{ color: 'var(--color-text-secondary)' }} disabled={!prev} onClick={() => prev && onIndex(prev.index)}>
              {prev ? `← ${prev.label}` : ''}
            </button>
            <button className="cursor-pointer disabled:opacity-30" style={{ color: 'var(--color-accent)' }} disabled={!next} onClick={() => next && onIndex(next.index)}>
              {next ? `${next.label} →` : 'The end'}
            </button>
          </div>
        )}
      </article>

      {/* the book player, here too (user, 2026-10-14) */}
      <div
        className="sticky bottom-0 mt-4 flex items-center gap-3 px-4 py-2.5 rounded-xl"
        style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
      >
        {ready ? (
          <>
            <button className="cursor-pointer" style={iconStyle} onClick={() => playingThis && player.seek(posNow - 15)} title="Back 15 s"><RotateCcw size={16} /></button>
            <button className="w-8 h-8 rounded-full grid place-items-center cursor-pointer" style={{ background: 'var(--color-accent)', color: 'var(--color-on-accent)' }} onClick={toggle} title={playingThis && player.wanted ? 'Pause' : 'Listen'}>
              {playingThis && player.wanted ? <Pause size={14} /> : <Play size={14} />}
            </button>
            <button className="cursor-pointer" style={iconStyle} onClick={() => playingThis && player.seek(posNow + 15)} title="Forward 15 s"><RotateCw size={16} /></button>
            <div className="text-[12.5px] whitespace-nowrap" style={{ color: 'var(--color-text)' }}>
              <b>{chapter?.label}</b>
              {playingThis && player.wanted && !player.playing && (
                <span style={{ color: 'var(--color-text-tertiary)' }}> · paused while Sage talks</span>
              )}
            </div>
            <div className="flex-1 flex items-center">
              <Scrubber className="w-full" pct={pct} disabled={!duration} onSeek={(f) => play(f * duration)} />
            </div>
            <span className="text-[12px] tabular-nums" style={{ color: 'var(--color-text-tertiary)' }}>{clock(posNow)} / {clock(duration || null)}</span>
          </>
        ) : (
          <span className="text-[12.5px]" style={{ color: 'var(--color-text-tertiary)' }}>
            {chapter?.label ?? 'This chapter'} isn't narrated yet{chapter?.state === 'queued' ? ' (queued)' : chapter?.state === 'rendering' ? ' (being made now)' : ''}.
          </span>
        )}
        {playingOther && (
          <button
            className="text-[12px] cursor-pointer whitespace-nowrap"
            style={{ color: 'var(--color-accent)' }}
            onClick={() => onIndex(playingOther.index)}
            title="Read the chapter that is playing"
          >
            Playing {playingOther.label} · read along →
          </button>
        )}
      </div>
    </div>
  );
}
