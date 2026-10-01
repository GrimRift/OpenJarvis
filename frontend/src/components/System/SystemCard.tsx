/**
 * What a system answer leaves in the conversation (M41): one line that
 * reopens the panel, which then goes live again. Rebuilt from the persisted
 * tool call, so it survives a reload.
 */

import { Cpu } from 'lucide-react';
import { useSystemPresenter } from '../../lib/system-presenter';
import { isHigh, type SystemReport } from '../../lib/system-report';

export function SystemCard({ report }: { report: SystemReport }) {
  const open = useSystemPresenter((s) => s.open);
  const s = report.snapshot;
  const ramHigh = isHigh('ram', s.ram.percent);
  return (
    <button
      type="button"
      onClick={() => open(report)}
      className="my-3 flex items-center gap-3 px-3 py-2 text-left cursor-pointer"
      style={{ border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)', background: 'var(--color-surface)', maxWidth: 420, width: '100%' }}
      aria-label="Open the system panel"
    >
      <Cpu size={24} color="var(--color-accent)" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="text-sm font-semibold" style={{ color: 'var(--color-text)' }}>
          CPU {Math.round(s.cpu.percent)}% ·{' '}
          <span style={{ color: ramHigh ? 'var(--color-accent-amber)' : undefined }}>RAM {Math.round(s.ram.percent)}%</span>
          {s.gpu ? ` · GPU ${Math.round(s.gpu.percent)}%` : ''}
        </div>
        <div className="text-[11.5px] truncate" style={{ color: 'var(--color-text-tertiary)' }}>
          {s.host} · live system panel
        </div>
      </div>
      <span className="text-xs whitespace-nowrap" style={{ color: 'var(--color-accent)' }}>
        Open
      </span>
    </button>
  );
}
