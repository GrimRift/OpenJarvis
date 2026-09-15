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

export function useMomentsFeed(): void {
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      // Never interleave with a reply that is still arriving.
      if (useAppStore.getState().streamState.isStreaming) return;
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
      if (cancelled) return;
      const { fresh, seen: next } = newMoments(history, seen, now);
      if (fresh.length === 0) return;
      const store = useAppStore.getState();
      const conversationId = store.activeId ?? store.createConversation();
      for (const record of fresh) {
        store.addMessage(conversationId, {
          id: `moment-${record.at}`,
          role: 'assistant',
          content: record.text,
          timestamp: Math.round(record.at * 1000),
          moment: record.kind,
        });
      }
      writeSeen(next);
      const replyAt = replyWindowFor(fresh, Date.now());
      if (replyAt !== null) store.requestReplyWindow(replyAt);
    };
    void poll();
    const timer = setInterval(() => void poll(), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);
}
