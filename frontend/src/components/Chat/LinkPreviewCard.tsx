import { useState } from 'react';
import { ExternalLink } from 'lucide-react';
import type { LinkPreview } from '../../lib/link-preview';

export function LinkPreviewCard({ preview }: { preview: LinkPreview }) {
  const hostname = new URL(preview.url).hostname.replace(/^www\./, '');
  // The server only attaches an image it believes in, but "the URL looked
  // fine" and "the browser could fetch it" are different claims. Without
  // this, a thumbnail that 404s or is hotlink-blocked left a 192px empty box
  // above the text -- which is what the user saw on the ESA/Hubble card.
  // Dropping it falls back to the text-only card, which was always the
  // intended shape for a source with no usable image.
  const [imageFailed, setImageFailed] = useState(false);

  return (
    <a
      href={preview.url}
      target="_blank"
      rel="noopener noreferrer"
      className="group/link mt-3 block overflow-hidden rounded-xl border no-underline transition-colors"
      style={{
        borderColor: 'var(--color-border)',
        background: 'var(--color-bg-secondary)',
        color: 'var(--color-text-primary)',
      }}
      aria-label={`Open ${preview.title} in a new tab`}
    >
      {preview.imageUrl && !imageFailed && (
        <img
          src={preview.imageUrl}
          alt=""
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          onError={() => setImageFailed(true)}
          className="h-48 w-full object-cover"
        />
      )}
      <div className="p-3">
        <div className="mb-1 flex items-center justify-between gap-3">
          <span className="text-xs" style={{ color: 'var(--color-accent)' }}>
            {hostname}{preview.publishedDate ? ` · ${preview.publishedDate}` : ''}
          </span>
          <ExternalLink size={13} aria-hidden="true" style={{ color: 'var(--color-text-tertiary)' }} />
        </div>
        <div className="text-sm font-semibold">{preview.title}</div>
        {preview.summary && (
          <p className="mt-1 line-clamp-3 text-xs leading-relaxed" style={{ color: 'var(--color-text-secondary)' }}>
            {preview.summary}
          </p>
        )}
      </div>
    </a>
  );
}
