import { apiFetch } from './api';
import { voiceTrace } from './voice-trace';

/**
 * The page's half of the media hold (server: speech/media_hold.py). A video
 * in the room mixes into the mic and the user's own words over it scored as
 * unsure as the video's dialogue (6 October), so other apps' media is turned
 * down ("duck") when the user is heard talking to Sage and while Sage speaks,
 * and given back ("release") when the exchange is over -- turned down, not
 * paused, by the user's choice. A confirmed wake word ducks on the server.
 * Fire-and-forget: the voice never waits on it.
 */
export type MediaHoldAction = 'duck' | 'release';

let held = false;

/**
 * Whether the page last asked for other apps to be turned down. While they
 * are, a video's voice reaches the echo-cancelled microphone far under the
 * user's (peak 697 against 3.2-4.2k, 7 October), so the reply may dip for
 * the user over it (lib/voice-duck.ts); at full volume it may not.
 */
export function mediaHeld(): boolean {
  return held;
}

export function holdMedia(action: MediaHoldAction): void {
  held = action === 'duck';
  voiceTrace('media.hold', { action });
  void apiFetch('/v1/voice/media', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  }).catch(() => {});
}

/** How long the exchange must stay idle before the media comes back: the
 * gap between the answer's text and its voice is idle too. */
export const MEDIA_RELEASE_IDLE_MS = 2500;
