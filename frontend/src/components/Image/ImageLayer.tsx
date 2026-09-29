/** Mounts whichever generated picture is open, once, above the whole app. */

import { useImagePresenter } from '../../lib/image-presenter';
import { ImageOverlay } from './ImageOverlay';

export function ImageLayer() {
  const current = useImagePresenter((s) => s.current);
  const close = useImagePresenter((s) => s.close);
  if (!current) return null;
  return <ImageOverlay image={current} onClose={close} />;
}
