import { ExternalLink } from 'lucide-react';
import type { LinkPreview } from '../../lib/link-preview';

export function LinkPreviewCard({ preview }: { preview: LinkPreview }) {
  const hostname = new URL(preview.url).hostname.replace(/^www\./, '');

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
      {preview.imageUrl && (
        <img
          src={preview.imageUrl}
          alt=""
          loading="lazy"
          decoding="async"
          crossOrigin="anonymous"
          referrerPolicy="no-referrer"
          className="h-48 w-full object-cover"
        />
      )}
      <div className="p-3">
        <div className="mb-1 flex items-center justify-between gap-3">
          <span className="text-xs" style={{ color: 'var(--color-accent)' }}>{hostname}</span>
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
