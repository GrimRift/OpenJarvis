import { useEffect, useRef, useState } from 'react';
import { PanelRight } from 'lucide-react';
import { InputArea } from '../components/Chat/InputArea';
import { OrbVisual, useOrbState } from '../components/Chat/OrbVisual';
import { VoiceTranscript } from '../components/Chat/VoiceTranscript';
import { useAppStore } from '../lib/store';

const ORB_SIZE = 588;

const STATUS: Record<ReturnType<typeof useOrbState>, string> = {
  idle: 'STANDING BY',
  listening: 'LISTENING',
  speaking: 'SPEAKING',
};

/**
 * Voice-first surface (M30).
 *
 * Spoken, not typed: the orb holds the centre, the controls below it are
 * icons only, and the conversation is read from a transcript that floats over
 * the right edge. Everything underneath — wake word, transcription, streaming
 * speech — is the same pipeline the main chat uses, so this page composes
 * existing pieces rather than reimplementing them.
 */
export function VoicePage() {
  const orbState = useOrbState();
  const messages = useAppStore((s) => s.messages);
  const sidebarOpen = useAppStore((s) => s.sidebarOpen);
  const startNewChat = useAppStore((s) => s.startNewChat);
  // Closed on arrival: this surface is for speaking, and the transcript is
  // something you reach for when reading is easier than listening.
  const [showTranscript, setShowTranscript] = useState(false);
  const startedRef = useRef(false);

  useEffect(() => {
    // A fresh thread per visit, but only on the way in: turns taken while you
    // stay on this page continue the same conversation. Clears rather than
    // creates — creating eagerly put an empty "New chat" in the sidebar on
    // every switch between Chat and Voice.
    if (startedRef.current) return;
    startedRef.current = true;
    startNewChat();
  }, [startNewChat]);

  return (
    <div className="relative h-full w-full overflow-hidden">
      {/* The orb holds the true window centre, not the centre of "window minus
          sidebar" — hence the phantom gutter, which includes px-6's own 24px
          because `pr` overrides `px` on that side. The transcript is absolutely
          positioned and so contributes nothing here, which is what keeps the
          orb still when it is shown or hidden. */}
      <div
        className={`flex h-full w-full flex-col items-center justify-center px-6 transition-[padding] duration-200 ease-in-out ${
          sidebarOpen ? 'md:pr-[calc(260px+1.5rem)]' : ''
        }`}
      >
        <div
          className="text-[11px] tracking-[0.28em] mb-3 select-none"
          style={{ color: 'var(--color-text-tertiary)' }}
        >
          {STATUS[orbState]}
        </div>

        <OrbVisual state={orbState} size={ORB_SIZE} />

        {/* Controls only — this surface is spoken, not typed. */}
        <div className="mt-8 flex items-center gap-3">
          <InputArea voiceOnly />
          <button
            type="button"
            onClick={() => setShowTranscript((v) => !v)}
            aria-pressed={showTranscript}
            aria-label={showTranscript ? 'Hide transcript' : 'Show transcript'}
            title={showTranscript ? 'Hide transcript' : 'Show transcript'}
            className="p-2.5 rounded-full transition-colors cursor-pointer"
            style={{
              background: showTranscript
                ? 'var(--color-accent-subtle)'
                : 'transparent',
              border: `1px solid ${
                showTranscript ? 'var(--color-accent)' : 'var(--color-border)'
              }`,
              color: showTranscript
                ? 'var(--color-accent)'
                : 'var(--color-text-tertiary)',
            }}
          >
            <PanelRight size={16} />
          </button>
        </div>
      </div>

      {showTranscript && (
        <VoiceTranscript
          messages={messages}
          onClose={() => setShowTranscript(false)}
        />
      )}
    </div>
  );
}
