/**
 * The system panel's Dashboard tile (M41, design D1): live bars while the
 * Dashboard is on screen; a click opens the full panel.
 */

import { useSystemStats } from '../../lib/system-api';
import { useSystemPresenter } from '../../lib/system-presenter';
import { isHigh } from '../../lib/system-report';
import { AMBER, SYSTEM_STYLE } from '../../lib/system-style';
import { Bar } from '../System/SystemParts';

export function SystemTile() {
  const { snap, error } = useSystemStats(true, null);
  const open = useSystemPresenter((s) => s.open);

  const rows = snap
    ? [
        { name: 'CPU', percent: snap.cpu.percent, high: isHigh('cpu', snap.cpu.percent) },
        { name: 'RAM', percent: snap.ram.percent, high: isHigh('ram', snap.ram.percent) },
        ...(snap.gpu ? [{ name: 'GPU', percent: snap.gpu.percent, high: isHigh('gpu', snap.gpu.percent) }] : []),
        ...(snap.disks[0] ? [{ name: `Disk ${snap.disks[0].name}`, percent: snap.disks[0].percent, high: isHigh('disk', snap.disks[0].percent) }] : []),
      ]
    : [];

  return (
    <button
      type="button"
      disabled={!snap}
      onClick={() => snap && open({ key: `tile:${Date.now()}`, snapshot: snap, focus: null })}
      className="w-full text-left cursor-pointer rounded-xl p-4"
      style={{ background: 'var(--color-surface)', border: '1px solid rgba(34,211,238,0.18)' }}
      aria-label="Open the system panel"
    >
      <style>{SYSTEM_STYLE}</style>
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-semibold" style={{ color: 'var(--color-text)' }}>
          System
        </span>
        <span className="text-[11px] tracking-[0.14em] flex items-center gap-1.5" style={{ color: error ? AMBER : '#4ade80' }}>
          <i style={{ width: 7, height: 7, borderRadius: '50%', background: error ? AMBER : '#4ade80', boxShadow: `0 0 8px ${error ? AMBER : '#4ade80'}`, display: 'inline-block' }} />
          {error ? 'PAUSED' : 'LIVE'}
        </span>
      </div>
      {snap ? (
        rows.map((r) => (
          <div key={r.name} className="grid items-center gap-2.5 py-0.5 text-[12.5px]" style={{ gridTemplateColumns: '58px 1fr 44px', color: 'var(--color-text)' }}>
            <span>{r.name}</span>
            <Bar percent={r.percent} high={r.high} height={6} />
            <span className="text-right" style={{ fontVariantNumeric: 'tabular-nums', color: r.high ? AMBER : undefined }}>
              {Math.round(r.percent)}%
            </span>
          </div>
        ))
      ) : (
        <div className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
          {error ? `Could not read this PC: ${error}` : 'Reading this PC…'}
        </div>
      )}
      {snap?.summary ? (
        <div className="text-[11.5px] mt-2 truncate" style={{ color: 'var(--color-text-tertiary)' }} title={snap.summary}>
          {snap.summary} · open details →
        </div>
      ) : null}
    </button>
  );
}
