import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  BookOpen,
  Loader2,
  Package,
  Pause,
  Play,
  Plus,
  RotateCcw,
  RotateCw,
  SkipBack,
  SkipForward,
  Smartphone,
  Upload,
  Volume2,
  X,
} from 'lucide-react';
import { fetchSpeechVoices, type LocalVoiceInfo } from '../lib/api';
import {
  cancelJob,
  chapterSpec,
  clock,
  coverPath,
  exportBook,
  fetchBlobUrl,
  fetchBook,
  fetchBooks,
  importBook,
  queueRender,
  redoChapter,
  sendToPhone,
  setBookVoice,
  updateQueue,
  workerLine,
  type BookChapter,
  type BookDetail,
  type BookSummary,
  type Delivery,
} from '../lib/books';
import { useBookPlayer } from '../lib/book-player';
import { ChapterReader } from '../components/Books/ChapterReader';
import { ChapterFlags } from '../components/Books/ChapterFlags';
import { AddChapters } from '../components/Books/AddChapters';
import { Scrubber } from '../components/Books/Scrubber';

/**
 * Books page (M42 step 5). Layout "M1", picked by the user from mockups on
 * 2026-10-13: the book list on the left; the open book starts with a big
 * now-listening player, then what the render worker is doing, one control
 * strip and the chapter table; a mini player sticks to the bottom once the
 * big one scrolls away. Export M4B / Send to phone use step 4 (export.py).
 */

const POLL_MS = 5000;
const SPEEDS = [0.5, 2 / 3, 0.75, 1];
/** Minutes; 0.5 = 30 s (user, 2026-10-14: a shorter break for faster books). */
const BREAKS = [0, 0.5, 2, 5, 10];
const breakLabel = (m: number) => (m === 0 ? 'none' : m < 1 ? `${Math.round(m * 60)} s` : `${m} min`);

const STATE_PILL: Record<string, { label: string; color: string; bg: string }> = {
  ready: { label: 'ready', color: 'var(--color-success)', bg: 'rgba(61,220,151,.1)' },
  rendering: { label: 'rendering', color: 'var(--color-accent)', bg: 'var(--color-accent-subtle)' },
  queued: { label: 'queued', color: 'var(--color-warning)', bg: 'var(--color-accent-amber-subtle)' },
  skipped: { label: 'skipped', color: 'var(--color-text-tertiary)', bg: 'var(--color-bg-tertiary)' },
  'not made': { label: 'not made', color: 'var(--color-text-tertiary)', bg: 'var(--color-bg-tertiary)' },
};

function useBlobUrl(path: string | null): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!path) return;
    let made: string | null = null;
    let cancelled = false;
    fetchBlobUrl(path)
      .then((u) => {
        if (cancelled) URL.revokeObjectURL(u);
        else {
          made = u;
          setUrl(u);
        }
      })
      .catch(() => setUrl(null));
    return () => {
      cancelled = true;
      if (made) URL.revokeObjectURL(made);
    };
  }, [path]);
  return url;
}

function Cover({ book, size }: { book: { id: string; has_cover: boolean }; size: [number, number] }) {
  const url = useBlobUrl(book.has_cover ? coverPath(book.id) : null);
  const [w, h] = size;
  return url ? (
    <img src={url} alt="" className="rounded-md object-cover shrink-0" style={{ width: w, height: h, boxShadow: '0 8px 24px rgba(0,0,0,.45)' }} />
  ) : (
    <div className="rounded-md grid place-items-center shrink-0" style={{ width: w, height: h, background: 'var(--color-bg-tertiary)', color: 'var(--color-text-tertiary)' }}>
      <BookOpen size={Math.min(w, h) / 3} />
    </div>
  );
}

function Pill({ state }: { state: string }) {
  const s = STATE_PILL[state] ?? STATE_PILL['not made'];
  return (
    <span className="text-[11px] px-2 py-0.5 rounded-full whitespace-nowrap" style={{ color: s.color, background: s.bg }}>
      {s.label}
    </span>
  );
}

