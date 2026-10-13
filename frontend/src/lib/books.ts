/**
 * The Books page's server side (M42): `/v1/books`, the same library layer
 * Sage's audiobook chat tools use. Chapter audio and covers are fetched with
 * the API key and shown through blob URLs, like generated pictures (M40):
 * the app never hands the key to an <audio> or <img> src.
 */

import { apiFetch } from './api';

export type ChapterState = 'ready' | 'rendering' | 'queued' | 'skipped' | 'not made';

export interface BookChapter {
  index: number;
  label: string;
  number: string | null;
  title: string;
  words: number;
  skip: boolean;
  skip_reason: string | null;
  state: ChapterState;
  seconds: number | null;
  estimate_seconds: number;
  flags: number;
}

export interface WorkerStatus {
  state: string;
  updated?: string;
  reason?: string;
  book?: string;
  chapter?: number;
  voice?: string;
  piece?: number;
  pieces?: number;
  book_voice_running?: boolean;
}

export interface RenderJob {
  id: string;
  book: string;
  voice: string;
  mode: 'book' | 'range' | 'rolling';
  chapters: number[];
  ahead: number;
  position: number;
  state: string;
  wanted?: number;
  done?: number | null;
}

export interface BookSummary {
  id: string;
  title: string;
  author: string;
  voice: string;
  has_cover: boolean;
  chapters: number;
  rendered: number;
  words: number | null;
  position: number | null;
}

export interface BookDetail {
  id: string;
  title: string;
  author: string;
  voice: string;
  has_cover: boolean;
  words: number | null;
  missing_numbers: number[];
  position: number | null;
  position_seconds: number;
  chapters: BookChapter[];
  jobs: RenderJob[];
  queue: { paused: boolean; speed: number; break_minutes: number };
  worker: WorkerStatus;
  /** Only on GET /v1/books/{id} (the page's poll). */
  delivery?: Delivery;
}

