// Wake-word acknowledgement: a short pre-rendered clip in Sage's own voice,
// played the instant the wake word fires so the user hears they were heard
// without waiting for a model round trip. Clips are generated once by
// scripts/generate_greetings.py — synthesizing per trigger would put a
// network call at exactly the moment latency is most obvious.

const MANIFEST_URL = 'greetings/manifest.json';
const FALLBACK_CLIPS = ['greetings/hello-sir.mp3'];

let clipsPromise: Promise<string[]> | null = null;
let lastPlayed: string | null = null;

function loadClips(): Promise<string[]> {
  if (!clipsPromise) {
    clipsPromise = fetch(MANIFEST_URL)
      .then((res) => (res.ok ? res.json() : FALLBACK_CLIPS))
      .then((list: unknown) =>
        Array.isArray(list) && list.length > 0 ? (list as string[]) : FALLBACK_CLIPS,
      )
      .catch(() => FALLBACK_CLIPS);
  }
  return clipsPromise;
}

/** Warm the manifest and decoder before the first trigger. */
export function preloadGreetings(): void {
  loadClips().then((clips) => {
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
export function playGreeting(options?: GreetingOptions): Promise<void> {
  return loadClips().then(
    (clips) =>
      new Promise<void>((resolve) => {
        // Avoid repeating the previous clip so consecutive triggers don't
        // sound like a stuck recording.
        const choices = clips.length > 1 ? clips.filter((c) => c !== lastPlayed) : clips;
        const clip = choices[Math.floor(Math.random() * choices.length)];
        lastPlayed = clip;

        const audio = new Audio(clip);
        let settled = false;
        const finish = () => {
          if (settled) return;
          settled = true;
          resolve();
        };
        audio.onended = finish;
        // Never leave the caller waiting on a clip that can't play (missing
        // file, autoplay policy, decode error) — listening must still start.
        audio.onerror = () => {
          options?.onFailure?.(`could not load ${clip}`);
          finish();
        };
        audio.play().catch((err: unknown) => {
          const reason = err instanceof Error ? `${err.name}: ${err.message}` : String(err);
          options?.onFailure?.(reason);
          finish();
        });
      }),
  );
}