const btn = 'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12.5px] transition-colors cursor-pointer disabled:cursor-default';
const btnStyle = { background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text)' };
const priStyle = { background: 'var(--color-accent)', color: 'var(--color-on-accent)', border: '1px solid transparent' };
const selectStyle = { background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text)' };

export function BooksPage() {
  const [books, setBooks] = useState<BookSummary[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [book, setBook] = useState<BookDetail | null>(null);
  const [voices, setVoices] = useState<LocalVoiceInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const refresh = useCallback(async () => {
    try {
      const list = await fetchBooks();
      setBooks(list.books);
      const id = openId ?? list.books[0]?.id ?? null;
      if (id && id !== openId) setOpenId(id);
      if (id) setBook(await fetchBook(id));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [openId]);

  useEffect(() => {
    void refresh();
    const t = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(t);
  }, [refresh]);

  useEffect(() => {
    fetchSpeechVoices()
      .then((v) => setVoices(v.chatterbox.voices.filter((x) => x.has_reference)))
      .catch(() => setVoices([]));
  }, []);

  const act = async (what: string, fn: () => Promise<unknown>) => {
    setBusy(what);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const onImport = (file: File | undefined) => {
    if (!file) return;
    void act('import', async () => {
      const made = await importBook(file);
      setOpenId(made.id);
    });
  };

  return (
    <div ref={scrollRef} className="h-full overflow-y-auto" style={{ background: 'var(--color-bg)' }}>
      <div className="books-layout max-w-6xl mx-auto px-6 py-6 grid gap-6" style={{ gridTemplateColumns: 'minmax(180px, 230px) 1fr' }}>
        <aside>
          <div className="flex items-center mb-3">
            <h1 className="text-lg font-semibold" style={{ color: 'var(--color-text)' }}>Books</h1>
            <button className={`${btn} ml-auto`} style={btnStyle} onClick={() => fileRef.current?.click()} title="Import a PDF or EPUB">
              {busy === 'import' ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
            </button>
            <input ref={fileRef} type="file" accept=".pdf,.epub" className="hidden" onChange={(e) => onImport(e.target.files?.[0])} />
          </div>
          <div className="flex flex-col gap-1">
            {books.map((b) => (
              <button
                key={b.id}
                onClick={() => setOpenId(b.id)}
                className="grid items-center gap-2.5 p-2 rounded-lg text-left cursor-pointer"
                style={{
                  gridTemplateColumns: '40px 1fr',
                  background: b.id === openId ? 'var(--color-bg-secondary)' : 'transparent',
                  boxShadow: b.id === openId ? 'inset 2px 0 0 var(--color-accent)' : undefined,
                }}
              >
                <Cover book={b} size={[40, 52]} />
                <div className="min-w-0">
                  <div className="text-[12.5px] font-semibold truncate" style={{ color: 'var(--color-text)' }}>{b.title}</div>
                  <div className="text-[11.5px] tabular-nums" style={{ color: 'var(--color-text-tertiary)' }}>
                    {b.rendered} / {b.chapters} ready
                  </div>
                </div>
              </button>
            ))}
            <button
              onClick={() => fileRef.current?.click()}
              className="grid items-center gap-2.5 p-2 rounded-lg text-left text-[12.5px] cursor-pointer"
              style={{ gridTemplateColumns: '40px 1fr', border: '1px dashed var(--color-border)', color: 'var(--color-text-tertiary)' }}
            >
              <div className="grid place-items-center" style={{ height: 52 }}><Plus size={16} /></div>
              Import a PDF or EPUB
            </button>
          </div>
        </aside>

        <main className="min-w-0">
          {error && (
            <div className="mb-3 text-[12.5px] px-3 py-2 rounded-lg flex items-center gap-2" style={{ background: 'rgba(255,107,107,.08)', color: 'var(--color-error)' }}>
              {error}
              <button className="ml-auto cursor-pointer" onClick={() => setError(null)}><X size={14} /></button>
            </div>
          )}
          {book ? (
            <BookView book={book} voices={voices} busy={busy} act={act} scrollRef={scrollRef} />
          ) : (
            <div className="text-sm py-20 text-center" style={{ color: 'var(--color-text-tertiary)' }}>
              No books yet. Import a PDF or EPUB you own, or ask Sage: “make an audiobook of D:\Downloads\book.epub”.
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

function BookView({
  book,
  voices,
  busy,
  act,
  scrollRef,
}: {
  book: BookDetail;
  voices: LocalVoiceInfo[];
  busy: string | null;
  act: (what: string, fn: () => Promise<unknown>) => Promise<void>;
  scrollRef: React.RefObject<HTMLDivElement | null>;
}) {
  const player = useBookPlayer();
  const heroObserver = useRef<IntersectionObserver | null>(null);
  const [heroVisible, setHeroVisible] = useState(true);
  const [mode, setMode] = useState<'range' | 'ahead' | 'book'>('ahead');
  const [range, setRange] = useState('');
  const [ahead, setAhead] = useState(5);
  const [reading, setReadingState] = useState<number | null>(null);
  const [flagsOpen, setFlagsOpen] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  // The page and the reader share one scroller: the book page gets its own
  // place back when the reader closes (user, 2026-10-14), the reader opens
  // at its top.
  const pageScroll = useRef<number | null>(null);
  const setReading = (index: number | null) => {
    if (reading == null && index != null) pageScroll.current = scrollRef.current?.scrollTop ?? 0;
    setReadingState(index);
  };
  useEffect(() => {
    if (reading != null || pageScroll.current == null) return;
    const top = pageScroll.current;
    pageScroll.current = null;
    requestAnimationFrame(() => scrollRef.current?.scrollTo({ top }));
  }, [reading, scrollRef]);
  const [sendOpen, setSendOpen] = useState(false);
  const [sendSpec, setSendSpec] = useState('');
  const [exported, setExported] = useState<string[] | null>(null);
  const delivery = book.delivery;
  const sendingNow = delivery?.sending?.state === 'sending';

  // A callback ref, not a mount effect: the big player is a new element every
  // time the reader closes, and an observer left on the old one reported it
  // off screen, so the mini player showed under a visible big one.
  const heroRef = useCallback((el: HTMLDivElement | null) => {
    heroObserver.current?.disconnect();
    heroObserver.current = null;
    setHeroVisible(true);
    if (!el) return;
    const io = new IntersectionObserver(([e]) => setHeroVisible(e.isIntersecting), { threshold: 0.15 });
    io.observe(el);
    heroObserver.current = io;
  }, []);
  useEffect(() => () => heroObserver.current?.disconnect(), []);

  const playingHere = player.now?.book === book.id ? player.now : null;
  const ready = book.chapters.filter((c) => c.state === 'ready');
  const narrated = book.chapters.filter((c) => !c.skip);
  const here = book.chapters.find((c) => c.index === (playingHere?.index ?? book.position));
  const current: BookChapter | undefined = here ?? ready[0];
  const totalReady = ready.reduce((s, c) => s + (c.seconds ?? 0), 0);
  // Send box prefill (user's choice): the next ready chapters from where you are.
  const ahead3 = ready.filter((c) => c.index >= (book.position ?? 0)).slice(0, 3);
  const nextUnheard = chapterSpec(ahead3.length ? ahead3 : ready.slice(0, 3));
  const w = book.worker;
  const workingHere = w.book === book.id && ['rendering', 'waiting', 'starting'].includes(w.state);
  const counts = useMemo(() => {
    const by: Record<string, number> = {};
    for (const c of book.chapters) by[c.state] = (by[c.state] ?? 0) + 1;
    return by;
  }, [book.chapters]);

  const playChapter = (c: BookChapter, startAt = 0) =>
    player.play({ book: book.id, title: book.title, index: c.index, label: c.label, voice: book.voice, startAt });

  /** Play `c` from `at` seconds: a jump when it is already loaded (play()
   * only resumes a loaded chapter, so flag buttons did nothing, 2026-10-14). */
  const playFrom = (c: BookChapter, at: number) => {
    if (playingHere?.index === c.index) {
      player.seek(at);
      player.setWanted(true);
    } else {
      playChapter(c, at);
    }
  };

  const togglePlay = () => {
    if (playingHere) player.setWanted(!player.wanted);
    else if (current && current.state === 'ready')
      playChapter(current, current.index === book.position ? book.position_seconds : 0);
  };

  const step = (dir: 1 | -1) => {
    const order = narrated;
    const i = order.findIndex((c) => c.index === current?.index);
    const target = order[i + dir];
    if (target && target.state === 'ready') playChapter(target);
  };

  const position = playingHere ? player.position : current?.index === book.position ? book.position_seconds : 0;
  // The player's own measure once it has one, else the chapter's known length.
  const duration = (playingHere && player.duration) || current?.seconds || 0;
  const pct = duration ? Math.min(100, (position / duration) * 100) : 0;
  const canSeek = !!current && current.state === 'ready' && duration > 0;

  /** Clicking the slider jumps there and plays (user's choice, 2026-10-13). */
  const seekTo = (fraction: number) => {
    if (!current || !canSeek) return;
    const target = fraction * duration;
    if (playingHere && playingHere.index === current.index) {
      player.seek(target);
      player.setWanted(true);
    } else {
      playChapter(current, target);
    }
  };

  if (reading != null) {
    return (
      <ChapterReader
        bookId={book.id}
        bookTitle={book.title}
        voice={book.voice}
        chapters={book.chapters}
        index={reading}
        position={book.position}
        positionSeconds={book.position_seconds}
        onIndex={setReading}
        onClose={() => setReading(null)}
      />
    );
  }

  const queue = () =>
    act('queue', () =>
      queueRender(book.id, mode === 'ahead' ? { ahead } : mode === 'range' ? { chapters: range } : {}),
    );

  const send = () =>
    act('send', async () => {
      await sendToPhone(book.id, sendSpec.trim());
      setSendOpen(false);
    });

  return (
    <div>
      {/* now listening */}
      <div ref={heroRef} className="books-hero grid gap-6 items-end" style={{ gridTemplateColumns: '150px 1fr' }}>
        <Cover book={book} size={[150, 194]} />
        <div className="min-w-0">
          <div className="text-[11.5px] uppercase tracking-wider" style={{ color: 'var(--color-text-tertiary)' }}>
            {playingHere ? 'Now listening' : 'Up next'} · {voices.find((v) => v.name === book.voice)?.label || book.voice}
          </div>
          <div className="text-[28px] font-semibold leading-tight" style={{ color: 'var(--color-text)' }}>
            {current ? current.label : 'Nothing made yet'}
          </div>
          <div className="text-[13px]" style={{ color: 'var(--color-text-secondary)' }}>
            {book.title}{book.author ? ` · ${book.author}` : ''} · {narrated.length} chapters ·{' '}
            {ready.length} ready ({clock(totalReady)})
          </div>
          <Scrubber pct={pct} disabled={!canSeek} onSeek={seekTo} />
          <div className="flex justify-between text-[12px] tabular-nums" style={{ color: 'var(--color-text-tertiary)' }}>
            <span>{clock(position)}</span>
            <span>{clock(duration || null)}</span>
          </div>
          <div className="flex items-center gap-5 mt-3" style={{ color: 'var(--color-text-secondary)' }}>
            <button className="cursor-pointer" onClick={() => step(-1)} title="Previous chapter"><SkipBack size={18} /></button>
            <button className="cursor-pointer" onClick={() => playingHere && player.seek(position - 15)} title="Back 15 s"><RotateCcw size={18} /></button>
            <button
              className="w-11 h-11 rounded-full grid place-items-center cursor-pointer disabled:opacity-40"
              style={priStyle}
              onClick={togglePlay}
              disabled={!current || current.state !== 'ready'}
              title={playingHere && player.wanted ? 'Pause' : 'Play'}
            >
              {playingHere && player.wanted ? <Pause size={18} /> : <Play size={18} />}
            </button>
            <button className="cursor-pointer" onClick={() => playingHere && player.seek(position + 15)} title="Forward 15 s"><RotateCw size={18} /></button>
            <button className="cursor-pointer" onClick={() => step(1)} title="Next chapter"><SkipForward size={18} /></button>
            {current && (
              <button
                className={btn}
                style={btnStyle}
                onClick={() => setReading(current.index)}
                title="Read this chapter yourself"
              >
                <BookOpen size={14} /> Read
              </button>
            )}
            {playingHere && player.steppedAside && (
              <span className="text-[12px]" style={{ color: 'var(--color-text-tertiary)' }}>paused while Sage talks</span>
            )}
          </div>
        </div>
      </div>

      {/* what the worker is doing */}
      <div className="mt-5 px-4 py-3 rounded-xl flex items-center gap-3 text-[13px]" style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}>
        <span className="w-2 h-2 rounded-full shrink-0" style={{ background: workingHere ? 'var(--color-accent)' : 'var(--color-text-tertiary)', boxShadow: workingHere ? '0 0 8px var(--color-accent)' : undefined }} />
        <b className="font-semibold">{book.queue.paused ? 'Paused' : workerLine(w, book.id, book.chapters)}</b>
        {workingHere && w.pieces ? (
          <>
            <span style={{ color: 'var(--color-text-secondary)' }} className="tabular-nums">piece {w.piece} / {w.pieces}</span>
            <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--color-bg-tertiary)' }}>
              <div className="h-full rounded-full" style={{ width: `${(100 * (w.piece ?? 0)) / w.pieces}%`, background: 'var(--color-accent)' }} />
            </div>
          </>
        ) : (
          <span className="flex-1" />
        )}
        <span className="text-[12px]" style={{ color: 'var(--color-text-tertiary)' }}>waits for Sage when you talk</span>
      </div>

      {/* control strip */}
      <div className="flex flex-wrap items-center gap-2 mt-4 text-[12.5px]">
        <label className="flex items-center gap-1.5" style={{ color: 'var(--color-text-secondary)' }}>
          Narrator
          <select
            className="px-2 py-1.5 rounded-lg"
            style={selectStyle}
            value={book.voice}
            onChange={(e) => void act('voice', () => setBookVoice(book.id, e.target.value))}
          >
            {(voices.some((v) => v.name === book.voice) ? voices : [{ name: book.voice, has_reference: true }, ...voices]).map((v) => (
              <option key={v.name} value={v.name}>
                {v.label || v.name}{v.engine ? ` (${v.engine})` : ''}
              </option>
            ))}
          </select>
        </label>
        <select className="px-2 py-1.5 rounded-lg" style={selectStyle} value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}>
          <option value="ahead">Keep chapters ready ahead</option>
          <option value="range">Chapters…</option>
          <option value="book">The whole book</option>
        </select>
        {mode === 'ahead' && (
          <input type="number" min={1} max={50} value={ahead} onChange={(e) => setAhead(Number(e.target.value) || 1)} className="w-16 px-2 py-1.5 rounded-lg" style={selectStyle} />
        )}
        {mode === 'range' && (
          <input placeholder="e.g. 4-12" value={range} onChange={(e) => setRange(e.target.value)} className="w-24 px-2 py-1.5 rounded-lg" style={selectStyle} />
        )}
        <button className={btn} style={priStyle} disabled={busy === 'queue' || (mode === 'range' && !range.trim())} onClick={() => void queue()}>
          {busy === 'queue' ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />} Queue
        </button>
        <button className={btn} style={btnStyle} onClick={() => void act('pause', () => updateQueue({ paused: !book.queue.paused }))}>
          {book.queue.paused ? <Play size={14} /> : <Pause size={14} />} {book.queue.paused ? 'Resume' : 'Pause all'}
        </button>
        <label className="flex items-center gap-1.5 ml-1" style={{ color: 'var(--color-text-secondary)' }}>
          Speed
          <select className="px-2 py-1.5 rounded-lg" style={selectStyle} value={String(book.queue.speed)} onChange={(e) => void act('speed', () => updateQueue({ speed: Number(e.target.value) }))}>
            {(SPEEDS.some((s) => Math.abs(s - book.queue.speed) < 1e-3) ? SPEEDS : [...SPEEDS, book.queue.speed]).map((s) => (
              <option key={s} value={String(Math.abs(s - book.queue.speed) < 1e-3 ? book.queue.speed : s)}>{Math.round(s * 100)}%</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1.5" style={{ color: 'var(--color-text-secondary)' }}>
          Break
          <select className="px-2 py-1.5 rounded-lg" style={selectStyle} value={String(book.queue.break_minutes)} onChange={(e) => void act('break', () => updateQueue({ break_minutes: Number(e.target.value) }))}>
            {(BREAKS.includes(book.queue.break_minutes) ? BREAKS : [...BREAKS, book.queue.break_minutes]).map((m) => (
              <option key={m} value={String(m)}>{breakLabel(m)}</option>
            ))}
          </select>
        </label>
        {/* auto-send (user, 2026-10-14): every chapter the queue makes goes to the phone */}
        <label
          className="flex items-center gap-1.5 cursor-pointer"
          style={{ color: 'var(--color-text-secondary)' }}
          title="Send each chapter to your phone (Telegram) as soon as it is narrated, redone ones too. A failed send goes with the next chapter."
        >
          <input
            type="checkbox"
            className="cursor-pointer"
            style={{ accentColor: 'var(--color-accent)' }}
            checked={book.queue.auto_send}
            disabled={busy === 'autosend'}
            onChange={(e) => void act('autosend', () => updateQueue({ auto_send: e.target.checked }))}
          />
          Send to phone
          {book.queue.send_waiting > 0 && !sendingNow && (
            <span className="text-[12px]" style={{ color: 'var(--color-text-tertiary)' }}>
              · {book.queue.send_waiting} waiting
            </span>
          )}
        </label>
        <span className="ml-auto flex gap-2">
          <button
            className={btn}
            style={btnStyle}
            disabled={busy === 'export' || ready.length === 0}
            onClick={() => void act('export', () => exportBook(book.id).then((r) => setExported(r.written)))}
            title={`Write M4B parts (${delivery?.part_size ?? 25} chapters each) of what is narrated to ${delivery?.folders.pc ?? 'the Audiobooks folder'}${delivery?.folders.onedrive ? ' and OneDrive' : ''}`}
          >
            {busy === 'export' ? <Loader2 size={14} className="animate-spin" /> : <Package size={14} />} Export M4B
          </button>
          <button
            className={btn}
            style={sendOpen ? priStyle : btnStyle}
            disabled={ready.length === 0}
            onClick={() => {
              if (!sendOpen) setSendSpec(nextUnheard);
              setSendOpen(!sendOpen);
            }}
            title="Send narrated chapters to your phone (Telegram)"
          >
            <Smartphone size={14} /> Send to phone
          </button>
        </span>
      </div>

      {sendOpen && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[12.5px] justify-end" style={{ color: 'var(--color-text-secondary)' }}>
          Chapters
          <input
            autoFocus
            value={sendSpec}
            onChange={(e) => setSendSpec(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && sendSpec.trim() && void send()}
            placeholder="e.g. 2-4"
            className="w-28 px-2 py-1.5 rounded-lg"
            style={selectStyle}
          />
          <button className={btn} style={priStyle} disabled={busy === 'send' || !sendSpec.trim() || sendingNow} onClick={() => void send()}>
            {busy === 'send' ? <Loader2 size={14} className="animate-spin" /> : <Smartphone size={14} />} Send
          </button>
          <button className="cursor-pointer" title="Close" onClick={() => setSendOpen(false)}><X size={14} /></button>
        </div>
      )}

      {delivery && <DeliveryLine delivery={delivery} chapters={book.chapters} exported={exported} />}

      {book.jobs.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2 text-[12px]">
          {book.jobs.map((j) => (
            <span key={j.id} className="inline-flex items-center gap-2 px-2.5 py-1 rounded-full" style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text-secondary)' }}>
              {j.mode === 'rolling' ? `keep ${j.ahead} ahead` : j.mode === 'range' ? `${j.wanted} chapters` : 'whole book'}
              {j.done != null && j.mode !== 'rolling' ? ` · ${j.done}/${j.wanted}` : ''} · {j.voice}
              <button className="cursor-pointer" title="Cancel" onClick={() => void act('cancel', () => cancelJob(j.id))}><X size={12} /></button>
            </span>
          ))}
        </div>
      )}

      {/* chapters */}
      <div className="mt-5">
        <div className="text-[12px] font-semibold uppercase tracking-wide mb-2" style={{ color: 'var(--color-text-secondary)' }}>
          Chapters · {counts.ready ?? 0} ready · {counts.queued ?? 0} queued · {(counts['not made'] ?? 0)} not made
          {book.missing_numbers.length > 0 && (
            <span className="normal-case font-normal" style={{ color: 'var(--color-text-tertiary)' }}>
              {' '}· numbers missing in the source: {book.missing_numbers.join(', ')}
            </span>
          )}
          <button
            className="ml-3 normal-case font-normal inline-flex items-center gap-1 cursor-pointer"
            style={{ color: 'var(--color-accent)' }}
            onClick={() => setAdding(!adding)}
            title="Add chapters the source skipped or newer ones"
          >
            <Plus size={12} /> Add chapters
          </button>
        </div>
        {adding && (
          <AddChapters
            book={book}
            onClose={() => setAdding(false)}
            act={act}
          />
        )}
        <div className="books-row grid text-[11.5px] px-2 py-1" style={{ gridTemplateColumns: '30px 30px 60px 1fr 80px 110px', color: 'var(--color-text-tertiary)' }}>
          <span /><span /><span>#</span><span>Title</span><span>Length</span><span>Status</span>
        </div>
        {book.chapters.map((c) => {
          const isHere = c.index === (playingHere?.index ?? book.position);
          const live = c.state === 'rendering' && workingHere && w.pieces ? `${Math.round((100 * (w.piece ?? 0)) / w.pieces)}%` : null;
          return (
            <Fragment key={c.index}>
            <div
              className="books-row grid items-center gap-2 px-2 py-1.5 rounded-lg text-[13px]"
              style={{
                gridTemplateColumns: '30px 30px 60px 1fr 80px 110px',
                background: isHere ? 'var(--color-accent-subtle)' : undefined,
                boxShadow: isHere ? 'inset 2px 0 0 var(--color-accent)' : undefined,
                color: c.skip ? 'var(--color-text-tertiary)' : 'var(--color-text)',
              }}
            >
              <button
                className="w-6 h-6 rounded-full grid place-items-center cursor-pointer disabled:opacity-30 disabled:cursor-default"
                style={{ background: 'var(--color-bg-tertiary)' }}
                disabled={c.state !== 'ready'}
                onClick={() => playChapter(c, c.index === book.position ? book.position_seconds : 0)}
                title={c.state === 'ready' ? `Play ${c.label}` : c.state}
              >
                {playingHere?.index === c.index && player.playing ? <Volume2 size={12} /> : <Play size={11} />}
              </button>
              <button
                className="w-6 h-6 rounded-full grid place-items-center cursor-pointer"
                style={{ background: 'var(--color-bg-tertiary)' }}
                onClick={() => setReading(c.index)}
                title={`Read ${c.label}`}
              >
                <BookOpen size={11} />
              </button>
              <span className="tabular-nums" style={{ color: 'var(--color-text-secondary)' }}>{c.number ?? '—'}</span>
              <span className="truncate">
                {c.title !== c.label && !c.label.includes(c.title) ? `${c.label}: ${c.title}` : c.label}
                {isHere && <span style={{ color: 'var(--color-text-tertiary)' }}> · you are here</span>}
                {c.flags > 0 && <span style={{ color: 'var(--color-text-tertiary)' }}> · </span>}
                {c.flags > 0 && (
                  <button
                    className="cursor-pointer underline decoration-dotted underline-offset-2"
                    style={{ color: 'var(--color-warning)' }}
                    title="Show the lines the check could not fix"
                    onClick={() => setFlagsOpen(flagsOpen === c.index ? null : c.index)}
                  >
                    {c.flags} flagged
                  </button>
                )}
              </span>
              <span className="tabular-nums" style={{ color: 'var(--color-text-tertiary)' }}>
                {live ?? (c.seconds != null ? clock(c.seconds) : c.skip ? '' : `~${clock(c.estimate_seconds)}`)}
              </span>
              <span className="flex items-center gap-1.5">
                <Pill state={c.state} />
                {c.state === 'ready' && (
                  <button
                    className="cursor-pointer opacity-60 hover:opacity-100"
                    style={{ color: flagsOpen === c.index ? 'var(--color-accent)' : 'var(--color-text-secondary)' }}
                    title={`Redo ${c.label}, with a note on what to change`}
                    onClick={() => setFlagsOpen(flagsOpen === c.index ? null : c.index)}
                  >
                    <RotateCcw size={12} />
                  </button>
                )}
              </span>
            </div>
            {flagsOpen === c.index && c.state === 'ready' && (
              <ChapterFlags
                bookId={book.id}
                chapter={c}
                onPlay={(at) => playFrom(c, at)}
                onChanged={() => void act('flags', async () => {})}
                onRedo={async (note) => {
                  const r = await redoChapter(book.id, c.index, note);
                  void act('redo', async () => {});
                  return r;
                }}
              />
            )}
            </Fragment>
          );
        })}
      </div>

      {/* mini player once the big one scrolls away */}
      {playingHere && !heroVisible && (
        <div className="sticky bottom-0 mt-4 flex items-center gap-3 px-4 py-2.5 rounded-xl" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}>
          <button className="w-8 h-8 rounded-full grid place-items-center cursor-pointer" style={priStyle} onClick={togglePlay}>
            {player.wanted ? <Pause size={14} /> : <Play size={14} />}
          </button>
          <div className="text-[12.5px] whitespace-nowrap" style={{ color: 'var(--color-text)' }}>
            <b>{playingHere.label}</b> <span style={{ color: 'var(--color-text-tertiary)' }}>· {book.title}</span>
          </div>
          <div className="flex-1 flex items-center"><Scrubber className="w-full" pct={pct} disabled={!canSeek} onSeek={seekTo} /></div>
          <span className="text-[12px] tabular-nums" style={{ color: 'var(--color-text-tertiary)' }}>{clock(position)} / {clock(duration || null)}</span>
        </div>
      )}
    </div>
  );
}

/** One line under the controls: what is exported where, and the last send. */
function DeliveryLine({ delivery, chapters, exported }: { delivery: Delivery; chapters: BookChapter[]; exported: string[] | null }) {
  const done = delivery.parts.filter((p) => p.exported);
  const s = delivery.sending;
  const label = (i: number | null) => chapters.find((c) => c.index === i)?.label ?? `#${i}`;
  const parts: string[] = [];
  if (exported && exported.length === 0) parts.push('Export: already up to date');
  if (done.length) {
    const partial = done.filter((p) => p.exported?.partial).length;
    parts.push(
      `${done.length} part${done.length > 1 ? 's' : ''} exported${partial ? ` (${partial} partial)` : ''} to ${delivery.folders.pc}` +
        (delivery.folders.onedrive ? ' + OneDrive' : ''),
    );
  } else {
    parts.push(
      delivery.auto_export
        ? `Parts of ${delivery.part_size} chapters are exported by themselves when complete`
        : 'Nothing exported yet',
    );
  }
  let sendLine: string | null = null;
  if (s?.state === 'sending')
    sendLine = `Sending to phone: ${label(s.current)} (${s.sent.length + 1} of ${s.made.length})`;
  else if (s?.message) sendLine = s.message;
  return (
    <div className="mt-2 text-[12px] flex flex-wrap gap-x-4 gap-y-1 justify-end" style={{ color: 'var(--color-text-tertiary)' }}>
      {parts.map((t) => <span key={t}>{t}</span>)}
      {sendLine && (
        <span className="inline-flex items-center gap-1.5" style={{ color: s?.state === 'failed' ? 'var(--color-error)' : undefined }}>
          {s?.state === 'sending' && <Loader2 size={12} className="animate-spin" />}
          {sendLine}
        </span>
      )}
    </div>
  );
}