async function json<T>(res: Response, what: string): Promise<T> {
  if (!res.ok) {
    let detail = '';
    try {
      detail = (await res.json()).detail ?? '';
    } catch {
      /* not JSON */
    }
    throw new Error(detail || `${what} failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

const enc = encodeURIComponent;

export async function fetchBooks(): Promise<{ books: BookSummary[]; worker: WorkerStatus }> {
  return json(await apiFetch('/v1/books'), 'Loading books');
}

export async function fetchBook(id: string): Promise<BookDetail> {
  return json(await apiFetch(`/v1/books/${enc(id)}`), 'Loading the book');
}

export async function importBook(file: File): Promise<BookDetail> {
  const form = new FormData();
  form.append('file', file);
  return json(await apiFetch('/v1/books/import', { method: 'POST', body: form }), 'Import');
}

export async function setBookVoice(id: string, voice: string): Promise<BookDetail> {
  const res = await apiFetch(`/v1/books/${enc(id)}/voice`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ voice }),
  });
  return json(res, 'Changing the narrator');
}

export async function queueRender(
  id: string,
  request: { chapters?: string; ahead?: number; voice?: string },
): Promise<{ job: RenderJob; book: BookDetail }> {
  const res = await apiFetch(`/v1/books/${enc(id)}/render`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  });
  return json(res, 'Queueing');
}

export async function updateQueue(change: {
  paused?: boolean;
  speed?: number;
  break_minutes?: number;
}): Promise<void> {
  const res = await apiFetch('/v1/books/queue', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(change),
  });
  await json(res, 'Changing the queue');
}

export async function cancelJob(jobId: string): Promise<void> {
  await json(await apiFetch(`/v1/books/queue/jobs/${enc(jobId)}`, { method: 'DELETE' }), 'Cancel');
}

export async function savePosition(id: string, index: number, seconds: number): Promise<void> {
  const res = await apiFetch(`/v1/books/${enc(id)}/position`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ index, seconds }),
  });
  await json(res, 'Saving your place');
}

/** A blob URL for a book's cover or a chapter's audio; the caller revokes it. */
export async function fetchBlobUrl(path: string): Promise<string> {
  const res = await apiFetch(path);
  if (!res.ok) throw new Error(`Could not load it (${res.status})`);
  return URL.createObjectURL(await res.blob());
}

export const coverPath = (id: string) => `/v1/books/${enc(id)}/cover`;
export const chapterAudioPath = (id: string, index: number, voice?: string) =>
  `/v1/books/${enc(id)}/chapters/${index}/audio${voice ? `?voice=${enc(voice)}` : ''}`;

/** "22:28", "1:04:09" */
export function clock(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '--:--';
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
}

/** What the worker is doing, in one line for the status card. */
export function workerLine(w: WorkerStatus, bookId: string, chapters: BookChapter[]): string {
  const label = (i?: number) => chapters.find((c) => c.index === i)?.label ?? `#${i}`;
  const mine = w.book === bookId;
  switch (w.state) {
    case 'rendering':
      return mine ? `Rendering ${label(w.chapter)}` : 'Rendering another book';
    case 'starting':
      return 'Loading the narrator voice';
    case 'waiting':
      return `Waiting: ${w.reason ?? 'Sage is busy'}`;
    case 'resting':
      return w.reason ? `Resting (${w.reason})` : 'Resting between chapters';
    case 'paused':
      return 'Paused';
    case 'error':
      return `Stopped: ${w.reason ?? 'an error'}`;
    case 'idle':
      return 'Nothing queued';
    default:
      return w.state === 'unknown' ? 'Render worker not running' : w.state;
  }
}

export interface ChapterText {
  index: number;
  label: string;
  title: string;
  words: number;
  paragraphs: string[];
}

/** A chapter's text, to read on the page without a narrator. */
export async function fetchChapterText(id: string, index: number): Promise<ChapterText> {
  return json(await apiFetch(`/v1/books/${enc(id)}/chapters/${index}/text`), 'Loading the chapter');
}

/** M42 step 4: getting the book off the PC (M4B parts, OneDrive, Telegram). */
export interface PartStatus {
  part: number;
  name: string;
  chapters: number[];
  rendered: number;
  total: number;
  complete: boolean;
  exported: null | {
    file: string;
    local: string;
    onedrive: string | null;
    chapters: number[];
    partial: boolean;
    seconds: number;
    bytes: number;
    exported_at: string;
  };
}

export interface SendStatus {
  state: 'sending' | 'done' | 'failed' | 'stopped';
  book: string;
  title: string;
  made: number[];
  not_made: number[];
  sent: number[];
  current: number | null;
  estimate_seconds: number;
  message: string;
}

export interface Delivery {
  folders: { pc: string; onedrive: string | null };
  part_size: number;
  auto_export: boolean;
  parts: PartStatus[];
  sending: SendStatus | null;
}

export async function exportBook(id: string): Promise<{ written: string[]; delivery: Delivery }> {
  const res = await apiFetch(`/v1/books/${enc(id)}/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ partial: true }),
  });
  return json(res, 'Export');
}

export async function sendToPhone(id: string, chapters: string): Promise<{ sending: SendStatus }> {
  const res = await apiFetch(`/v1/books/${enc(id)}/send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chapters }),
  });
  return json(res, 'Sending');
}

/** "2-4", "7", "2, 5, 9": book numbers of the chapters, ranges where they run on. */
export function chapterSpec(chapters: Pick<BookChapter, 'index' | 'number'>[]): string {
  const out: string[] = [];
  let start = 0;
  for (let i = 1; i <= chapters.length; i++) {
    if (i < chapters.length && chapters[i].index === chapters[i - 1].index + 1) continue;
    const a = chapters[start].number ?? String(chapters[start].index);
    const b = chapters[i - 1].number ?? String(chapters[i - 1].index);
    out.push(a === b ? a : `${a}-${b}`);
    start = i;
  }
  return out.join(', ');
}
