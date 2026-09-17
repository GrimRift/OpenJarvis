// Wake-word acknowledgement: a short pre-rendered clip in Sage's own voice,
// played the instant the wake word fires so the user hears they were heard
// without waiting for a model round trip. Clips are generated once by
// scripts/generate_greetings.py — synthesizing per trigger would put a
// network call at exactly the moment latency is most obvious.

import { volumeFor } from './volume';

const MANIFEST_URL = 'greetings/manifest.json';
const FALLBACK_CLIPS = ['greetings/jarvis/hello-sir-sonic36.mp3'];

interface GreetingManifest {
  default_voice_id: string;
  voices: Record<string, string[]>;
  /** "One moment" lines for a wait that has gone on (M36). */
  fillers?: Record<string, string[]>;
  /** "Still working on it" lines for a wait that goes on after that. */
  fillers_again?: Record<string, string[]>;
}

export type ClipKind = 'greetings' | 'fillers' | 'fillers_again';

let manifestPromise: Promise<GreetingManifest | null> | null = null;
const lastPlayed: Record<ClipKind, string | null> = {
  greetings: null,
  fillers: null,
  fillers_again: null,
};

function loadManifest(): Promise<GreetingManifest | null> {
  if (!manifestPromise) {
    manifestPromise = fetch(MANIFEST_URL)
      .then((res) => (res.ok ? res.json() : null))
      .then((value: unknown) => {
        if (!value || typeof value !== 'object') return null;
        const manifest = value as Partial<GreetingManifest>;
        if (!manifest.voices || typeof manifest.default_voice_id !== 'string') return null;
        return manifest as GreetingManifest;
      })
      .catch(() => null);
  }
  return manifestPromise;
}

export function clipsForVoice(
  manifest: GreetingManifest | null,
  voiceId: string,
  kind: ClipKind = 'greetings',
): string[] {
  if (!manifest) return kind === 'greetings' ? FALLBACK_CLIPS : [];
  const table =
    kind === 'greetings'
      ? manifest.voices
      : kind === 'fillers'
        ? (manifest.fillers ?? {})
        : (manifest.fillers_again ?? {});
  const selected = table[voiceId];
  if (Array.isArray(selected) && selected.length > 0) return selected;
  const fallback = table[manifest.default_voice_id];
  if (Array.isArray(fallback) && fallback.length > 0) return fallback;
  // A filler is optional: with none rendered, the turn is simply quiet
  // until the answer, as it was before.
  return kind === 'greetings' ? FALLBACK_CLIPS : [];
}

/** Warm the manifest and decoder before the first trigger. */
export function preloadGreetings(): void {
  loadManifest().then((manifest) => {
    const clips = manifest
      ? Array.from(
          new Set([
            ...Object.values(manifest.voices).flat(),
            ...Object.values(manifest.fillers ?? {}).flat(),
            ...Object.values(manifest.fillers_again ?? {}).flat(),
          ]),
        )
      : FALLBACK_CLIPS;
    for (const clip of clips) {
      const audio = new Audio(clip);
      audio.preload = 'auto';
      // Referenced only to keep the fetch alive; the browser caches the
      // decoded audio, which is what makes the first real trigger instant.
      audio.load();
    }
  });
}

export interface GreetingOptions {
  voiceId: string;
  /** Called when the clip could not be played at all.
   *
   * Playback failure has to stay non-fatal (listening must start either
   * way), but it must not be silent: a browser refusing autoplay and a
   * greeting being cancelled instantly look identical from the outside —
   * both are "it never greeted" — and telling them apart from the console
   * alone is guesswork.
   */
  onFailure?: (reason: string) => void;
}

/** Resolves when the clip has finished playing (or could not play at all). */
/** Longest a greeting clip may take before the caller stops waiting on it. */
const GREETING_TIMEOUT_MS = 8000;

export function playGreeting(options: GreetingOptions): Promise<void> {
  return playClip('greetings', options);
}

/**
 * "One moment, sir." while a tool call runs on. Plain audio, deliberately
 * not registered as `audioPlaying`: that flag's falling edge re-arms the
 * microphone for continuous conversation, and a filler ending mid-turn
 * must not open the mic while the answer is still being generated.
 */
export function playFiller(
  options: GreetingOptions & { again?: boolean },
): Promise<void> {
  return playClip(options.again ? 'fillers_again' : 'fillers', options);
}

function playClip(kind: ClipKind, options: GreetingOptions): Promise<void> {
  return loadManifest().then(
    (manifest) =>
      new Promise<void>((resolve) => {
        const clips = clipsForVoice(manifest, options.voiceId, kind);
        if (clips.length === 0) {
          resolve();
          return;
        }
        // Avoid repeating the previous clip so consecutive triggers don't
        // sound like a stuck recording.
        const previous = lastPlayed[kind];
        const choices = clips.length > 1 ? clips.filter((c) => c !== previous) : clips;
        const clip = choices[Math.floor(Math.random() * choices.length)];
        lastPlayed[kind] = clip;

        const audio = new Audio(clip);
        audio.volume = volumeFor('ack');
        let settled = false;
        let timer: ReturnType<typeof setTimeout> | undefined;
        const finish = () => {
          if (settled) return;
          settled = true;
          if (timer !== undefined) clearTimeout(timer);
          resolve();
        };
        // A clip that starts and then stalls fires neither `ended` nor
        // `error`, so without this the promise never settles. The wake word
        // awaits it while holding its busy flag, so one stalled greeting
        // disarmed the wake word for the rest of the session -- only a page
        // reload brought it back. The clips are a second or two; anything
        // past this cap is not going to arrive.
        timer = setTimeout(() => {
          options.onFailure?.(`${clip} stalled during playback`);
          finish();
        }, GREETING_TIMEOUT_MS);
        audio.onended = finish;
        // Never leave the caller waiting on a clip that can't play (missing
        // file, autoplay policy, decode error) — listening must still start.
        audio.onerror = () => {
          options.onFailure?.(`could not load ${clip}`);
          finish();
        };
        audio.play().catch((err: unknown) => {
          const reason = err instanceof Error ? `${err.name}: ${err.message}` : String(err);
          options.onFailure?.(reason);
          finish();
        });
      }),
  );
}
