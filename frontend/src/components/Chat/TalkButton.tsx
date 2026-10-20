import { useEffect, useRef, useState } from 'react';
import { Mic } from 'lucide-react';
import { HOLD_AFTER_MS, pressEnd, slidingToCancel } from '../../lib/hold-to-talk';

interface TalkButtonProps {
  /** Big round button on Voice, or the small one in the Chat box. */
  variant: 'big' | 'inline';
  /** The microphone is live (a turn in progress). */
  listening: boolean;
  disabled?: boolean;
  /** A quick press: the same as clicking the mic on the PC. */
  onTap: () => void;
  onHoldStart: () => void;
  onHoldEnd: (cancel: boolean) => void;
  /** Shown under the big button when nothing is held. */
  hint?: string;
}

type Held = 'no' | 'send' | 'cancel';

/**
 * The touch layout's talk button (M29): a tap talks hands-free, a hold is a
 * walkie-talkie -- release sends, slide up cancels (lib/hold-to-talk.ts).
 */
export function TalkButton({
  variant,
  listening,
  disabled,
  onTap,
  onHoldStart,
  onHoldEnd,
  hint,
}: TalkButtonProps) {
  const [held, setHeld] = useState<Held>('no');
  const press = useRef<{ id: number; at: number; y: number; timer: ReturnType<typeof setTimeout> | null; holding: boolean } | null>(null);

  // A press still down when the button goes away (a page change) ends as a cancel.
  useEffect(() => () => {
    const p = press.current;
    if (p?.timer) clearTimeout(p.timer);
    if (p?.holding) onHoldEnd(true);
    // Only on unmount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const finish = (clientY: number | null) => {
    const p = press.current;
    if (!p) return;
    press.current = null;
    if (p.timer) clearTimeout(p.timer);
    setHeld('no');
    const end = clientY === null ? 'cancel' : pressEnd(Date.now() - p.at, p.y - clientY);
    if (!p.holding) {
      if (end === 'tap') onTap();
      return;
    }
    onHoldEnd(end === 'cancel');
  };

  const big = variant === 'big';
  const size = big ? 76 : 38;
  const active = listening || held !== 'no';
  const label = held === 'cancel' ? 'release to cancel' : held === 'send' ? 'release to send' : hint;

  return (
    <div className="flex flex-col items-center">
      <button
        type="button"
        disabled={disabled}
        aria-label={listening ? 'Stop listening' : 'Talk: tap, or hold and release to send'}
        title={listening ? 'Stop listening' : 'Tap to talk, hold to talk and release to send'}
        className="talk-button relative grid place-items-center rounded-full shrink-0 cursor-pointer disabled:opacity-35 disabled:cursor-default transition-transform duration-150"
        style={{
          width: size,
          height: size,
          transform: held !== 'no' ? 'scale(1.12)' : undefined,
          background: big
            ? held === 'cancel' ? 'var(--color-error)' : 'var(--color-accent)'
            : active ? 'var(--color-accent)' : 'transparent',
          border: big ? 'none' : '1px solid var(--color-accent)',
          color: big || active ? 'var(--color-text-inverse)' : 'var(--color-accent)',
          boxShadow: big
            ? held !== 'no'
              ? '0 0 0 10px color-mix(in srgb, var(--color-accent) 18%, transparent), 0 0 50px var(--color-accent-glow)'
              : '0 0 0 6px color-mix(in srgb, var(--color-accent) 12%, transparent), 0 0 30px var(--color-accent-glow)'
            : active ? '0 0 16px var(--color-accent-glow)' : 'none',
          animation: listening && held === 'no' ? 'pulse 1.5s ease-in-out infinite' : 'none',
        }}
        onContextMenu={(e) => e.preventDefault()}
        onPointerDown={(e) => {
          if (disabled || press.current) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          const p = { id: e.pointerId, at: Date.now(), y: e.clientY, timer: null as ReturnType<typeof setTimeout> | null, holding: false };
          p.timer = setTimeout(() => {
            p.timer = null;
            p.holding = true;
            setHeld('send');
            navigator.vibrate?.(10);
            onHoldStart();
          }, HOLD_AFTER_MS);
          press.current = p;
        }}
        onPointerMove={(e) => {
          const p = press.current;
          if (!p || !p.holding || e.pointerId !== p.id) return;
          setHeld(slidingToCancel(p.y - e.clientY) ? 'cancel' : 'send');
        }}
        onPointerUp={(e) => {
          if (press.current?.id === e.pointerId) finish(e.clientY);
        }}
        onPointerCancel={(e) => {
          if (press.current?.id === e.pointerId) finish(null);
        }}
      >
        <Mic size={big ? 30 : 18} strokeWidth={big ? 2 : 1.9} />
      </button>
      {big && (
        <div
          className="mt-2 text-[11px] h-4 whitespace-nowrap"
          style={{ color: held === 'no' ? 'var(--color-text-tertiary)' : held === 'cancel' ? 'var(--color-error)' : 'var(--color-accent)' }}
        >
          {label}
        </div>
      )}
    </div>
  );
}
