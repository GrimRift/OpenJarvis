/** Settings > Tools > Search engine, kept on the server (voice turns use it too). */

import { apiFetch } from './api';

export interface SearchEngineInfo {
  id: string;
  label: string;
  key_name: string;
  key_set: boolean;
  /** A second key used when the first is out of credits (Tavily only). */
  backup_key_name: string | null;
  backup_key_set: boolean;
  /** Out of credits or key refused; skipped for half an hour. */
  passed_over: boolean;
}

export interface SearchSettings {
  engine: string;
  engines: SearchEngineInfo[];
}

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

export async function fetchSearchSettings(): Promise<SearchSettings> {
  return json(await apiFetch('/v1/search/settings'));
}

export async function saveSearchEngine(engine: string): Promise<SearchSettings> {
  return json(
    await apiFetch('/v1/search/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ engine }),
    }),
  );
}
