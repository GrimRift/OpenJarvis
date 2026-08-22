import { Trophy, ExternalLink } from 'lucide-react';
import { useAppStore } from '../../lib/store';

export function LeaderboardCard() {
  const optInEnabled = useAppStore((s) => s.optInEnabled);
  const setOptInModalOpen = useAppStore((s) => s.setOptInModalOpen);

  return (
    <div className="hud-panel p-6">
      <h3 className="hud-label flex items-center gap-2 mb-4">
        <Trophy size={12} style={{ color: 'var(--color-accent)' }} />
        Leaderboard
      </h3>

      <div className="flex items-center justify-between p-3 rounded-lg" style={{ background: 'var(--color-bg-secondary)' }}>
        <span className="text-sm" style={{ color: 'var(--color-text)' }}>
          Share Your Savings
        </span>
        <div
          onClick={() => setOptInModalOpen(true)}
          className="relative cursor-pointer"
          style={{
            width: 34,
            height: 18,
            borderRadius: 10,
            background: optInEnabled ? 'var(--color-accent-subtle)' : 'var(--color-bg-tertiary)',
            border: `1px solid ${optInEnabled ? 'var(--color-accent)' : 'var(--color-border)'}`,
          }}
        >
          <div
            className="absolute rounded-full transition-all"
            style={{
              top: 1,
              left: optInEnabled ? 17 : 1,
              width: 14,
              height: 14,
              background: optInEnabled ? 'var(--color-accent)' : 'var(--color-text-tertiary)',
            }}
          />
        </div>
      </div>

      <a
        href="https://open-jarvis.github.io/OpenJarvis/leaderboard"
        target="_blank"
        rel="noopener noreferrer"
        className="flex items-center gap-1.5 mt-3 text-xs"
        style={{ color: 'var(--color-accent)' }}
      >
        <ExternalLink size={12} />
        View Leaderboard
      </a>
    </div>
  );
}
