import { useEffect, useRef, useState } from 'react';
import { useAppStore } from '../../lib/store';
import {
  chaptersAfter,
  nextReadyChapter,
  sageNeedsTheRoom,
  shouldSavePosition,
  useBookPlayer,
} from '../../lib/book-player';
import { chapterAudioPath, fetchBlobUrl, fetchBook, savePosition } from '../../lib/books';
import { volumeFor } from '../../lib/volume';

/** Quiet this long after Sage is done before the book comes back. */
const RESUME_AFTER_MS = 1500;

/**
 * The one <audio> element audiobooks play through (M42), mounted once in the
 * app so a chapter keeps playing across pages and a voice request ("read me
 * chapter 4") works from anywhere. It steps aside while Sage needs the room
 * and saves "where you are" as it plays (user's choices, 2026-10-13).
 */
export function BookPlayerHost() {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);
  const savedAt = useRef(0);
  const now = useBookPlayer((s) => s.now);
  const wanted = useBookPlayer((s) => s.wanted);
  const seekTick = useBookPlayer((s) => s.seekTick);
  const sageBusy = useAppStore((s) =>
    sageNeedsTheRoom({
      voiceState: s.voiceState,
      replyAudio: s.audioPlaying,
      serverSpeaking: s.serverSpeaking,
      streaming: s.streamState.isStreaming,
    }),
  );
  const quiet = useQuietAfter(sageBusy);

  // Load a chapter when what is playing changes.
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !now) return;
    let cancelled = false;
    fetchBlobUrl(chapterAudioPath(now.book, now.index, now.voice || undefined))
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        if (urlRef.current) URL.revokeObjectURL(urlRef.current);
        urlRef.current = url;
        audio.src = url;
        audio.currentTime = now.startAt;
        savedAt.current = now.startAt;
        if (useBookPlayer.getState().wanted && quiet) void audio.play().catch(() => {});
      })
      .catch(() => useBookPlayer.getState().stop());
    return () => {
      cancelled = true;
    };
    // `quiet` is read when the chapter arrives, not a reason to reload it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [now?.book, now?.index, now?.voice]);

  // Play or step aside.
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !now || !audio.src) return;
    // Sage's voice volume: the book is read at the level Sage speaks at.
    audio.volume = volumeFor('chat');
    if (wanted && quiet) void audio.play().catch(() => {});
    else if (!audio.paused) audio.pause();
  }, [wanted, quiet, now]);

  useEffect(() => {
    const audio = audioRef.current;
    if (audio && seekTick) audio.currentTime = useBookPlayer.getState().seekTo;
  }, [seekTick]);

  useEffect(() => () => {
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
  }, []);

  const save = (force = false) => {
    const audio = audioRef.current;
    const playing = useBookPlayer.getState().now;
    if (!audio || !playing) return;
    if (!force && !shouldSavePosition(audio.currentTime, savedAt.current)) return;
    savedAt.current = audio.currentTime;
    void savePosition(playing.book, playing.index, audio.currentTime).catch(() => {});
  };

  const onEnded = async () => {
    const playing = useBookPlayer.getState().now;
    if (!playing) return;
    try {
      const book = await fetchBook(playing.book);
      const next = nextReadyChapter(book.chapters, playing.index);
      if (next) {
        useBookPlayer.getState().play({ ...playing, index: next.index, label: next.label, startAt: 0 });
        return;
      }
      // Not made yet: park "where you are" on it so rolling renders it next.
      const after = chaptersAfter(book.chapters, playing.index)[0];
      if (after) await savePosition(playing.book, after.index, 0);
    } catch {
      /* the page shows the book's state on its next refresh */
    }
    useBookPlayer.getState().setWanted(false);
  };

  return (
    <audio
      ref={audioRef}
      preload="auto"
      onPlay={() => useBookPlayer.getState().report({ playing: true })}
      onPause={() => {
        useBookPlayer.getState().report({ playing: false });
        save(true);
      }}
      onTimeUpdate={(e) => {
        useBookPlayer.getState().report({ position: e.currentTarget.currentTime });
        save();
      }}
      onLoadedMetadata={(e) => useBookPlayer.getState().report({ duration: e.currentTarget.duration })}
      onDurationChange={(e) => {
        const d = e.currentTarget.duration;
        if (Number.isFinite(d)) useBookPlayer.getState().report({ duration: d });
      }}
      onEnded={() => void onEnded()}
      style={{ display: 'none' }}
    />
  );
}

/** True once `busy` has been false for RESUME_AFTER_MS; false at once when busy. */
function useQuietAfter(busy: boolean): boolean {
  const [quiet, setQuiet] = useState(!busy);
  useEffect(() => {
    if (busy) {
      setQuiet(false);
      return;
    }
    const t = window.setTimeout(() => setQuiet(true), RESUME_AFTER_MS);
    return () => window.clearTimeout(t);
  }, [busy]);
  return quiet;
}
