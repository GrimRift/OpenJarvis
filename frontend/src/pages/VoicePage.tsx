import { useState } from 'react';
import { PanelRight } from 'lucide-react';
import { InputArea } from '../components/Chat/InputArea';
import { OrbVisual, useOrbGenerating, useOrbState } from '../components/Chat/OrbVisual';
import { VoiceTranscript } from '../components/Chat/VoiceTranscript';
import { useAppStore } from '../lib/store';

/** 30% over the old 588. */
const ORB_SIZE = 764;

const STATUS: Record<ReturnType<typeof useOrbState>, string> = {
  idle: 'STANDING BY',
  listening: 'LISTENING',
  speaking: 'SPEAKING',
  away: 'AWAY',
};

/**
 * Voice-first surface (M30).
 *
 * Spoken, not typed: the orb holds the centre, the controls below it are
 * icons only, and the conversation is read from a transcript that floats over
 * the right edge. Everything underneath — wake word, transcription, streaming
 * speech — is the same pipeline the main chat uses, so this page composes
 * existing pieces rather than reimplementing them. It continues whichever
 * conversation is open: it once cleared the thread on every visit, so a
 * switch from Chat to Voice mid-conversation lost the thread.
 */
export function VoicePage() {
  const orbState = useOrbState();
  const generating = useOrbGenerating();
  const messages = useAppStore((s) => s.messages);
  const sidebarOpen = useAppStore((s) => s.sidebarOpen);
  // Closed on arrival: this surface is for speaking, and the transcript is
  // something you reach for when reading is easier than listening.
  const [showTranscript, setShowTranscript] = useState(false);

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
          {generating ? 'GENERATING' : STATUS[orbState]}
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

      {showTranscript && <VoiceTranscript messages={messages} />}
    </div>
  );
}
