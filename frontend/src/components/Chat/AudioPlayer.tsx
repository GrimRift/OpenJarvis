import { useRef, useState, useEffect, useCallback } from 'react';
import { Play, Pause, Volume2 } from 'lucide-react';
import { useAppStore } from '../../lib/store';

interface AudioPlayerProps {
  src: string;
  autoPlay?: boolean;
  label?: string;
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export function AudioPlayer({ src, autoPlay = false, label = 'Morning Digest' }: AudioPlayerProps) {
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

    el.addEventListener('timeupdate', onTime);
    el.addEventListener('loadedmetadata', onMeta);
    el.addEventListener('ended', onEnded);
    return () => {
      el.removeEventListener('timeupdate', onTime);
      el.removeEventListener('loadedmetadata', onMeta);
      el.removeEventListener('ended', onEnded);
    };
  }, []);

  // Mirror into the store so the orb (rendered elsewhere) can show a
  // "speaking" state for the actual duration of the spoken audio, not
  // just the response's text-streaming window.
  useEffect(() => {
    useAppStore.getState().setAudioPlaying(playing);
    return () => {
      if (playing) useAppStore.getState().setAudioPlaying(false);
    };
  }, [playing]);

  const progress = duration > 0 ? (currentTime / duration) * 100 : 0;

  const seek = (e: React.MouseEvent<HTMLDivElement>) => {
    const el = audioRef.current;
    if (!el || !duration) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const pct = (e.clientX - rect.left) / rect.width;
    el.currentTime = pct * duration;
  };

  return (
    <div
      className="flex items-center gap-3 px-4 py-3 rounded-xl mb-3"
      style={{
        background: 'var(--color-surface)',
        border: '1px solid var(--color-border)',
      }}
    >
      <audio ref={audioRef} src={src} preload="metadata" />

      <button
        onClick={toggle}
        className="flex items-center justify-center w-9 h-9 rounded-full transition-colors shrink-0"
        style={{
          background: 'var(--color-accent)',
          color: 'var(--color-on-accent)',
          cursor: 'pointer',
        }}
      >
        {playing ? <Pause size={16} /> : <Play size={16} className="ml-0.5" />}
      </button>

      <div className="flex flex-col gap-1.5 flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <Volume2 size={14} style={{ color: 'var(--color-text-tertiary)' }} />
          <span
            className="text-xs font-medium"
            style={{ color: 'var(--color-text-secondary)' }}
          >
            {label}
          </span>
        </div>

        <div
          className="h-1.5 rounded-full cursor-pointer"
          style={{ background: 'var(--color-bg-tertiary)' }}
          onClick={seek}
        >
          <div
            className="h-full rounded-full transition-all"
            style={{
              width: `${progress}%`,
              background: 'var(--color-accent)',
            }}
          />
        </div>

        <div
          className="flex justify-between text-xs"
          style={{ color: 'var(--color-text-tertiary)' }}
        >
          <span>{formatTime(currentTime)}</span>
          <span>{duration > 0 ? formatTime(duration) : '--:--'}</span>
        </div>
      </div>
    </div>
  );
}
