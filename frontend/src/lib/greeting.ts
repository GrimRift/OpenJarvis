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

export interface GreetingPlayback {
  /** Resolves when the clip finishes, is cut short, or fails to play. */
  done: Promise<void>;
  /** Stop immediately — used when the user talks over the greeting. */
  cancel: () => void;
}

export function playGreeting(): GreetingPlayback {
  let cancel = () => {};
  const done = loadClips().then(
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
        cancel = () => {
          audio.pause();
          finish();
        };
        audio.onended = finish;
        // Never leave the caller waiting on a clip that can't play (missing
        // file, autoplay policy, decode error) — listening must still start.
        audio.onerror = finish;
        audio.play().catch(finish);
      }),
  );
  return { done, cancel: () => cancel() };
}
