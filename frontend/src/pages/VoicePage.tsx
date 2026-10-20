import { useEffect, useState } from 'react';
import { AlignLeft, PanelRight } from 'lucide-react';
import { ComposerSlot } from '../components/Chat/ComposerSlot';
import { OrbVisual, useOrbGenerating, useOrbState } from '../components/Chat/OrbVisual';
import { VoiceTranscript } from '../components/Chat/VoiceTranscript';
import { useAppStore } from '../lib/store';
import { touchOrbSize, useTouchLayout } from '../lib/touch-layout';

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
  const touch = useTouchLayout();

  if (touch) {
    return (
      <TouchVoice
        status={generating ? 'GENERATING' : STATUS[orbState]}
        orbState={orbState}
        messages={messages}
        showTranscript={showTranscript}
        setShowTranscript={setShowTranscript}
      />
    );
  }

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
          <ComposerSlot voice />
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

function useTouchOrbSize(): number {
  const read = () => touchOrbSize(window.innerWidth, window.innerHeight);
  const [size, setSize] = useState(read);
  useEffect(() => {
    const onResize = () => setSize(read());
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return size;
}

/** Voice on a phone or iPad (M29, mockup "picks-v2", take A). */
function TouchVoice({
  status,
  orbState,
  messages,
  showTranscript,
  setShowTranscript,
}: {
  status: string;
  orbState: ReturnType<typeof useOrbState>;
  messages: ReturnType<typeof useAppStore.getState>['messages'];
  showTranscript: boolean;
  setShowTranscript: (show: boolean | ((v: boolean) => boolean)) => void;
}) {
  const size = useTouchOrbSize();
  const wakeWord = useAppStore((s) => s.settings.wakeWordEnabled);
  const live = orbState === 'listening' || orbState === 'speaking';
  return (
    <div className="relative h-full w-full overflow-hidden flex flex-col">
      {/* With the transcript open the orb shrinks above it, and the
          controls stay in view below (it covered them as a sheet). */}
      <div
        className={`${showTranscript ? 'shrink-0 py-3' : 'flex-1 min-h-0'} flex flex-col items-center justify-center gap-3 px-4`}
      >
        <div
          className="text-[11px] tracking-[0.28em] select-none"
          style={{ color: live ? 'var(--color-accent)' : 'var(--color-text-tertiary)' }}
        >
          {status}
        </div>
        <OrbVisual state={orbState} size={showTranscript ? 110 : size} />
      </div>
      {showTranscript && (
        <VoiceTranscript messages={messages} onClose={() => setShowTranscript(false)} />
      )}
      <div className="shrink-0 flex items-start justify-center gap-6 pb-2">
        <ComposerSlot voice />
        <div className="flex flex-col items-center">
          <button
            type="button"
            onClick={() => setShowTranscript((v) => !v)}
            aria-pressed={showTranscript}
            aria-label={showTranscript ? 'Hide transcript' : 'Show transcript'}
            className="grid place-items-center rounded-full cursor-pointer"
            style={{
              width: 54,
              height: 54,
              background: 'color-mix(in srgb, var(--color-bg-secondary) 70%, transparent)',
              border: '1px solid var(--color-border)',
              color: 'var(--color-text-secondary)',
            }}
          >
            <AlignLeft size={21} />
          </button>
          <div className="mt-2 text-[11px]" style={{ color: 'var(--color-text-tertiary)' }}>transcript</div>
        </div>
      </div>
      {wakeWord && (
        <div className="shrink-0 flex justify-center pb-3">
          <span
            className="inline-flex items-center gap-1.5 text-[11.5px] rounded-full px-2.5 py-1"
            style={{ color: 'var(--color-text-secondary)', border: '1px solid var(--color-border)' }}
          >
            <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'var(--color-success)' }} />
            Hey Sage on · while this screen is open
          </span>
        </div>
      )}
    </div>
  );
}
