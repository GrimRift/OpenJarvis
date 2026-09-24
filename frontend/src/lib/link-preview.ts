import type { ChatMessage, ToolCallInfo } from '../types';

export interface LinkPreview {
  title: string;
  url: string;
  summary?: string;
  imageUrl?: string;
  publishedDate?: string;
  /** Sage opened and read this page, rather than only seeing it in results. */
  read?: boolean;
}

export interface SearchImage {
  url: string;
  description?: string;
}

function remoteHttpUrl(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  try {
    const url = new URL(value);
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return undefined;
    return url.href;
  } catch {
    return undefined;
  }
}

function remoteHttpsUrl(value: unknown): string | undefined {
  const url = remoteHttpUrl(value);
  return url?.startsWith('https:') ? url : undefined;
}

export function externalLinkAttributes(href: string | undefined): {
  target?: '_blank';
  rel?: 'noopener noreferrer';
} {
  return remoteHttpUrl(href)
    ? { target: '_blank', rel: 'noopener noreferrer' }
    : {};
}

/** Tools whose results list the pages an answer came from. */
const SOURCE_TOOLS = new Set(['web_search', 'web_read']);

function previewsFromToolCall(toolCall: ToolCallInfo): LinkPreview[] {
  if (!SOURCE_TOOLS.has(toolCall.tool) || toolCall.status !== 'success') return [];
  const read = toolCall.tool === 'web_read';
  const sources = toolCall.metadata?.sources;
  if (!Array.isArray(sources)) return [];

  return sources.flatMap((source) => {
    if (!source || typeof source !== 'object') return [];
    const record = source as Record<string, unknown>;
    const url = remoteHttpUrl(record.url);
    if (!url) return [];

    const title = typeof record.title === 'string' && record.title.trim()
      ? record.title.trim()
      : new URL(url).hostname;
    const summary = typeof record.summary === 'string' && record.summary.trim()
      ? record.summary.trim()
      : undefined;
    const imageUrl = remoteHttpsUrl(record.image_url);
    const publishedDate = typeof record.published_date === 'string' && record.published_date.trim()
      ? record.published_date.trim()
      : undefined;
    return [{ title, url, summary, imageUrl, publishedDate, ...(read ? { read } : {}) }];
  });
}

export function selectSearchImages(message: ChatMessage): SearchImage[] {
  for (const toolCall of message.toolCalls ?? []) {
    if (
      toolCall.tool !== 'web_search'
      || toolCall.status !== 'success'
      || toolCall.metadata?.explicit_image_search !== true
      || !Array.isArray(toolCall.metadata.images)
    ) continue;

    const seen = new Set<string>();
    return toolCall.metadata.images.flatMap((image) => {
      if (!image || typeof image !== 'object') return [];
      const record = image as Record<string, unknown>;
      const url = remoteHttpsUrl(record.url);
      if (!url || seen.has(url)) return [];
      seen.add(url);
      const description = typeof record.description === 'string' && record.description.trim()
        ? record.description.trim()
        : undefined;
      return [{ url, description }];
    });
  }
  return [];
}

export function selectLinkPreview(message: ChatMessage): LinkPreview | undefined {
  // A source the answer already links is not shown again: its own
  // "Sources:" list, then the card and the list below repeated the same
  // three threads (24 September). Of the rest, a page Sage read (what the
  // answer rests on) first, then the search's highest-ranked result.
  const cited = citedKeys(message.content);
  const previews = (message.toolCalls ?? [])
    .flatMap(previewsFromToolCall)
    .filter((preview) => !cited.has(sourceKey(preview.url)));
  if (previews.length === 0) return undefined;
  return previews.find((preview) => preview.read) ?? previews[0];
}

/** The pages an answer links in its own text, keyed like sources. */
function citedKeys(content: string): Set<string> {
  const keys = new Set<string>();
  for (const match of content.matchAll(/https?:\/\/[^\s)\]>"'<]+/g)) {
    try {
      keys.add(sourceKey(match[0].replace(/[.,;:!?]+$/, '')));
    } catch {
      /* not a URL after all */
    }
  }
  return keys;
}

/** Most sources listed under an answer. */
export const MAX_LISTED_SOURCES = 8;

/**
 * Every other source behind an answer, pages read first, one per address.
 * The card showed a single result, so a page Sage spent twenty seconds
 * reading could be the one reference missing (23 September).
 */
export function selectSources(
  message: ChatMessage,
  shown?: LinkPreview,
): LinkPreview[] {
  const previews = (message.toolCalls ?? []).flatMap(previewsFromToolCall);
  const ordered = [...previews.filter((p) => p.read), ...previews.filter((p) => !p.read)];
  const seen = citedKeys(message.content);
  if (shown) seen.add(sourceKey(shown.url));
  const listed: LinkPreview[] = [];
  for (const preview of ordered) {
    const key = sourceKey(preview.url);
    if (seen.has(key)) continue;
    seen.add(key);
    listed.push(preview);
    if (listed.length >= MAX_LISTED_SOURCES) break;
  }
  return listed;
}

function sourceKey(url: string): string {
  const parsed = new URL(url);
  return `${parsed.hostname.replace(/^www\./, '')}${parsed.pathname.replace(/\/$/, '')}${parsed.search}`;
}
