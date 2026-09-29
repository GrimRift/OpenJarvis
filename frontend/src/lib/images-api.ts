/** Settings > Images and the spend figures (M40), both kept on the server. */

import { apiFetch } from './api';

export interface ImageSettings {
  enabled: boolean;
  model: string;
  quality: string;
  size: string;
  save_dir: string;
  models: string[];
  qualities: string[];
  sizes: string[];
}

export interface Spend {
  total_usd: number;
  images_usd: number;
  image_count: number;
  /** Pictures whose price was not known: counted, never priced at $0. */
  images_cost_unknown: number;
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

export async function fetchImageSettings(): Promise<ImageSettings> {
  return json(await apiFetch('/v1/images/settings'));
}

export async function saveImageSettings(changes: Partial<ImageSettings>): Promise<ImageSettings> {
  return json(
    await apiFetch('/v1/images/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(changes),
    }),
  );
}

export async function fetchSpend(days = 30): Promise<Spend> {
  return json(await apiFetch(`/v1/images/spend?days=${days}`));
}
