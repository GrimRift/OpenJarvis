import { useEffect, useMemo, useRef } from 'react';
import { InputArea } from '../components/Chat/InputArea';
import { OrbVisual, useOrbState } from '../components/Chat/OrbVisual';
import { useAppStore } from '../lib/store';

const ORB_SIZE = 420;

const STATUS: Record<ReturnType<typeof useOrbState>, string> = {
  idle: 'STANDING BY',
  listening: 'LISTENING',
  speaking: 'SPEAKING',
};

/**
 * Voice-first surface (M30).
 *
 * The orb dominates the centre, the last exchange sits quietly beneath it, and
 * text entry is deliberately small and secondary. Everything underneath —
 * wake word, transcription, streaming speech — is the same pipeline the main
 * chat uses, so this page composes existing pieces rather than reimplementing
 * them: `InputArea` is reused whole, just given a narrow column.
 */
export function VoicePage() {
  const orbState = useOrbState();
  const messages = useAppStore((s) => s.messages);
  const sidebarOpen = useAppStore((s) => s.sidebarOpen);
  const createConversation = useAppStore((s) => s.createConversation);
  const activeId = useAppStore((s) => s.activeId);
  const startedRef = useRef(false);

  useEffect(() => {
    // A fresh thread per visit, but only on the way in: turns taken while
    // you stay on this page continue the same conversation.
    if (startedRef.current) return;
    startedRef.current = true;
    if (activeId) createConversation();
  }, [activeId, createConversation]);

  const exchange = useMemo(() => {
    const assistant = [...messages]
      .reverse()
      .find((m) => m.role === 'assistant' && m.content.trim());
    const asked = [...messages].reverse().find((m) => m.role === 'user');
    return { asked: asked?.content ?? '', answered: assistant?.content ?? '' };
  }, [messages]);

  return (
    // The orb must sit on the true centre of the window, not the centre of
    // "window minus sidebar". A phantom right-hand gutter the width of the
    // sidebar rebalances it — the same trick ChatPage uses for its column.
    // The gutter includes px-6's own 24px because `pr` overrides `px` on that
    // side: without it the orb lands 12px right of centre.
    // Collapsing the sidebar with its own toggle then makes this full-bleed,
    // which is what "collapsible" is for; this page does not mutate that
    // global state itself, since it would leak to every other page.
    <div
      className={`flex h-full w-full flex-col items-center justify-center overflow-hidden px-6 transition-[padding] duration-200 ease-in-out ${
        sidebarOpen ? 'md:pr-[calc(260px+1.5rem)]' : ''
      }`}
    >
      <div
        className="text-[11px] tracking-[0.28em] mb-2 select-none"
        style={{ color: 'var(--color-text-tertiary)' }}
      >
        {STATUS[orbState]}
      </div>

      <OrbVisual state={orbState} size={ORB_SIZE} />

      {/* The last exchange only — readable, never a transcript. */}
      <div className="mt-6 w-full max-w-2xl text-center min-h-[4.5rem]">
        {exchange.asked && (
          <p
            className="text-sm mb-1.5 truncate"
            style={{ color: 'var(--color-text-tertiary)' }}
          >
            {exchange.asked}
          </p>
        )}
        {exchange.answered && (
          <p
            className="text-[15px] leading-relaxed line-clamp-4"
            style={{ color: 'var(--color-text-secondary)' }}
          >
            {exchange.answered}
          </p>
        )}
      </div>

      {/* Text entry stays deliberately small and secondary. */}
      <div className="mt-6 w-full max-w-lg">
        <InputArea />
      </div>
    </div>
  );
}
