/**
 * The words Deepgram is told to expect (server: speech/keyterms.py).
 *
 * Unboosted it wrote "addition" for "Hey Sage" and "Close to the diagram"
 * for "close the diagram". The built-in and harvested lists are shown but
 * not editable: they are what Sage already covers, so the user does not
 * retype them.
 */

import { apiFetch } from './api';

export interface Keyterms {
  terms: string[];
  built_in: string[];
  harvested: string[];
  max_terms: number;
  min_length: number;
}

export async function fetchKeyterms(): Promise<Keyterms> {
  const res = await apiFetch('/v1/speech/keyterms');
  if (!res.ok) throw new Error(`Keyterms unavailable (${res.status})`);
  return (await res.json()) as Keyterms;
}

export async function saveKeyterms(terms: string[]): Promise<string[]> {
  const res = await apiFetch('/v1/speech/keyterms', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ terms }),
  });
  if (!res.ok) throw new Error(`Could not save (${res.status})`);
  return ((await res.json()) as { terms: string[] }).terms;
}

/** One word per line, as the Settings box shows them. */
export function parseTerms(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((line) => line.trim())
    .filter(Boolean);
}
