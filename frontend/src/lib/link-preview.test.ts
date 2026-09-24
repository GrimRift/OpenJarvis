import { describe, expect, it } from 'vitest';
import {
  externalLinkAttributes,
  selectLinkPreview,
  selectSearchImages,
  selectSources,
} from './link-preview';
import type { ChatMessage } from '../types';

describe('externalLinkAttributes', () => {
  it('opens external http links in a new tab safely', () => {
    expect(externalLinkAttributes('https://example.com/page')).toEqual({
      target: '_blank',
      rel: 'noopener noreferrer',
    });
  });

  it('does not change internal or unsafe links', () => {
    expect(externalLinkAttributes('/settings')).toEqual({});
    expect(externalLinkAttributes('#sources')).toEqual({});
    expect(externalLinkAttributes('javascript:alert(1)')).toEqual({});
  });
});

describe('selectLinkPreview', () => {
  it('does not repeat a source the answer already links', () => {
    const message: ChatMessage = {
      id: 'm1',
      role: 'assistant',
      timestamp: 1,
      content: 'See https://second.example/icl for details.',
      toolCalls: [{
        id: 't1',
        tool: 'web_search',
        arguments: '{}',
        status: 'success',
        metadata: {
          sources: [
            { title: 'First', url: 'https://first.example/' },
            {
              title: 'Second',
              url: 'https://second.example/icl',
              summary: 'ICL information',
              image_url: 'https://images.example/eye.jpg',
              published_date: '2026-08-31',
            },
          ],
        },
      }],
    };

    // The answer links "Second" itself; the card shows what it did not.
    expect(selectLinkPreview(message)?.title).toBe('First');
    expect(selectSources(message, selectLinkPreview(message))).toEqual([]);
  });

  it('shows nothing extra when the answer links every source', () => {
    const message: ChatMessage = {
      id: 'm3',
      role: 'assistant',
      timestamp: 1,
      content: 'Sources:\n- https://www.reddit.com/r/a/comments/1/x/\n- https://reddit.com/r/b.',
      toolCalls: [{
        id: 't1',
        tool: 'web_search',
        arguments: '{}',
        status: 'success',
        metadata: {
          sources: [
            { title: 'A', url: 'https://www.reddit.com/r/a/comments/1/x' },
            { title: 'B', url: 'https://www.reddit.com/r/b' },
          ],
        },
      }],
    };
    expect(selectLinkPreview(message)).toBeUndefined();
    expect(selectSources(message)).toEqual([]);
  });

  it('ignores invalid preview URLs', () => {
    const message: ChatMessage = {
      id: 'm1',
      role: 'assistant',
      timestamp: 1,
      content: 'No links',
      toolCalls: [{
        id: 't1',
        tool: 'web_search',
        arguments: '{}',
        status: 'success',
        metadata: { sources: [{ title: 'Bad', url: 'javascript:alert(1)' }] },
      }],
    };

    expect(selectLinkPreview(message)).toBeUndefined();
  });

  it('rejects non-HTTPS source thumbnails', () => {
    const message: ChatMessage = {
      id: 'm1', role: 'assistant', timestamp: 1, content: 'Result',
      toolCalls: [{
        id: 't1', tool: 'web_search', arguments: '{}', status: 'success',
        metadata: {
          sources: [{
            title: 'Result',
            url: 'https://example.com/result',
            image_url: 'http://images.example/result.jpg',
          }],
        },
      }],
    };

    expect(selectLinkPreview(message)?.imageUrl).toBeUndefined();
  });
});

describe('selectSearchImages', () => {
  const message = (explicit: boolean): ChatMessage => ({
    id: 'm1', role: 'assistant', timestamp: 1, content: 'Images',
    toolCalls: [{
      id: 't1', tool: 'web_search', arguments: '{}', status: 'success',
      metadata: {
        explicit_image_search: explicit,
        images: [
          { url: 'https://images.example/one.jpg', description: 'One' },
          { url: 'http://images.example/two.jpg', description: 'Unsafe' },
          { url: 'https://images.example/one.jpg', description: 'Duplicate' },
        ],
      },
    }],
  });

  it('returns deduplicated HTTPS images for explicit image searches', () => {
    expect(selectSearchImages(message(true))).toEqual([
      { url: 'https://images.example/one.jpg', description: 'One' },
    ]);
  });

  it('does not create a gallery for an ordinary web search', () => {
    expect(selectSearchImages(message(false))).toEqual([]);
  });
});

describe('selectSources', () => {
  const message: ChatMessage = {
    id: 'm2',
    role: 'assistant',
    timestamp: 1,
    content: 'The Sun is a G-type star.',
    toolCalls: [
      {
        id: 't1',
        tool: 'web_search',
        arguments: '{}',
        status: 'success',
        metadata: {
          sources: [
            { title: 'Video', url: 'https://www.youtube.com/watch?v=abc' },
            { title: 'Maricopa', url: 'https://open.maricopa.edu/sun/' },
            { title: 'Wiki', url: 'https://en.wikipedia.org/wiki/Sun' },
          ],
        },
      },
      {
        id: 't2',
        tool: 'web_read',
        arguments: '{}',
        status: 'success',
        metadata: {
          sources: [{ title: 'The Structure of the Sun', url: 'https://open.maricopa.edu/sun' }],
        },
      },
    ],
  };

  it('puts a page Sage read on the card, ahead of the top search result', () => {
    const card = selectLinkPreview(message);
    expect(card?.url).toBe('https://open.maricopa.edu/sun');
    expect(card?.read).toBe(true);
  });

  it('lists every other source once, without the card', () => {
    const card = selectLinkPreview(message);
    expect(selectSources(message, card).map((s) => s.title)).toEqual(['Video', 'Wiki']);
  });

  it('lists nothing for an answer without sources', () => {
    expect(selectSources({ ...message, toolCalls: [] })).toEqual([]);
  });
});
