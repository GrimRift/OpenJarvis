import { useLayoutEffect, useRef, useSyncExternalStore } from 'react';
import {
  clearComposerSlot,
  getComposerSize,
  setComposerSlot,
  subscribeComposerSlot,
} from '../../lib/composer-slot';

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
      style={voice ? { width: size.width, height: size.height } : { height: size.height }}
    />
  );
}
