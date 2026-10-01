/** Mounts the open system panel (M41), once, above the whole app. */

import { useSystemPresenter } from '../../lib/system-presenter';
import { SystemOverlay } from './SystemOverlay';

export function SystemLayer() {
  const current = useSystemPresenter((s) => s.current);
  const close = useSystemPresenter((s) => s.close);
  if (!current) return null;
  return <SystemOverlay report={current} onClose={close} />;
}
