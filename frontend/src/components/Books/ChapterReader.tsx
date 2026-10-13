import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ChevronLeft, ChevronRight, Loader2, Minus, Play, Plus } from 'lucide-react';
import { fetchChapterText, type BookChapter, type ChapterText } from '../../lib/books';

/**
 * Reading a chapter on the page, no narrator needed (user, 2026-10-13). The
 * same cleaned text the voice reads: `* * *` is drawn as a scene break and
 * the translator's notes keep their "Notes." heading. Text size is a
 * per-viewer convenience kept in localStorage.
 */

const SIZES = [15, 17, 19, 21, 23];
const SIZE_KEY = 'sage.books.readerSize';

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
  chapters,
  index,
  onIndex,
  onClose,
  onListen,
}: {
  bookId: string;
  bookTitle: string;
  chapters: BookChapter[];
  index: number;
  onIndex: (index: number) => void;
  onClose: () => void;
  onListen: (chapter: BookChapter) => void;
}) {
  const [text, setText] = useState<ChapterText | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [size, setSize] = useState(loadSize);
  const topRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    setText(null);
    setError(null);
    fetchChapterText(bookId, index)
      .then((t) => !cancelled && setText(t))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    topRef.current?.scrollIntoView({ block: 'start' });
    return () => {
      cancelled = true;
    };
  }, [bookId, index]);

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
  const chapter = chapters.find((c) => c.index === index);
  const iconBtn = 'p-1.5 rounded-lg cursor-pointer disabled:opacity-30 disabled:cursor-default';
  const iconStyle = { color: 'var(--color-text-secondary)' };

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
        {chapter?.state === 'ready' && (
          <button
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12.5px] cursor-pointer"
            style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}
            onClick={() => onListen(chapter)}
          >
            <Play size={13} /> Listen instead
          </button>
        )}
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
          </div>
        )}
        {error && <div style={{ color: 'var(--color-error)' }}>{error}</div>}
        {!text && !error && <Loader2 className="animate-spin" size={18} style={{ color: 'var(--color-text-tertiary)' }} />}
        {text?.paragraphs.map((p, i) =>
          p === '* * *' ? (
            <div key={i} className="text-center my-8 tracking-[0.6em]" style={{ color: 'var(--color-text-tertiary)' }}>
              * * *
            </div>
          ) : p === 'Notes.' ? (
            <h2 key={i} className="text-[13px] font-semibold uppercase tracking-wide mt-10 mb-3" style={{ color: 'var(--color-text-secondary)' }}>
              Notes
            </h2>
          ) : (
            <p key={i} className="mb-4" style={{ fontSize: size, lineHeight: 1.7 }}>
              {p}
            </p>
          ),
        )}
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
    </div>
  );
}
