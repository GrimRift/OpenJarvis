/**
 * How the sidebar lays out the chat history.
 *
 * It listed every chat, newest first: 171 of them, most of them the same
 * "What's up?". Now pinned chats stay on top, the last three days are one
 * list, and everything older folds into clusters by age that open in place.
 */

import type { Conversation } from '../types';

const DAY = 24 * 60 * 60 * 1000;

/** How long "recent" is: shown in full, not folded. */
export const RECENT_DAYS = 3;

/** The folds past the recent list, oldest last. Upper bounds in days. */
export const CLUSTERS: ReadonlyArray<{ key: string; label: string; upTo: number }> = [
  { key: '7d', label: 'Last 7 days', upTo: 7 },
  { key: '1m', label: 'Last month', upTo: 30 },
  { key: '3m', label: 'Last 3 months', upTo: 91 },
  { key: '6m', label: 'Last 6 months', upTo: 182 },
  { key: '1y', label: 'Last year', upTo: 365 },
  { key: 'older', label: 'Older', upTo: Infinity },
];

export interface ChatCluster {
  key: string;
  label: string;
  conversations: Conversation[];
}

export interface ChatGroups {
  pinned: Conversation[];
  recent: Conversation[];
  clusters: ChatCluster[];
}

/** Newest first within every group; clusters with no chats are left out. */
export function groupConversations(conversations: readonly Conversation[], now: number): ChatGroups {
  const sorted = [...conversations].sort((a, b) => b.updatedAt - a.updatedAt);
  const pinned = sorted.filter((c) => c.pinned);
  const rest = sorted.filter((c) => !c.pinned);
  const recent: Conversation[] = [];
  const buckets = CLUSTERS.map((c) => ({ key: c.key, label: c.label, conversations: [] as Conversation[] }));
  for (const conversation of rest) {
    const days = (now - conversation.updatedAt) / DAY;
    if (days < RECENT_DAYS) {
      recent.push(conversation);
      continue;
    }
    const index = CLUSTERS.findIndex((c) => days < c.upTo);
    buckets[index === -1 ? buckets.length - 1 : index].conversations.push(conversation);
  }
  return { pinned, recent, clusters: buckets.filter((b) => b.conversations.length > 0) };
}
