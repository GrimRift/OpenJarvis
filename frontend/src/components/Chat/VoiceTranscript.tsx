import { useEffect, useRef } from 'react';
import type { ChatMessage } from '../../types';

/**
 * Read-only transcript for the voice page.
 *
 * Floats over the right edge rather than sitting in the layout, so showing or
 * hiding it never moves the orb off the window's centre.
 */
export function VoiceTranscript({
  messages,
  onClose,
}: {
  messages: ChatMessage[];
  onClose: () => void;
}) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' });
  }, [messages]);

  return (
    <aside
      aria-label="Conversation transcript"
      className="absolute right-4 top-4 bottom-4 z-20 flex w-[340px] flex-col rounded-xl overflow-hidden"
      style={{
        background: 'var(--color-surface)',
        border: '1px solid var(--color-border)',
        boxShadow: 'var(--shadow-sm)',
      }}
    >
      <header
        className="flex items-center justify-between px-3 py-2 shrink-0"
        style={{ borderBottom: '1px solid var(--color-border)' }}
      >
        <span
          className="text-[11px] tracking-[0.18em]"
          style={{ color: 'var(--color-text-tertiary)' }}
        >
          TRANSCRIPT
        </span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Hide transcript"
          title="Hide transcript"
          className="text-xs px-1.5 rounded cursor-pointer"
          style={{ color: 'var(--color-text-tertiary)' }}
        >
          ✕
        </button>
      </header>

      <div className="flex-1 overflow-y-auto px-3 py-3 space-y-3">
        {messages.length === 0 && (
          <p className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
            Nothing said yet.
          </p>
        )}
        {messages.map((m) => (
          <div key={m.id}>
            <div
              className="text-[10px] tracking-[0.14em] mb-0.5"
              style={{ color: 'var(--color-text-tertiary)' }}
            >
              {m.role === 'user' ? 'YOU' : 'SAGE'}
            </div>
            <p
              className="text-[13px] leading-relaxed whitespace-pre-wrap break-words"
              style={{
                color:
                  m.role === 'user'
                    ? 'var(--color-text-tertiary)'
                    : 'var(--color-text-secondary)',
              }}
            >
              {m.content}
            </p>
          </div>
        ))}
        <div ref={endRef} />
      </div>
    </aside>
  );
}
