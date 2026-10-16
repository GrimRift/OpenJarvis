/**
 * Tic-tac-toe in the panel's look (mockup A, "dark glass + cyan"): click a
 * square; it is sent as its number ("5"), the way the user says it.
 */

import { useEffect, useState } from 'react';
import type { TicTacToePanel } from '../../lib/game-panel';

const CELL = 112;

interface Props {
  panel: TicTacToePanel;
  busy: boolean;
  onMove: (square: string) => boolean;
}

export function TicTacToeBoard({ panel, busy, onMove }: Props) {
  const [pending, setPending] = useState<number | null>(null);
  useEffect(() => setPending(null), [panel]);
  const canMove = panel.turn === 'user' && !panel.over && !busy && pending === null;

  return (
    <div
      role="grid"
      aria-label="Tic-tac-toe board"
      style={{
        display: 'grid',
        gridTemplateColumns: `repeat(3, ${CELL}px)`,
        gridTemplateRows: `repeat(3, ${CELL}px)`,
        gap: 8,
        flex: 'none',
      }}
    >
      {panel.cells.map((mark, index) => {
        const shown = mark || (pending === index ? panel.user : '');
        const win = panel.win_line.includes(index);
        const last = panel.last === index && !panel.over;
        const open = !shown && canMove;
        return (
          <button
            key={index}
            type="button"
            role="gridcell"
            aria-label={`Square ${index + 1}${shown ? `, ${shown}` : ''}`}
            disabled={!open}
            onClick={() => {
              if (open && onMove(String(index + 1))) setPending(index);
            }}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 54,
              fontWeight: 700,
              borderRadius: 10,
              background: win ? 'rgba(34,211,238,0.22)' : '#0e1d2b',
              border: `1px solid ${last ? 'rgba(34,211,238,0.7)' : 'rgba(34,211,238,0.25)'}`,
              color: shown === 'X' ? '#e8fbff' : '#22d3ee',
              textShadow: shown === 'X' ? '0 0 12px #67e8f9' : '0 0 8px rgba(34,211,238,0.35)',
              cursor: open ? 'pointer' : 'default',
              padding: 0,
            }}
          >
            {shown || (
              <span style={{ fontSize: 13, fontWeight: 500, color: 'rgba(125,211,252,0.3)' }}>{index + 1}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}
