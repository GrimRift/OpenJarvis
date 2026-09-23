/**
 * Where the one message box goes on the current page.
 *
 * The message box owns the microphone: the wake word, the Flux session, the
 * turn in progress. Chat and Voice each used to render their own, so
 * switching pages unmounted one -- the wake word restarted with a gap and a
 * sentence half said was cut off. Now a single box lives in the layout for
 * the life of the app, and a page only says where it should sit by
 * rendering a <ComposerSlot>. A page with no slot hides the box but keeps it
 * running, so Sage still hears "Hey Sage" from Settings or the Dashboard.
 */

export interface ComposerSlotInfo {
  el: HTMLElement;
  /** Voice's controls-only box, rather than the full composer. */
  voice: boolean;
}

type Listener = () => void;

let slot: ComposerSlotInfo | null = null;
let size = { width: 0, height: 0 };
const listeners = new Set<Listener>();

function emit(): void {
  for (const listener of listeners) listener();
}

export function subscribeComposerSlot(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getComposerSlot(): ComposerSlotInfo | null {
  return slot;
}

export function setComposerSlot(next: ComposerSlotInfo): void {
  slot = next;
  emit();
}

/** Clear the slot, but only if it is still *el*: the next page's slot can
 * register before the old one unmounts. */
export function clearComposerSlot(el: HTMLElement): void {
  if (slot?.el === el) {
    slot = null;
    emit();
  }
}

/** The box's own size, so its slot can hold the space it covers. */
export function getComposerSize(): { width: number; height: number } {
  return size;
}

export function setComposerSize(width: number, height: number): void {
  if (width === size.width && height === size.height) return;
  size = { width, height };
  emit();
}
