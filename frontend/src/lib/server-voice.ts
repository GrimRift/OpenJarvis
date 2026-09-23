/**
 * What the server is saying aloud, for the orb.
 *
 * Reminders, schedule notices and moments are spoken by the server through
 * the machine's speakers, not by this tab, so nothing here knew Sage was
 * talking and the orb sat in standing by through every one of them. The
 * server now pushes when a voice starts and stops, with its loudness every
 * 20 ms, and the orb speaks along with it.
 *
 * Deliberately apart from `audioPlaying`. That flag's falling edge re-arms
 * the microphone for a follow-up, and the server's voice must never do that
 * -- it would open the mic on Sage's own last words. This moves the orb and
 * nothing else.
 */

import { smoothLevel } from './audio-level';

export interface ServerVoiceEvent {
  speaking: boolean;
  id?: number;
  channel?: string;
  elapsed_ms?: number;
  step_ms?: number;
  envelope?: number[] | null;
}

interface Voice {
  startedAt: number;
  stepMs: number;
  envelope: number[] | null;
}

/**
 * How long after the server marks a voice the sound is actually heard: the
 * player has to start and fill its buffer. The envelope is read this far
 * behind, so a syllable lights the orb as it is heard, not before.
 */
export const AUDIO_LEAD_MS = 150;

let current: Voice | null = null;
let shown = 0;
let lastAt = 0;

/** Take in an event from the stream. Returns whether the server is speaking. */
export function applyServerVoice(event: ServerVoiceEvent, receivedAt = performance.now()): boolean {
  if (!event.speaking) {
    current = null;
    return false;
  }
  current = {
    // Tuning in part-way lines the envelope up with the sound.
    startedAt: receivedAt - (event.elapsed_ms ?? 0),
    stepMs: event.step_ms && event.step_ms > 0 ? event.step_ms : 20,
    envelope: Array.isArray(event.envelope) && event.envelope.length > 0 ? event.envelope : null,
  };
  return true;
}

export function clearServerVoice(): void {
  current = null;
}

/** How long a voice can go on before it is taken to be over, in ms: its own
 * length and some, or two minutes for a voice with no measured length. A
 * stop that never arrives must not hold the orb in speaking. */
export function serverVoiceTimeoutMs(event: ServerVoiceEvent): number {
  const env = event.envelope;
  const step = event.step_ms && event.step_ms > 0 ? event.step_ms : 20;
  return Array.isArray(env) && env.length > 0 ? env.length * step + 10_000 : 120_000;
}

/**
 * A syllable train for a voice with nothing measured -- the built-in Windows
 * voice plays with no file. About four syllables a second at varied
 * strength, with the occasional breath, so the orb still talks rather than
 * sitting at rest in the speaking state.
 */
export function syntheticSpeech(elapsedMs: number): number {
  const frames = elapsedMs / (1000 / 60);
  if (Math.sin(frames * 0.01) < -0.55) return 0;
  const period = 14;
  const idx = Math.floor(frames / period);
  const frac = frames / period - idx;
  const r = Math.abs(Math.sin(idx * 12.9898) * 43758.5453) % 1;
  const env = frac < 0.12 ? frac / 0.12 : Math.pow(1 - (frac - 0.12) / 0.88, 1.5);
  return Math.max(0, Math.min(1, (0.4 + 0.6 * r) * env));
}

/** The server voice's loudness now, 0..1, smoothed like the tab's own. */
export function serverVoiceLevel(now = performance.now()): number {
  const dt = lastAt ? Math.min(3, (now - lastAt) / (1000 / 60)) : 1;
  lastAt = now;
  let target = 0;
  if (current) {
    const heard = now - current.startedAt - AUDIO_LEAD_MS;
    if (heard >= 0) {
      if (current.envelope) {
        const i = Math.floor(heard / current.stepMs);
        target = i < current.envelope.length ? current.envelope[i] : 0;
      } else {
        target = syntheticSpeech(heard);
      }
    }
  }
  shown = smoothLevel(shown, target, dt);
  return shown;
}

/** Split a server-sent-events buffer into its data payloads and what is left
 * over, still incomplete. */
export function takeSseEvents(buffer: string): { events: unknown[]; rest: string } {
  const events: unknown[] = [];
  let rest = buffer.replace(/\r\n/g, '\n');
  let cut = rest.indexOf('\n\n');
  while (cut >= 0) {
    const block = rest.slice(0, cut);
    rest = rest.slice(cut + 2);
    const data = block
      .split('\n')
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trimStart())
      .join('\n');
    if (data) {
      try {
        events.push(JSON.parse(data));
      } catch {
        /* a malformed event is skipped, not fatal to the stream */
      }
    }
    cut = rest.indexOf('\n\n');
  }
  return { events, rest };
}

/** Test seam. */
export function _resetServerVoice(): void {
  current = null;
  shown = 0;
  lastAt = 0;
}
