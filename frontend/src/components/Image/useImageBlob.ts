/**
 * Fetch a generated picture with the API key and hand back a blob URL.
 *
 * `/v1/images/{id}` is behind the key like every `/v1` route, and an `<img>`
 * cannot send a header. One fetch per picture, shared by the card and the
 * overlay; a `data:` URL is never used (the desktop app only sends http/https
 * links to the browser).
 */

import { useEffect, useState } from 'react';
import { apiFetch } from '../../lib/api';

const cache = new Map<string, Promise<string>>();

function load(url: string): Promise<string> {
  let pending = cache.get(url);
  if (!pending) {
    pending = apiFetch(url).then(async (response) => {
      if (!response.ok) {
        throw new Error(response.status === 410 ? 'The file is gone' : `HTTP ${response.status}`);
      }
      return URL.createObjectURL(await response.blob());
    });
    // A failure is not cached: the next render may try again.
    pending.catch(() => cache.delete(url));
    cache.set(url, pending);
  }
  return pending;
}

export function useImageBlob(url: string): { src: string | null; error: string | null } {
  const [state, setState] = useState<{ src: string | null; error: string | null }>({ src: null, error: null });
  useEffect(() => {
    let alive = true;
    setState({ src: null, error: null });
    load(url).then(
      (src) => alive && setState({ src, error: null }),
      (error: Error) => alive && setState({ src: null, error: error.message || 'Could not load' }),
    );
    return () => {
      alive = false;
    };
  }, [url]);
  return state;
}
