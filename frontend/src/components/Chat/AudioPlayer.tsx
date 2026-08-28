import { useRef, useState, useEffect, useCallback } from 'react';
import { Play, Pause } from 'lucide-react';
import { useAppStore } from '../../lib/store';

interface AudioPlayerProps {
  src: string;
  autoPlay?: boolean;
}

export function AudioPlayer({ src, autoPlay = false }: AudioPlayerProps) {
  const audioRef = useRef<HTMLAudioElement>(null);
  // Seeded from autoPlay rather than always false: when a caller sets
  // audioPlaying=true optimistically ahead of this component mounting
  // (see InputArea.tsx's TTS fallback), starting this at false would
  // immediately overwrite that via the effect below, reopening the exact
  // gap the caller was trying to close, until the .play() promise
  // resolves a beat later.
  const [playing, setPlaying] = useState(autoPlay);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  // Synthesized clips live in an in-memory map on the server, so every token
  // dies with a restart while the chat history keeps its URL. Reopening an
  // old chat then renders a player that 404s and can never play.
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    if (!autoPlay) return;
    const el = audioRef.current;
    if (!el) return;
    el
      .play()
      .then(() => setPlaying(true))
      .catch(() => {
        // Browsers can block autoplay outside a fresh user-gesture window —
        // the manual play button remains the fallback either way. Must
        // still correct `playing` back to false here: it now starts
        // seeded to `autoPlay` (true) optimistically, so a blocked
        // autoplay would otherwise leave it stuck true forever with
        // nothing actually playing.
        setPlaying(false);
      });
  }, [autoPlay, src]);

  const toggle = useCallback(() => {
    const el = audioRef.current;
    if (!el) return;
    if (playing) {
      el.pause();
    } else {
      el.play();
    }
    setPlaying(!playing);
  }, [playing]);

  useEffect(() => {
    const el = audioRef.current;
    if (!el) return;

    const onTime = () => setCurrentTime(el.currentTime);
    const onMeta = () => setDuration(el.duration);
    const onEnded = () => {
      setPlaying(false);
      setCurrentTime(0);
    };
    const onError = () => {
      setUnavailable(true);
      setPlaying(false);
    };

    el.addEventListener('timeupdate', onTime);
    el.addEventListener('loadedmetadata', onMeta);
    el.addEventListener('ended', onEnded);
    el.addEventListener('error', onError);
    return () => {
      el.removeEventListener('timeupdate', onTime);
      el.removeEventListener('loadedmetadata', onMeta);
      el.removeEventListener('ended', onEnded);
      el.removeEventListener('error', onError);
    };
  }, [src]);

  // Mirror into the store so the orb (rendered elsewhere) can show a
  // "speaking" state for the actual duration of the spoken audio, not
  // just the response's text-streaming window.
  useEffect(() => {
    useAppStore.getState().setAudioPlaying(playing);
    return () => {
      if (playing) useAppStore.getState().setAudioPlaying(false);
    };
  }, [playing]);

  useEffect(() => {
    setUnavailable(false);
  }, [src]);

  const seek = (e: React.ChangeEvent<HTMLInputElement>) => {
    const el = audioRef.current;
    if (!el) return;
    const nextTime = Number(e.currentTarget.value);
    el.currentTime = nextTime;
    setCurrentTime(nextTime);
  };

  // Nothing to offer: a dead clip's controls only mislead.
  if (unavailable) return null;

  return (
    <div
      className="flex w-full max-w-md items-center gap-3 rounded-xl px-3 py-2 mb-3"
      style={{
        background: 'var(--color-surface)',
        border: '1px solid var(--color-border)',
      }}
    >
      <audio ref={audioRef} src={src} preload="metadata" />

      <button
        onClick={toggle}
        aria-label={playing ? 'Pause voice reply' : 'Play voice reply'}
        className="flex items-center justify-center w-8 h-8 rounded-full transition-colors shrink-0"
        style={{
          background: 'var(--color-accent)',
          color: 'var(--color-on-accent)',
          cursor: 'pointer',
        }}
      >
        {playing ? <Pause size={14} /> : <Play size={14} className="ml-0.5" />}
      </button>

      <input
        type="range"
        min={0}
        max={duration > 0 ? duration : 0}
        step={0.1}
        value={duration > 0 ? Math.min(currentTime, duration) : 0}
        onChange={seek}
        aria-label="Voice reply playback position"
        className="h-1.5 min-w-0 flex-1 cursor-pointer"
        style={{ accentColor: 'var(--color-accent)' }}
      />
    </div>
  );
}
