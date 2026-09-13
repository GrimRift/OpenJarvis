/**
 * Voice diagnostics that survive the refresh the user is forced into.
 *
 * Two voice fixes in a row were made from theory, and each broke the other
 * half of the loop. What decides whether the microphone re-arms -- turn
 * active, audio playing, socket status, timers, the wake-word gate -- lives
 * entirely in the browser, and a page refresh wipes it. So every transition
 * is recorded here and batched to the server, where it lands in the log
 * that can actually be read afterwards. Values only: never audio, never
 * transcripts.
 */

import { apiFetch } from './api';

interface TraceEvent {
  t: number;
  event: string;
  detail: string;
}

const FLUSH_MS = 1500;
const RING = 300;
const KEY = 'sage-voice-trace';

let pending: TraceEvent[] = [];
let flushTimer: ReturnType<typeof setTimeout> | null = null;

// The in-app Logs page is fed through a sink the store registers, rather
// than by importing the store here: the store reads localStorage at module
// load, and the speech hooks that trace are unit-tested without a DOM.
type Sink = (event: string, detail: string, at: number) => void;
let sink: Sink | null = null;
export function setVoiceTraceSink(next: Sink | null): void {
  sink = next;
}

function persist(entry: TraceEvent): void {
  if (typeof localStorage === 'undefined') return;
  try {
    const raw = localStorage.getItem(KEY);
    const ring: TraceEvent[] = raw ? JSON.parse(raw) : [];
    ring.push(entry);
    localStorage.setItem(KEY, JSON.stringify(ring.slice(-RING)));
  } catch {
    /* storage may be unavailable; the server copy still goes out */
  }
}

async function flush(): Promise<void> {
  flushTimer = null;
  if (pending.length === 0) return;
  const batch = pending;
  pending = [];
  try {
    await apiFetch('/v1/speech/trace', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ events: batch }),
    });
  } catch {
    /* diagnostics must never break the thing they diagnose */
  }
}

/** Record one voice-pipeline transition. Cheap; safe to call anywhere. */
export function voiceTrace(event: string, detail: Record<string, unknown> = {}): void {
  const entry: TraceEvent = {
    t: Date.now(),
    event,
    detail: Object.entries(detail)
      .map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`)
      .join(' '),
  };
  persist(entry);
  try {
    sink?.(event, entry.detail, entry.t);
  } catch {
    /* the Logs page is a convenience, not the record */
  }
  pending.push(entry);
  if (flushTimer === null) flushTimer = setTimeout(flush, FLUSH_MS);
}

if (typeof window !== 'undefined') {
  // A refresh must not lose the last second of events: they are the ones
  // that explain why the user refreshed.
  window.addEventListener('pagehide', () => {
    if (pending.length === 0) return;
    try {
      const body = JSON.stringify({ events: pending });
      pending = [];
      // sendBeacon carries no auth header; fall back to the fetch which
      // does, keepalive so the unload does not cancel it.
      void apiFetch('/v1/speech/trace', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
        keepalive: true,
      }).catch(() => {});
    } catch {
      /* nothing left to do on the way out */
    }
  });
}
