import { useEffect, useState } from 'react';
import { Check, Loader2, Play, RotateCcw } from 'lucide-react';
import { clock, dismissFlag, fetchFlags, type BookChapter, type ChapterFlag } from '../../lib/books';

/**
 * The lines a chapter's narration check could not get right, opened from
 * "N flagged" (user, 2026-10-14: "I can't see what it flagged and I can't
 * remove it"). Each line plays from just before it; "Sounds fine" drops the
 * flag; "Redo chapter" renders it again (the old file is kept).
 */

const WHY: Record<string, string> = {
  'too short': 'came out too short (words may be missing)',
  'too long': 'ran on longer than the text',
  garbled: 'did not sound like the text',
};

function reason(why: string): string {
  if (why.startsWith('repeated')) return `may be said twice (${why.replace(/^repeated\s*/, '')})`;
  return WHY[why] ?? why;
}

export function ChapterFlags({
  bookId,
  chapter,
  onPlay,
  onChanged,
  onRedo,
}: {
  bookId: string;
  chapter: BookChapter;
  onPlay: (at: number) => void;
  onChanged: () => void;
  onRedo: (note: string) => Promise<{ understood: string[]; not_possible: string[] }>;
}) {
  const [note, setNote] = useState('');
  const [redoing, setRedoing] = useState(false);
  const [result, setResult] = useState<{ understood: string[]; not_possible: string[] } | null>(null);
  const redo = async () => {
    setRedoing(true);
    setError(null);
    try {
      setResult(await onRedo(note));
      setNote('');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRedoing(false);
    }
  };
  const [flags, setFlags] = useState<ChapterFlag[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  useEffect(() => {
    if (chapter.flags === 0) {
      setFlags([]);
      return;
    }
    let cancelled = false;
    fetchFlags(bookId, chapter.index)
      .then((f) => !cancelled && setFlags(f))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [bookId, chapter.index, chapter.flags]);

  const dismiss = async (piece: number) => {
    setBusy(piece);
    try {
      await dismissFlag(bookId, chapter.index, piece);
      setFlags((f) => f?.filter((x) => x.piece !== piece) ?? null);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const small = 'inline-flex items-center gap-1 px-2 py-1 rounded-md text-[12px] cursor-pointer disabled:opacity-40';
  const smallStyle = { background: 'var(--color-bg-tertiary)', color: 'var(--color-text)' };

  return (
    <div
      className="ml-[70px] mr-2 mb-2 mt-1 px-3 py-2.5 rounded-lg text-[12.5px]"
      style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
    >
      {chapter.flags > 0 && (
        <div className="mb-2" style={{ color: 'var(--color-text-secondary)' }}>
          The check listens back to short lines. Play each one: if it sounds right, mark it fine.
        </div>
      )}
      {error && <div style={{ color: 'var(--color-error)' }}>{error}</div>}
      {!flags && !error && chapter.flags > 0 && <Loader2 size={14} className="animate-spin" />}
      {flags?.map((f) => (
        <div key={f.piece} className="flex items-center gap-2 py-1">
          <button
            className={small}
            style={smallStyle}
            disabled={chapter.state !== 'ready' || f.at == null}
            onClick={() => f.at != null && onPlay(Math.max(0, f.at - 1))}
            title="Play from just before this line"
          >
            <Play size={11} /> {f.at != null ? clock(f.at) : ''}
          </button>
          <span className="min-w-0 flex-1" style={{ color: 'var(--color-text)' }}>
            “{f.text}”{' '}
            <span style={{ color: 'var(--color-warning)' }}>{f.why.map(reason).join('; ')}</span>
          </span>
          <button className={small} style={smallStyle} disabled={busy === f.piece} onClick={() => void dismiss(f.piece)}>
            {busy === f.piece ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />} Sounds fine
          </button>
        </div>
      ))}
      {flags && flags.length === 0 && chapter.flags > 0 && (
        <div style={{ color: 'var(--color-text-tertiary)' }}>Nothing flagged any more.</div>
      )}

      {/* redo with a note (user, 2026-10-14) */}
      <div className={chapter.flags > 0 ? 'mt-3 pt-3' : ''} style={chapter.flags > 0 ? { borderTop: '1px solid var(--color-border)' } : undefined}>
        {chapter.redo?.note && (
          <div className="mb-2" style={{ color: 'var(--color-text-secondary)' }}>
            Last note: “{chapter.redo.note}”
            {chapter.redo.understood.length > 0 && <> → {chapter.redo.understood.join(' ')}</>}
          </div>
        )}
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={2}
          placeholder={'Optional: what should change? e.g. "say Gio as Jee-oh", "longer pauses", "a bit slower", "no sound effects"'}
          className="w-full px-2 py-1.5 rounded-md resize-y"
          style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}
        />
        <div className="flex items-start gap-3 mt-1.5">
          <div className="flex-1 min-w-0" style={{ color: 'var(--color-text-secondary)' }}>
            {result && (
              <>
                {result.understood.length > 0 && <div style={{ color: 'var(--color-success)' }}>Will change: {result.understood.join(' ')}</div>}
                {result.not_possible.length > 0 && <div style={{ color: 'var(--color-warning)' }}>Can't do: {result.not_possible.join(' ')}</div>}
              </>
            )}
            {!result && <span style={{ color: 'var(--color-text-tertiary)' }}>Every line gets a fresh take. The old file is kept as a backup.</span>}
          </div>
          <button className={small} style={smallStyle} disabled={redoing} onClick={() => void redo()} title="Narrate this chapter again">
            {redoing ? <Loader2 size={11} className="animate-spin" /> : <RotateCcw size={11} />} Redo chapter
          </button>
        </div>
      </div>
    </div>
  );
}
