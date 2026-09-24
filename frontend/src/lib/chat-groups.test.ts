import { describe, expect, it } from 'vitest';
import { groupConversations } from './chat-groups';
import type { Conversation } from '../types';

const DAY = 24 * 60 * 60 * 1000;
const NOW = Date.UTC(2026, 8, 24, 8);

function chat(id: string, daysAgo: number, pinned = false): Conversation {
  return {
    id,
    title: id,
    createdAt: NOW - daysAgo * DAY,
    updatedAt: NOW - daysAgo * DAY,
    model: 'm',
    messages: [],
    ...(pinned ? { pinned } : {}),
  };
}

describe('groupConversations', () => {
  it('keeps the last three days whole and folds the rest by age', () => {
    const groups = groupConversations(
      [chat('today', 0.1), chat('two days', 2.5), chat('five days', 5), chat('three weeks', 21), chat('two years', 800)],
      NOW,
    );
    expect(groups.recent.map((c) => c.id)).toEqual(['today', 'two days']);
    expect(groups.clusters.map((c) => [c.label, c.conversations.map((x) => x.id)])).toEqual([
      ['Last 7 days', ['five days']],
      ['Last month', ['three weeks']],
      ['Older', ['two years']],
    ]);
  });

  it('puts pinned chats on top, out of their age group', () => {
    const groups = groupConversations([chat('old pin', 40, true), chat('new', 0.2)], NOW);
    expect(groups.pinned.map((c) => c.id)).toEqual(['old pin']);
    expect(groups.recent.map((c) => c.id)).toEqual(['new']);
    expect(groups.clusters).toEqual([]);
  });

  it('lists newest first within each group', () => {
    const groups = groupConversations([chat('b', 1), chat('a', 0.5), chat('c', 2)], NOW);
    expect(groups.recent.map((c) => c.id)).toEqual(['a', 'b', 'c']);
  });
});
