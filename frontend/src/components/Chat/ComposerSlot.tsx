import { useLayoutEffect, useRef, useSyncExternalStore } from 'react';
import {
  clearComposerSlot,
  getComposerSize,
  setComposerSlot,
  subscribeComposerSlot,
} from '../../lib/composer-slot';
import { touchLayoutNow } from '../../lib/touch-layout';

/** Decided once: a device does not stop being a phone (lib/touch-layout.ts). */
const TOUCH = touchLayoutNow();

/** The phone's Voice controls (InputArea: replies 54 + gap 24 + talk 76,
 * talk 76 + its label). Fixed, not measured: the measured width lagged a
 * page change and the controls settled off-screen (M29). */
const TOUCH_VOICE_CONTROLS = { width: 154, height: 102 };

/** Where the app's one message box sits on this page (lib/composer-slot.ts).
 * It holds the box's space in the layout; the box itself is drawn over it. */
export function ComposerSlot({ voice = false }: { voice?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const size = useSyncExternalStore(subscribeComposerSlot, getComposerSize, getComposerSize);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setComposerSlot({ el, voice });
    return () => clearComposerSlot(el);
  }, [voice]);

  return (
    <div
      ref={ref}
      aria-hidden="true"
      className="shrink-0"
      style={
        voice
          ? TOUCH
            ? { width: TOUCH_VOICE_CONTROLS.width, height: TOUCH_VOICE_CONTROLS.height }
            : { width: size.width, height: size.height }
          : { height: size.height }
      }
    />
  );
}
