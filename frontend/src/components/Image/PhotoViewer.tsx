/**
 * A photo from an answer, over the whole app (lib/photo-viewer.ts).
 *
 * Looks and closes like the generated-picture overlay. Unlike it, the
 * picture is someone else's: it loads straight from its site, may fail to,
 * and offers its source page in the browser for whoever wants the context.
 */

import { useEffect, useState } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { isBackdropClick } from '../../lib/overlay-backdrop';
import { usePhotoViewer } from '../../lib/photo-viewer';

const pill = {
  padding: '8px 16px',
  borderRadius: 999,
  border: '1px solid rgba(255,255,255,0.18)',
  background: 'rgba(24,24,27,0.85)',
  color: '#fafafa',
  fontSize: 13,
  fontWeight: 600,
  cursor: 'pointer',
  textDecoration: 'none',
} as const;

export function PhotoViewer() {
  const photos = usePhotoViewer((s) => s.photos);
  const index = usePhotoViewer((s) => s.index);
  const step = usePhotoViewer((s) => s.step);
  const close = usePhotoViewer((s) => s.close);
  const [actualSize, setActualSize] = useState(false);
  const [failed, setFailed] = useState(false);
  const photo = photos[index];

  useEffect(() => {
    setActualSize(false);
    setFailed(false);
  }, [photo?.src]);

  useEffect(() => {
    if (!photo) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
      else if (event.key === 'ArrowRight') step(1);
      else if (event.key === 'ArrowLeft') step(-1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [photo, close, step]);

  if (!photo) return null;
  const several = photos.length > 1;
  const sourcePage = photo.page ?? (photo.src.startsWith('http') ? photo.src : undefined);

  const arrow = (delta: number, side: 'left' | 'right') => (
    <button
      type="button"
      aria-label={delta < 0 ? 'Previous photo' : 'Next photo'}
      onClick={(event) => {
        event.stopPropagation();
        step(delta);
      }}
      style={{
        position: 'absolute',
        top: '50%',
        [side]: 20,
        transform: 'translateY(-50%)',
        width: 44,
        height: 44,
        borderRadius: 999,
        border: '1px solid rgba(255,255,255,0.18)',
        background: 'rgba(24,24,27,0.85)',
        color: '#fafafa',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        cursor: 'pointer',
      }}
    >
      {delta < 0 ? <ChevronLeft size={22} /> : <ChevronRight size={22} />}
    </button>
  );

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 80 }} role="presentation">
      <div style={{ position: 'absolute', inset: 0, background: 'rgba(10, 10, 11, 0.62)', backdropFilter: 'blur(2px)' }} />
      <div
        style={{
          position: 'absolute',
          inset: 0,
          boxSizing: 'border-box',
          padding: '56px 84px 36px',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: actualSize ? 'flex-start' : 'center',
          gap: 16,
          overflow: 'auto',
          color: '#fafafa',
        }}
        onClick={(event) => {
          if (isBackdropClick(event)) close();
        }}
        role="dialog"
        aria-modal="true"
        aria-label={photo.description || 'Photo'}
      >
        {failed ? (
          <div style={{ fontSize: 14, color: '#a1a1aa', padding: 40 }}>
            This picture's site would not let it load here.
          </div>
        ) : (
          <img
            src={photo.src}
            alt={photo.description || ''}
            referrerPolicy="no-referrer"
            onError={() => setFailed(true)}
            onClick={() => setActualSize((value) => !value)}
            style={{
              maxWidth: actualSize ? 'none' : 'min(88vw, 1100px)',
              maxHeight: actualSize ? 'none' : 'calc(100vh - 190px)',
              borderRadius: 14,
              boxShadow: '0 24px 70px rgba(0,0,0,0.55)',
              cursor: actualSize ? 'zoom-out' : 'zoom-in',
              background: '#18181b',
            }}
          />
        )}

        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, maxWidth: 760 }}>
          {photo.description ? (
            <div style={{ fontSize: 14, color: '#e4e4e7', textAlign: 'center', lineHeight: 1.45 }}>
              {photo.description}
            </div>
          ) : null}
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            {several ? (
              <span style={{ fontSize: 12, color: '#a1a1aa' }}>
                {index + 1} of {photos.length}
              </span>
            ) : null}
            {sourcePage ? (
              <a
                href={sourcePage}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(event) => event.stopPropagation()}
                style={{ ...pill, fontSize: 12, padding: '6px 14px' }}
              >
                {photo.page ? 'Open source page' : 'Open in browser'}
              </a>
            ) : null}
          </div>
          <div style={{ fontSize: 11.5, color: '#71717a' }}>
            Esc, Close or click outside to dismiss
            {several ? ' · arrow keys for the others' : ''} · click the picture for full size
          </div>
        </div>
      </div>

      {several ? arrow(-1, 'left') : null}
      {several ? arrow(1, 'right') : null}

      <button
        type="button"
        aria-label="Close photo"
        onClick={(event) => {
          event.stopPropagation();
          close();
        }}
        style={{ ...pill, position: 'absolute', top: 18, right: 20 }}
      >
        Close
      </button>
    </div>
  );
}
