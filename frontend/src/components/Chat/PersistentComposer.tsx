import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import {
  getComposerSlot,
  setComposerSize,
  subscribeComposerSlot,
} from '../../lib/composer-slot';
import { InputArea } from './InputArea';
import { appWindowHidden, whenAppWindowShown } from '../../lib/app-window';

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
      const key = `${rect.left}|${rect.top}|${rect.width}`;
      if (key !== last) {
        last = key;
        setBox({ left: rect.left, top: rect.top, width: rect.width });
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
  const style: React.CSSProperties =
    slot && box
      ? {
          position: 'fixed',
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
