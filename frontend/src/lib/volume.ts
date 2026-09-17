/**
 * Sage's volumes, per channel, as the server holds them (speech/volume.py).
 *
 * The browser plays chat replies and the wake-word clips itself, so it
 * needs the same numbers the server uses for the moments and reminders.
 * Fetched once at start and again after every change from Settings; a
 * play that happens before the first fetch lands uses full volume, which
 * is the file's default too.
 */

import { apiFetch } from './api';

export type VolumeChannel = 'chat' | 'ack' | 'moments' | 'reminders' | 'chime';

export interface Volumes {
  master: number;
  chat: number;
  ack: number;
  moments: number;
  reminders: number;
  chime: number;
}

export const DEFAULT_VOLUMES: Volumes = {
  master: 1,
  chat: 1,
  ack: 1,
  moments: 1,
  reminders: 1,
  chime: 1,
};

let current: Volumes = { ...DEFAULT_VOLUMES };

function clamp(value: unknown, fallback = 1): number {
  const n = typeof value === 'number' && Number.isFinite(value) ? value : fallback;
  return Math.min(1, Math.max(0, n));
}

export function normaliseVolumes(raw: unknown): Volumes {
  const data = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  return {
    master: clamp(data.master),
    chat: clamp(data.chat),
    ack: clamp(data.ack),
    moments: clamp(data.moments),
    reminders: clamp(data.reminders),
    chime: clamp(data.chime),
  };
}

/** master × channel, 0-1: what a sound on this channel plays at. */
export function effectiveVolume(volumes: Volumes, channel: VolumeChannel): number {
  return clamp(volumes.master * volumes[channel]);
}

/** The level for a channel right now, from the last fetched values. */
export function volumeFor(channel: VolumeChannel): number {
  return effectiveVolume(current, channel);
}

export function currentVolumes(): Volumes {
  return { ...current };
}

export async function fetchVolumes(): Promise<Volumes> {
  const res = await apiFetch('/v1/speech/volume');
  if (!res.ok) throw new Error(`Volume unavailable (${res.status})`);
  current = normaliseVolumes(await res.json());
  return { ...current };
}

export async function updateVolumes(patch: Partial<Volumes>): Promise<Volumes> {
  const res = await apiFetch('/v1/speech/volume', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Could not save volume (${res.status})`);
  current = normaliseVolumes(await res.json());
  return { ...current };
}
