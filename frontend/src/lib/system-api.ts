/** The system panel's live feed and End button (M41). */

import { useEffect, useRef, useState } from 'react';
import { apiFetch } from './api';
import type { SystemPart, SystemSnapshot } from './system-report';

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* keep the status */
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export async function fetchSystemStats(part?: SystemPart | null): Promise<SystemSnapshot> {
  return json(await apiFetch(`/v1/system/stats${part ? `?part=${part}` : ''}`));
}

export async function endProgram(app: string): Promise<{ app: string; ended: number; still_running: number }> {
  return json(
    await apiFetch('/v1/system/end', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ app }),
    }),
  );
}

/** Seconds of history the graphs keep. */
export const HISTORY = 60;

export interface SystemHistory {
  cpu: number[];
  ram: number[];
  gpu: number[];
}

/**
 * Polls once a second while `active` (the panel or tile is on screen) and
 * keeps the last minute of CPU / RAM / GPU for the graphs. Stops on unmount.
 */
export function useSystemStats(active: boolean, seed: SystemSnapshot | null, part?: SystemPart | null) {
  const [snap, setSnap] = useState<SystemSnapshot | null>(seed);
  const [history, setHistory] = useState<SystemHistory>({ cpu: [], ram: [], gpu: [] });
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    if (!active) return undefined;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const next = await fetchSystemStats(part);
        if (!alive.current) return;
        setSnap(next);
        setError(null);
        setHistory((h) => ({
          cpu: [...h.cpu, next.cpu.percent].slice(-HISTORY),
          ram: [...h.ram, next.ram.percent].slice(-HISTORY),
          gpu: [...h.gpu, next.gpu?.percent ?? 0].slice(-HISTORY),
        }));
      } catch (e) {
        if (alive.current) setError((e as Error).message);
      }
      if (alive.current) timer = window.setTimeout(tick, 1000);
    };
    void tick();
    return () => {
      alive.current = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [active, part]);

  return { snap, history, error };
}
