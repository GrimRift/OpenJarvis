import { useRef, useState } from 'react';
import { FileUp, Globe, Loader2, Plus, X } from 'lucide-react';
import {
  addChapterText,
  addChaptersFromFile,
  fetchChapterPage,
  type BookDetail,
} from '../../lib/books';

/**
 * Adding chapters after import (user, 2026-10-14): the numbers the source
 * skipped and chapters that came out later. Three ways, all chosen by the
 * user: a newer PDF/EPUB of the book (only chapters it lacks are taken),
 * pasted text, or a link -- the page's text lands in the paste form to be
 * checked before it is saved. Added chapters slot in by number; nothing
 * already made is touched.
 */

type Tab = 'file' | 'paste' | 'link';

export function AddChapters({
  book,
  onClose,
  act,
}: {
  book: BookDetail;
  onClose: () => void;
  act: (what: string, fn: () => Promise<unknown>) => Promise<void>;
}) {
  const [tab, setTab] = useState<Tab>('file');
  const [number, setNumber] = useState(book.missing_numbers[0] ? String(book.missing_numbers[0]) : '');
  const [title, setTitle] = useState('');
  const [text, setText] = useState('');
  const [url, setUrl] = useState('');
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const run = async (fn: () => Promise<string | null>) => {
    setWorking(true);
    setError(null);
    setNote(null);
    try {
      // Errors stay in this panel (act() would show them in the page banner);
      // act() only refreshes the book afterwards.
      const message = await fn();
      setNote(message);
      await act('add', async () => {});
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setWorking(false);
    }
  };

  const fromFile = (file: File | undefined) => {
    if (!file) return;
    void run(async () => {
      const r = await addChaptersFromFile(book.id, file);
      return r.added.length
        ? `Added ${r.added.length}: ${r.added.slice(0, 8).join(', ')}${r.added.length > 8 ? '…' : ''}`
        : `Nothing new: the book already has all ${r.already} numbered chapters in that file.`;
    });
  };

  const save = () =>
    void run(async () => {
      const r = await addChapterText(book.id, { number: number.trim(), title: title.trim(), text });
      setText('');
      setTitle('');
      setNumber('');
      return `Added ${r.added.label}. Queue it like any other chapter.`;
    });

  const fetchLink = () =>
    void run(async () => {
      const page = await fetchChapterPage(book.id, url.trim());
      setText(page.text);
      if (page.number) setNumber(page.number);
      setTitle('');
      setTab('paste');
      return `Read ${page.text.length.toLocaleString()} characters from the page. Check the text below (trim any menus or comments), then add it.`;
    });

  const tabBtn = (t: Tab, icon: React.ReactNode, label: string) => (
    <button
      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md cursor-pointer"
      style={{
        background: tab === t ? 'var(--color-accent-subtle)' : 'transparent',
        color: tab === t ? 'var(--color-accent)' : 'var(--color-text-secondary)',
      }}
      onClick={() => setTab(t)}
    >
      {icon} {label}
    </button>
  );
  const field = { background: 'var(--color-bg)', border: '1px solid var(--color-border)', color: 'var(--color-text)' };
  const primary = 'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12.5px] cursor-pointer disabled:opacity-40 disabled:cursor-default';
  const primaryStyle = { background: 'var(--color-accent)', color: 'var(--color-on-accent)' };

  return (
    <div className="mb-3 px-4 py-3 rounded-xl text-[12.5px]" style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', color: 'var(--color-text)' }}>
      <div className="flex items-center gap-1 mb-3">
        {tabBtn('file', <FileUp size={13} />, 'Newer PDF / EPUB')}
        {tabBtn('paste', <Plus size={13} />, 'Paste text')}
        {tabBtn('link', <Globe size={13} />, 'From a link')}
        <button className="ml-auto cursor-pointer" style={{ color: 'var(--color-text-tertiary)' }} onClick={onClose} title="Close">
          <X size={14} />
        </button>
      </div>

      {tab === 'file' && (
        <div className="flex items-center gap-3">
          <span style={{ color: 'var(--color-text-secondary)' }}>
            Pick a newer copy of this book. Only chapters it doesn't have yet are added; made chapters stay as they are.
          </span>
          <button className={primary} style={primaryStyle} disabled={working} onClick={() => fileRef.current?.click()}>
            {working ? <Loader2 size={14} className="animate-spin" /> : <FileUp size={14} />} Choose file
          </button>
          <input ref={fileRef} type="file" accept=".pdf,.epub" className="hidden" onChange={(e) => {
              fromFile(e.target.files?.[0]);
              e.target.value = ''; // the same file can be picked again
            }} />
        </div>
      )}

      {tab === 'link' && (
        <div className="flex items-center gap-2">
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && url.trim() && fetchLink()}
            placeholder="https://… link to one chapter"
            className="flex-1 px-2 py-1.5 rounded-lg"
            style={field}
          />
          <button className={primary} style={primaryStyle} disabled={working || !url.trim()} onClick={fetchLink}>
            {working ? <Loader2 size={14} className="animate-spin" /> : <Globe size={14} />} Read page
          </button>
        </div>
      )}

      {tab === 'paste' && (
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-1.5" style={{ color: 'var(--color-text-secondary)' }}>
              Chapter
              <input value={number} onChange={(e) => setNumber(e.target.value)} placeholder="88" className="w-20 px-2 py-1.5 rounded-lg" style={field} />
            </label>
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Title (optional)" className="flex-1 px-2 py-1.5 rounded-lg" style={field} />
          </div>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Paste the chapter's text. A blank line between paragraphs, or one paragraph per line."
            rows={8}
            className="px-2 py-1.5 rounded-lg resize-y"
            style={{ ...field, lineHeight: 1.5 }}
          />
          <div className="flex items-center gap-3">
            <span style={{ color: 'var(--color-text-tertiary)' }}>
              {text.trim() ? `${text.trim().split(/\s+/).length.toLocaleString()} words` : ''}
            </span>
            <button className={`${primary} ml-auto`} style={primaryStyle} disabled={working || !text.trim()} onClick={save}>
              {working ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} Add chapter
            </button>
          </div>
        </div>
      )}

      {note && <div className="mt-2" style={{ color: 'var(--color-success)' }}>{note}</div>}
      {error && <div className="mt-2" style={{ color: 'var(--color-error)' }}>{error}</div>}
    </div>
  );
}
