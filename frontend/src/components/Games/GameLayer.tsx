/** Mounts the open game panel, once, above the whole app. */

import { useGamePresenter } from '../../lib/game-presenter';
import { GameOverlay } from './GameOverlay';

export function GameLayer() {
  const current = useGamePresenter((s) => s.current);
  const visible = useGamePresenter((s) => s.visible);
  const close = useGamePresenter((s) => s.close);
  if (!current || !visible) return null;
  return <GameOverlay panel={current} onClose={close} />;
}
