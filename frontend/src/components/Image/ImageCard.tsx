/**
 * What a generated picture leaves in the conversation (M40): a thumbnail that
 * opens it full size. Rebuilt from the persisted tool call, so it survives a
 * reload; the overlay is transient, this is the record.
 */

import { formatImageCost, type GeneratedImage } from '../../lib/generated-image';
import { useImagePresenter } from '../../lib/image-presenter';
import { useImageBlob } from './useImageBlob';

export function ImageCard({ image }: { image: GeneratedImage }) {
  const { src, error } = useImageBlob(image.url);
  const open = useImagePresenter((s) => s.open);
  const cost = formatImageCost({
    count: 1,
    usd: image.cost_usd ?? 0,
    unknown: image.cost_usd === null ? 1 : 0,
  });

  return (
    <div
      className="my-3"
      style={{
        border: '1px solid var(--color-border)',
        borderRadius: 'var(--radius-lg)',
        background: 'var(--color-surface)',
        overflow: 'hidden',
        maxWidth: 420,
      }}
    >
      <button
        type="button"
        onClick={() => open(image)}
        aria-label={`Open picture: ${image.prompt}`}
        className="block w-full"
        style={{ padding: 0, border: 'none', background: 'var(--color-bg-secondary)', cursor: 'zoom-in' }}
      >
        {src ? (
          <img src={src} alt={image.prompt} className="block w-full" style={{ aspectRatio: '1 / 1', objectFit: 'cover' }} />
        ) : (
          <div
            className="flex items-center justify-center text-xs"
            style={{ aspectRatio: '1 / 1', color: 'var(--color-text-tertiary)' }}
          >
            {error ? `Could not load the picture (${error})` : 'Loading the picture...'}
          </div>
        )}
      </button>
      <div className="flex items-center gap-2 px-3 py-2 text-[11.5px]" style={{ color: 'var(--color-text-tertiary)' }}>
        <span className="truncate" title={image.prompt}>
          {image.kind === 'edit' ? 'Edited' : 'Created'} · {image.model}
        </span>
        <span className="ml-auto whitespace-nowrap">{cost}</span>
      </div>
    </div>
  );
}
