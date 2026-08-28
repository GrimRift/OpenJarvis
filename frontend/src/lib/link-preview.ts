import type { ChatMessage, ToolCallInfo } from '../types';

export interface LinkPreview {
  title: string;
  url: string;
  summary?: string;
  imageUrl?: string;
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

export function externalLinkAttributes(href: string | undefined): {
  target?: '_blank';
  rel?: 'noopener noreferrer';
} {
  return remoteHttpUrl(href)
    ? { target: '_blank', rel: 'noopener noreferrer' }
    : {};
}

function previewsFromToolCall(toolCall: ToolCallInfo): LinkPreview[] {
  if (toolCall.tool !== 'web_search' || toolCall.status !== 'success') return [];
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
    const imageUrl = remoteHttpUrl(record.image_url);
    return [{ title, url, summary, imageUrl }];
  });
}

export function selectLinkPreview(message: ChatMessage): LinkPreview | undefined {
  const previews = (message.toolCalls ?? []).flatMap(previewsFromToolCall);
  if (previews.length === 0) return undefined;

  // Prefer a source the final answer actually linked. Fall back to Tavily's
  // highest-ranked result when the model cited it by name rather than URL.
  return previews.find((preview) => message.content.includes(preview.url)) ?? previews[0];
}
