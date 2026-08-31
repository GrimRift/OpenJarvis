export type IncrementalTtsMessage =
  | { type: 'text'; delta: string }
  | { type: 'finish' }
  | { type: 'cancel' };

/**
 * Bounded ordered queue for model deltas produced before the speech socket is
 * ready. Once ready, deltas go straight to the server; sentence buffering and
 * all sanitization stay server-side so private values cannot be split around
 * a browser-side sanitizer.
 */
export class IncrementalTtsOutbox {
  private readonly pending: string[] = [];
  private pendingChars = 0;
  private ready = false;
  private finishRequested = false;
  private cancelRequested = false;
  private cancelSent = false;
  overflowed = false;

  constructor(
    private readonly send: (message: IncrementalTtsMessage) => void,
    private readonly maxPendingChars = 20_000,
  ) {}

  get finished(): boolean {
    return this.finishRequested;
  }

  push(delta: string): boolean {
    if (!delta || this.finishRequested || this.cancelRequested || this.overflowed) {
      return false;
    }
    if (this.ready) {
      this.send({ type: 'text', delta });
      return true;
    }
    if (this.pendingChars + delta.length > this.maxPendingChars) {
      this.pending.length = 0;
      this.pendingChars = 0;
      this.overflowed = true;
      return false;
    }
    this.pending.push(delta);
    this.pendingChars += delta.length;
    return true;
  }

  finish(): void {
    if (this.finishRequested || this.cancelRequested || this.overflowed) return;
    this.finishRequested = true;
    if (this.ready) this.send({ type: 'finish' });
  }

  cancel(): void {
    if (this.cancelRequested) return;
    this.cancelRequested = true;
    this.pending.length = 0;
    this.pendingChars = 0;
    if (this.ready) this.sendCancel();
  }

  markReady(): void {
    if (this.ready) return;
    this.ready = true;
    if (this.cancelRequested || this.overflowed) {
      this.sendCancel();
      return;
    }
    for (const delta of this.pending) {
      this.send({ type: 'text', delta });
    }
    this.pending.length = 0;
    this.pendingChars = 0;
    if (this.finishRequested) this.send({ type: 'finish' });
  }

  private sendCancel(): void {
    if (this.cancelSent) return;
    this.cancelSent = true;
    this.send({ type: 'cancel' });
  }
}

/** Nothing heard means batch fallback is safe; otherwise it would replay. */
export function shouldFallbackAfterTtsFailure(audioStarted: boolean): boolean {
  return !audioStarted;
}
