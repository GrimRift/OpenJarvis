import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import {
  getComposerSlot,
  setComposerSize,
  subscribeComposerSlot,
} from '../../lib/composer-slot';
import { InputArea } from './InputArea';
import { appWindowHidden, whenAppWindowShown } from '../../lib/app-window';
import { touchLayoutNow } from '../../lib/touch-layout';

interface Box {
  left: number;
  top: number;
  width: number;
}

/**
 * The app's one message box, mounted once in the layout (lib/composer-slot.ts).
 * Drawn over the current page's <ComposerSlot>; with none, hidden but still
 * running, so the microphone and the wake word never stop on a page change.
 */
/** Decided once: a device does not stop being a phone (lib/touch-layout.ts). */
const TOUCH = touchLayoutNow();

export function PersistentComposer() {
  const slot = useSyncExternalStore(subscribeComposerSlot, getComposerSlot, getComposerSlot);
  const ref = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState<Box | null>(null);

  // Follow the slot. Every frame, because the sidebar animates the layout
  // and a ResizeObserver sees sizes, not positions; a rect read is cheap and
  // state only changes when the slot actually moved.
  useEffect(() => {
    if (!slot) {
      setBox(null);
      return;
    }
    let frame = 0;
    let last = '';
    let stopWaiting = () => {};
    const follow = () => {
      // Out of sight in the app's tray or minimised: nothing moves that
      // anyone could see; wait for the window to come back.
      if (appWindowHidden()) {
        stopWaiting = whenAppWindowShown(follow);
        return;
      }
      const rect = slot.el.getBoundingClientRect();
      // On a phone the box is placed inside the page, not on the screen
      // (M29): with the iOS keyboard up, screen coordinates and fixed
      // positioning disagree by the page's scroll, and the box jumped to the
      // top. Both rects below share one frame, whatever iOS has scrolled.
      const origin = TOUCH ? ref.current?.parentElement?.getBoundingClientRect() : undefined;
      const left = rect.left - (origin?.left ?? 0);
      const top = rect.top - (origin?.top ?? 0);
      const key = `${left}|${top}|${rect.width}`;
      if (key !== last) {
        last = key;
        setBox({ left, top, width: rect.width });
      }
      frame = requestAnimationFrame(follow);
    };
    follow();
    return () => {
      cancelAnimationFrame(frame);
      stopWaiting();
    };
  }, [slot]);

  // Report this box's size, so the slot holds exactly the space it covers.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const report = () => setComposerSize(el.offsetWidth, el.offsetHeight);
    report();
    const observer = new ResizeObserver(report);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const voice = slot?.voice ?? false;
  // Chat's box and Voice's controls swap in place: on a phone the swap
  // fades rather than snaps. Animated, never remounted -- the box owns the
  // microphone.
  const firstVoice = useRef(true);
  useEffect(() => {
    if (firstVoice.current) {
      firstVoice.current = false;
      return;
    }
    if (!TOUCH) return;
    ref.current?.animate?.(
      [{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }],
      { duration: 220, easing: 'cubic-bezier(.2,.7,.2,1)' },
    );
  }, [voice]);
  const style: React.CSSProperties =
    slot && box
      ? {
          position: TOUCH ? 'absolute' : 'fixed',
          left: box.left,
          top: box.top,
          width: voice ? undefined : box.width,
          zIndex: 3,
        }
      : // No slot on this page: out of sight, still mounted and listening.
        { position: 'fixed', left: -10000, top: 0, width: 600, visibility: 'hidden' };

  return (
    <div ref={ref} style={style} data-composer="">
      <InputArea voiceOnly={voice} />
    </div>
  );
}
