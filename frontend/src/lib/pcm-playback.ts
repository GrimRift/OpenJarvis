/**
 * Scheduling maths for gapless playback of streamed PCM.
 *
 * Kept pure and separate from the hook because this project has no jsdom —
 * a hook cannot be rendered in a test, but the arithmetic that decides when
 * each chunk starts can be checked exactly.
 */

/** Cartesia streams raw little-endian float32; the SSE endpoint offers no mp3. */
export function decodePcmF32(buffer: ArrayBuffer): Float32Array {
  // A partial frame would shift every later sample by a byte and turn the
  // rest of the reply into noise, so trailing bytes are dropped.
  const usable = buffer.byteLength - (buffer.byteLength % 4);
  return new Float32Array(buffer.slice(0, usable));
}

/**
 * When the next chunk should start.
 *
 * Generation runs ~4.3x faster than playback, so `scheduledUntil` is normally
 * ahead of the clock and chunks queue back to back. It falls behind only on
 * the first chunk or after a stall, and then playing immediately is right —
 * scheduling in the past makes Web Audio drop the buffer silently.
 */
export function nextStartTime(
  currentTime: number,
  scheduledUntil: number,
  safetyMargin = 0.02,
): number {
  return Math.max(currentTime + safetyMargin, scheduledUntil);
}

/** Seconds of audio in a chunk. */
export function chunkDuration(samples: number, sampleRate: number): number {
  if (!(sampleRate > 0)) return 0;
  return samples / sampleRate;
}

/** Keep UI state alive while the browser and host device drain their buffers. */
export function outputTailDelayMs(
  baseLatencySeconds: number,
  outputLatencySeconds: number,
): number {
  const latency = [baseLatencySeconds, outputLatencySeconds].reduce(
    (total, value) =>
      total + (Number.isFinite(value) && value > 0 ? value * 1000 : 0),
    0,
  );
  return Math.ceil(latency) + 50;
}

/** Invalidates delayed callbacks as soon as a newer utterance takes over. */
export class PlaybackGeneration {
  private current = 0;

  begin(): number {
    this.current += 1;
    return this.current;
  }

  cancel(): void {
    this.current += 1;
  }

  isCurrent(generation: number): boolean {
    return generation === this.current;
  }
}

export type TtsStreamEvent =
  | { kind: 'ready'; sampleRate: number }
  | { kind: 'start'; sampleRate: number }
  | { kind: 'done' }
  | { kind: 'error'; reason: string; started: boolean }
  | { kind: 'ignored' };

/** The orb may say "speaking" only after the relay announces real audio. */
export function shouldShowSpeakingState(event: TtsStreamEvent): boolean {
  return event.kind === 'start';
}

/**
 * Interpret one control message from the relay.
 *
 * `started` matters: an error before any audio is recoverable by falling back
 * to the batch endpoint, but once the user is hearing the reply, restarting it
 * would speak the opening twice.
 */
export function interpretTtsMessage(raw: string): TtsStreamEvent {
  let msg: Record<string, unknown>;
  try {
    msg = JSON.parse(raw) as Record<string, unknown>;
  } catch {
    return { kind: 'ignored' };
  }
  switch (msg.type) {
    case 'ready': {
      const rate = Number(msg.sample_rate);
      return { kind: 'ready', sampleRate: rate > 0 ? rate : 24000 };
    }
    case 'start': {
      const rate = Number(msg.sample_rate);
      return { kind: 'start', sampleRate: rate > 0 ? rate : 24000 };
    }
    case 'done':
      return { kind: 'done' };
    case 'error':
      return {
        kind: 'error',
        reason: typeof msg.reason === 'string' ? msg.reason : 'unknown',
        started: Boolean(msg.started),
      };
    default:
      return { kind: 'ignored' };
  }
}
