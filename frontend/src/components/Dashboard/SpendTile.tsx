/**
 * What Sage has spent on cloud calls, with pictures broken out (M40).
 *
 * The Dashboard showed no money at all; telemetry.db already has a cost per
 * call. A picture whose price was unknown is counted as unknown, never shown
 * as free.
 */

import { useEffect, useState } from 'react';
import { DollarSign } from 'lucide-react';
import { fetchSpend, type Spend } from '../../lib/images-api';

const DAYS = 30;

export function SpendTile() {
  const [spend, setSpend] = useState<Spend | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      fetchSpend(DAYS).then(
        (value) => alive && (setSpend(value), setError(null)),
        (e: Error) => alive && setError(e.message),
      );
    load();
    const timer = setInterval(load, 30_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const images = spend
    ? `${spend.image_count} ${spend.image_count === 1 ? 'picture' : 'pictures'} · $${spend.images_usd.toFixed(3)}` +
      (spend.images_cost_unknown ? ` + ${spend.images_cost_unknown} unpriced` : '')
    : '';

  return (
    <div className="hud-panel p-6">
      <h3 className="hud-label flex items-center gap-2 mb-4">
        <DollarSign size={12} style={{ color: 'var(--color-accent)' }} />
        Cloud spend · last {DAYS} days
      </h3>
      {spend ? (
        <div className="flex flex-wrap items-end gap-x-10 gap-y-3">
          <div>
            <div className="hud-mono text-2xl font-semibold" style={{ color: 'var(--color-text)' }}>
              ${spend.total_usd.toFixed(2)}
            </div>
            <div className="text-xs mt-1" style={{ color: 'var(--color-text-tertiary)' }}>All cloud calls</div>
          </div>
          <div>
            <div className="hud-mono text-lg" style={{ color: 'var(--color-text)' }}>{images}</div>
            <div className="text-xs mt-1" style={{ color: 'var(--color-text-tertiary)' }}>Images</div>
          </div>
        </div>
      ) : (
        <div className="text-sm hud-mono" style={{ color: 'var(--color-text-tertiary)' }}>
          {error ? `Could not load spend: ${error}` : 'loading…'}
        </div>
      )}
    </div>
  );
}
