// Rewind a chat to before one of the user's messages, like Claude Code's
// rewind: that message and everything after it leave the chat, and its text
// goes back into the message box to edit and resend. For UNDO_MS an Undo
// toast puts it all back; after that (or as soon as the next message is
// sent) the facts Sage learned from the cut messages are forgotten too --
// soft deleted, restorable on the Memory page under Removed.

import { toast } from 'sonner';
import { forgetMemoryTurns } from './api';
import { useAppStore } from './store';
import type { ChatMessage } from '../types';

export const UNDO_MS = 10_000;

interface Pending {
  conversationId: string;
  removed: ChatMessage[];
  toastId: string | number;
  timer: ReturnType<typeof setTimeout>;
}

let pending: Pending | null = null;

/** The user messages among *removed*: the turns whose facts are forgotten. */
export function rewoundTurns(removed: ChatMessage[]): string[] {
  return removed.filter((m) => m.role === 'user').map((m) => m.id);
}

/** Forget what the pending rewind cut, now: Undo is no longer possible. */
export function finalizePendingRewind(): void {
  const done = pending;
  if (!done) return;
  pending = null;
  clearTimeout(done.timer);
  toast.dismiss(done.toastId);
  const turns = rewoundTurns(done.removed);
  if (!turns.length) return;
  forgetMemoryTurns(turns)
    .then(({ removed }) => {
      if (removed.length) {
        useAppStore.getState().addLogEntry({
          timestamp: Date.now(),
          level: 'info',
          category: 'chat',
          message: `Rewind: forgot ${removed.length} fact(s) learned from the removed messages`,
        });
      }
    })
    .catch(() => {
      // Memory is best-effort; the chat itself is already rewound.
    });
}

function undo(): void {
  const done = pending;
  if (!done) return;
  pending = null;
  clearTimeout(done.timer);
  const state = useAppStore.getState();
  if (state.restoreRewound(done.conversationId, done.removed)) {
    // Take the text back out of the box, unless it was edited meanwhile.
    state.setComposerDraft({ text: '', unless: done.removed[0].content });
  } else {
    toast('Could not undo: a new message was sent since.');
  }
}

/** Rewind the chat to before *messageId*. False if it could not. */
export function rewindChat(conversationId: string, messageId: string): boolean {
  const state = useAppStore.getState();
  if (state.streamState.isStreaming) return false;
  // A second rewind settles the first: its Undo would no longer fit.
  finalizePendingRewind();
  const removed = state.rewindTo(conversationId, messageId);
  if (!removed?.length) return false;
  state.setComposerDraft({ text: removed[0].content });
  const replies = removed.length - 1;
  const toastId = toast('Rewound', {
    description:
      replies > 0
        ? `Removed this message and ${replies} after it. Edit it and send again.`
        : 'Edit the message and send it again.',
    duration: UNDO_MS,
    action: { label: 'Undo', onClick: undo },
  });
  const timer = setTimeout(finalizePendingRewind, UNDO_MS);
  pending = { conversationId, removed, toastId, timer };
  return true;
}
