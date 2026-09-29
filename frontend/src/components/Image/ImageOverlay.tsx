/**
 * A picture Sage made, over the whole app (M40).
 *
 * Styled like the diagram overlay: the app behind is dimmed and the picture
 * floats on it. It closes on Esc, on the Close button, and on a click on the
 * dimmed background (the user's request). Unlike a diagram it does not leave
 * when the voice stops: "Here's your cafe, Sir." is one line, and the picture
 * is what they asked to see.
 */

import { useEffect, useState } from 'react';
import { formatImageCost, type GeneratedImage } from '../../lib/generated-image';
import { isBackdropClick } from '../../lib/overlay-backdrop';
import { useImageBlob } from './useImageBlob';

interface Props {
  image: GeneratedImage;
  onClose: () => void;
}

export function ImageOverlay({ image, onClose }: Props) {
  const { src, error } = useImageBlob(image.url);
  // Fit to the screen first; a click on the picture shows it at full size.
  const [actualSize, setActualSize] = useState(false);

  useEffect(() => {
    setActualSize(false);
  }, [image.id]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const cost = formatImageCost({
    count: 1,
    usd: image.cost_usd ?? 0,
    unknown: image.cost_usd === null ? 1 : 0,
  });
  const details = [image.kind === 'edit' ? 'Edited' : 'Created', image.model, cost]
    .filter(Boolean)
    .join(' · ');

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 80 }} role="presentation">
      <div style={{ position: 'absolute', inset: 0, background: 'rgba(10, 10, 11, 0.62)', backdropFilter: 'blur(2px)' }} />
      <div
        style={{
          position: 'absolute',
          inset: 0,
          boxSizing: 'border-box',
          padding: '56px 40px 36px',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: actualSize ? 'flex-start' : 'center',
          gap: 16,
          overflow: 'auto',
          color: '#fafafa',
        }}
        onClick={(event) => {
          // The whole screen is this layer: a click on the dimmed area closes,
          // a click on the picture or a button does not.
          if (isBackdropClick(event)) onClose();
        }}
        role="dialog"
        aria-modal="true"
        aria-label={image.prompt || 'Generated image'}
      >
        {src ? (
          <img
            src={src}
            alt={image.prompt}
            onClick={() => setActualSize((value) => !value)}
            style={{
              maxWidth: actualSize ? 'none' : 'min(92vw, 1100px)',
              maxHeight: actualSize ? 'none' : 'calc(100vh - 190px)',
              borderRadius: 14,
              boxShadow: '0 24px 70px rgba(0,0,0,0.55)',
              cursor: actualSize ? 'zoom-out' : 'zoom-in',
              // A transparent PNG (background removed) needs something behind it.
              backgroundColor: '#e4e4e7',
              backgroundImage:
                'linear-gradient(45deg, #c4c4c8 25%, transparent 25%), linear-gradient(-45deg, #c4c4c8 25%, transparent 25%), linear-gradient(45deg, transparent 75%, #c4c4c8 75%), linear-gradient(-45deg, transparent 75%, #c4c4c8 75%)',
              backgroundSize: '24px 24px',
              backgroundPosition: '0 0, 0 12px, 12px -12px, -12px 0',
            }}
          />
        ) : (
          <div style={{ fontSize: 14, color: '#a1a1aa', padding: 40 }}>
            {error ? `Could not load the picture: ${error}` : 'Loading the picture...'}
          </div>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, maxWidth: 760 }}>
          {image.prompt ? (
            <div style={{ fontSize: 14, color: '#e4e4e7', textAlign: 'center', lineHeight: 1.45 }}>{image.prompt}</div>
          ) : null}
          <div style={{ fontSize: 11.5, color: '#71717a' }}>
            {details}
            {image.file ? ` · Pictures\\Sage\\${image.file}` : ''}
          </div>
          <div style={{ fontSize: 11.5, color: '#71717a' }}>
            Esc, Close or click outside to dismiss · click the picture for full size
          </div>
        </div>
      </div>

      <button
        type="button"
        aria-label="Close image"
        onClick={(event) => {
          event.stopPropagation();
          onClose();
        }}
        style={{
          position: 'absolute',
          top: 18,
          right: 20,
          padding: '8px 16px',
          borderRadius: 999,
          border: '1px solid rgba(255,255,255,0.18)',
          background: 'rgba(24,24,27,0.85)',
          color: '#fafafa',
          fontSize: 13,
          fontWeight: 600,
          cursor: 'pointer',
        }}
      >
        Close
      </button>
    </div>
  );
}
