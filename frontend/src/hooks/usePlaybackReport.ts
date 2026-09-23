/**
 * Tells the server while a reply is being heard, so its own voices (a
 * reminder, an initiative line) wait for the end of it rather than the end
 * of its synthesis, which comes about halfway through. Repeated while it
 * plays: the server forgets it after 20 s, so a closed tab never holds a
 * reminder back.
 */

import { useEffect } from 'react';
import { apiFetch } from '../lib/api';
import { useAppStore } from '../lib/store';

const REPEAT_MS = 8_000;

function report(playing: boolean): void {
  void apiFetch('/v1/speech/playback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ playing }),
  }).catch(() => undefined);
}

export function usePlaybackReport(): void {
  const audioPlaying = useAppStore((s) => s.audioPlaying);

  useEffect(() => {
    report(audioPlaying);
    if (!audioPlaying) return;
    const repeat = setInterval(() => report(true), REPEAT_MS);
    return () => clearInterval(repeat);
  }, [audioPlaying]);
}
