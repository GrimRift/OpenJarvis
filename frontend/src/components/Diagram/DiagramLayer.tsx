/**
 * Mounts whichever diagram is open, once, above the whole app.
 *
 * It latches whether the voice ever started: "spoken" decides how the overlay
 * leaves (with the voice, or when the user says so), and the voice begins a
 * beat after the diagram appears, so asking at the moment it opens would
 * always answer no.
 */

import { useEffect, useRef } from 'react';
import { useDiagramPresenter } from '../../lib/diagram-presenter';
import { useAppStore } from '../../lib/store';
import { DiagramOverlay } from './DiagramOverlay';

export function DiagramLayer() {
  const current = useDiagramPresenter((s) => s.current);
  const spoken = useDiagramPresenter((s) => s.spoken);
  const close = useDiagramPresenter((s) => s.close);
  const audioPlaying = useAppStore((s) => s.audioPlaying);
  const everSpoke = useRef(false);

  useEffect(() => {
    if (!current) everSpoke.current = false;
    else if (audioPlaying) everSpoke.current = true;
  }, [current, audioPlaying]);

  if (!current) return null;

  return (
    <DiagramOverlay
      diagram={current}
      spoken={spoken}
      speaking={audioPlaying}
      wasSpoken={everSpoke.current}
      onClose={close}
    />
  );
}
