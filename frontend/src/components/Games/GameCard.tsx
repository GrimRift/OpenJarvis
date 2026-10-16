/**
 * What a game turn leaves in the conversation: one line that reopens the
 * game panel, in place of the text board. Rebuilt from the persisted tool
 * call, so it survives a reload.
 */

import type { GamePanel } from '../../lib/game-panel';
import { useGamePresenter } from '../../lib/game-presenter';
import { MiniBoard } from './ChessBoard';

function summary(panel: GamePanel): string {
  const name = panel.game === 'chess' ? 'Chess' : panel.game === 'tic-tac-toe' ? 'Tic-tac-toe' : 'Ping pong';
  if (panel.ended) return `${name} · ended`;
  if (panel.over) {
    return `${name} · ${panel.winner === 'user' ? 'you won' : panel.winner === 'sage' ? 'Sage won' : 'draw'}`;
  }
  if (panel.game === 'ping pong') return `${name} · ${panel.user_score}–${panel.sage_score}`;
  return `${name} · ${panel.turn === 'user' ? 'your move' : "Sage's move"}`;
}

export function GameCard({ panel }: { panel: GamePanel }) {
  const open = useGamePresenter((s) => s.open);
  return (
    <button
      type="button"
      onClick={() => open(panel)}
      className="my-3 flex items-center gap-3 px-3 py-2 text-left cursor-pointer"
      style={{
        border: '1px solid rgba(34,211,238,0.28)',
        borderRadius: 'var(--radius-lg)',
        background: 'rgba(34,211,238,0.07)',
        maxWidth: 420,
        width: '100%',
        color: '#cffafe',
      }}
      aria-label="Open the game panel"
    >
      {panel.game === 'chess' ? (
        <MiniBoard fen={panel.fen} />
      ) : (
        <span
          aria-hidden
          style={{
            width: 56,
            height: 56,
            flex: 'none',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            borderRadius: 8,
            background: '#0e1d2b',
            border: '1px solid rgba(34,211,238,0.25)',
            fontWeight: 700,
            color: '#22d3ee',
          }}
        >
          {panel.game === 'tic-tac-toe' ? 'XO' : `${panel.user_score}–${panel.sage_score}`}
        </span>
      )}
      <div className="min-w-0 flex-1">
        <div className="text-sm font-semibold">{summary(panel)}</div>
        <div className="text-[11.5px] truncate" style={{ color: 'rgba(125,211,252,0.7)' }}>
          {panel.line.split('```')[0].trim() || 'Open the board'}
        </div>
      </div>
      <span className="text-xs whitespace-nowrap" style={{ color: 'var(--color-accent)' }}>
        Open
      </span>
    </button>
  );
}
