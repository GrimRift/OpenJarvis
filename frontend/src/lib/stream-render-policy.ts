/**
 * Streaming Markdown is reparsed from the beginning on every rendered update.
 * Keep short replies lively, then progressively coalesce deltas as the document
 * grows so long answers do not monopolize the main thread.
 */
export function streamRenderIntervalMs(contentChars: number): number {
  if (contentChars > 8_000) return 300;
  if (contentChars > 2_000) return 160;
  return 80;
}

export function shouldFlushStreamRender(
  nowMs: number,
  lastFlushMs: number,
  contentChars: number,
): boolean {
  return (
    lastFlushMs === 0 ||
    nowMs - lastFlushMs >= streamRenderIntervalMs(contentChars)
  );
}
