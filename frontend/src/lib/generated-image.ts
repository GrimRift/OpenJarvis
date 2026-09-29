/**
 * Pictures Sage made (M40), as they arrive on a reply's tool calls.
 *
 * The server never puts image data in a tool result: `tool_call_end` carries
 * `metadata.image` with an id and a `/v1/images/{id}` URL, and the tool calls
 * are persisted with the conversation, so the card survives a reload.
 */

import type { ToolCallInfo } from '../types';

export interface GeneratedImage {
  id: string;
  url: string;
  prompt: string;
  kind: 'generate' | 'edit' | string;
  model: string;
  quality?: string;
  size?: string;
  /** null = the price was not known, which is not the same as free. */
  cost_usd: number | null;
  parent_id?: string | null;
  file?: string;
  seconds?: number;
}

export const IMAGE_TOOLS = new Set(['image_generate', 'image_edit']);

function asImage(value: unknown): GeneratedImage | null {
  if (!value || typeof value !== 'object') return null;
  const image = value as Record<string, unknown>;
  if (typeof image.id !== 'string' || typeof image.url !== 'string') return null;
  if (!image.url.startsWith('/v1/images/')) return null;
  const cost = image.cost_usd;
  return {
    id: image.id,
    url: image.url,
    prompt: typeof image.prompt === 'string' ? image.prompt : '',
    kind: typeof image.kind === 'string' ? image.kind : 'generate',
    model: typeof image.model === 'string' ? image.model : '',
    quality: typeof image.quality === 'string' ? image.quality : undefined,
    size: typeof image.size === 'string' ? image.size : undefined,
    cost_usd: typeof cost === 'number' && Number.isFinite(cost) ? cost : null,
    parent_id: typeof image.parent_id === 'string' ? image.parent_id : null,
    file: typeof image.file === 'string' ? image.file : undefined,
    seconds: typeof image.seconds === 'number' ? image.seconds : undefined,
  };
}

/** The picture a finished tool call produced, if it produced one. */
export function imageFromToolCall(call: Pick<ToolCallInfo, 'status' | 'metadata'>): GeneratedImage | null {
  if (call.status !== 'success') return null;
  return asImage(call.metadata?.image);
}

/** Every picture a reply made, in order. */
export function imagesIn(toolCalls: ToolCallInfo[] | undefined): GeneratedImage[] {
  const out: GeneratedImage[] = [];
  for (const call of toolCalls ?? []) {
    const image = imageFromToolCall(call);
    if (image) out.push(image);
  }
  return out;
}

export interface ImageCost {
  count: number;
  /** Sum of the known costs. */
  usd: number;
  /** How many had no known price. */
  unknown: number;
}

export function imageCost(toolCalls: ToolCallInfo[] | undefined): ImageCost | null {
  const images = imagesIn(toolCalls);
  if (images.length === 0) return null;
  let usd = 0;
  let unknown = 0;
  for (const image of images) {
    if (image.cost_usd === null) unknown += 1;
    else usd += image.cost_usd;
  }
  return { count: images.length, usd, unknown };
}

/** "$0.013", "$0.013 + 1 unknown", "cost unknown" -- never "$0.000" for an unknown. */
export function formatImageCost(cost: ImageCost): string {
  const known = cost.count - cost.unknown;
  if (known === 0) return 'cost unknown';
  const amount = `$${cost.usd.toFixed(3)}`;
  return cost.unknown ? `${amount} + ${cost.unknown} unknown` : amount;
}

/** The status line while a picture tool runs (generation takes 15-50 s). */
export function imageToolPhase(tool: string): string | null {
  if (tool === 'image_generate') return 'Drawing...';
  if (tool === 'image_edit') return 'Editing the picture...';
  if (tool === 'image_to_phone') return 'Sending it to your phone...';
  return null;
}
