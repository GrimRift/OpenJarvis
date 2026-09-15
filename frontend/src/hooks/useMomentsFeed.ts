/**
 * Moments in the transcript (M36 phase 3).
 *
 * What Sage says on its own is spoken by the server, not the browser, so the
 * tab has no natural copy of it. This polls the record and appends each new
 * moment to the open conversation as an assistant turn, so a greeting can be
 * read afterwards and so the next reply knows it was said. A watermark in
 * localStorage keeps a reopened tab from adding what it already has.
 */

import { useEffect } from 'react';
import { fetchMoments, fetchPresence, type MomentRecord } from '../lib/api';
import { newMoments, replyWindowFor } from '../lib/moments-feed';
import { useAppStore } from '../lib/store';

// Five seconds: after Sage asks something aloud, the microphone should
// open for the answer before the user has given up waiting for it.
const POLL_MS = 5_000;
// The orb dims for an empty desk and brightens on return; five seconds so
// the brighten-up is seen as the user sits down, not a while after.
const PRESENCE_POLL_MS = 5_000;
const SEEN_KEY = 'sage-moments-seen';

function readSeen(): number | null {
  try {
    const raw = localStorage.getItem(SEEN_KEY);
    return raw ? Number(raw) : null;
  } catch {
    return null;
  }
}

function writeSeen(at: number): void {
  try {
    localStorage.setItem(SEEN_KEY, String(at));
  } catch {
    /* a lost watermark only means one duplicate on the next load */
  }
}

/** What the server believes about the desk, into the store for the orb. */
export function usePresenceState(): void {
  useEffect(() => {
    let cancelled = false;
    const poll = () =>
      fetchPresence()
        .then((snap) => {
          if (!cancelled) useAppStore.getState().setPresenceState(snap.state);
        })
        .catch(() => {
          if (!cancelled) useAppStore.getState().setPresenceState('unknown');
        });
    void poll();
    const timer = setInterval(poll, PRESENCE_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);
}

// One poll at a time, across every mount: the dev server mounts effects
// twice, and two polls in flight before the watermark was written appended
// the same moment to the chat twice.
let inFlight = false;

async function pollOnce(isCancelled: () => boolean): Promise<void> {
  const seen = readSeen();
  const now = Date.now() / 1000;
  if (seen === null) {
    writeSeen(now);
    return;
  }
  let history: MomentRecord[];
  try {
    history = (await fetchMoments(seen)).history;
  } catch {
    return;
  }
  if (isCancelled()) return;
  const { fresh, seen: next } = newMoments(history, seen, now);
  // The watermark moves before anything is added, so a second look at the
  // same records -- another tab, a retry -- finds nothing new.
  writeSeen(next);
  if (fresh.length === 0) return;
  const store = useAppStore.getState();
  const conversationId = store.activeId ?? store.createConversation();
  const already = new Set(store.messages.map((m) => m.id));
  for (const record of fresh) {
    const id = `moment-${record.at}`;
    if (already.has(id)) continue;
    store.addMessage(conversationId, {
      id,
      role: 'assistant',
      content: record.text,
      timestamp: Math.round(record.at * 1000),
      moment: record.kind,
    });
  }
  const replyAt = replyWindowFor(fresh, Date.now());
  if (replyAt !== null) store.requestReplyWindow(replyAt);
}

export function useMomentsFeed(): void {
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      if (inFlight) return;
      // Never interleave with a reply that is still arriving.
      if (useAppStore.getState().streamState.isStreaming) return;
      inFlight = true;
      try {
        await pollOnce(() => cancelled);
      } finally {
        inFlight = false;
      }
    };
    void poll();
    const timer = setInterval(() => void poll(), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);
}
