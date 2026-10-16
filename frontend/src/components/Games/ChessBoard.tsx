/**
 * The chess board, in the look the user picked on 16 October (mockup C,
 * "minimal flat slate"): grey squares, flat pieces, an amber underline for
 * the last move, small cyan dots for where the picked piece may go and a red
 * outline round a king in check.
 *
 * Click a piece then a square, or drag it (the user wanted both). The legal
 * squares come from the server; a move is sent as its notation ("Nc3") and
 * shown at once, then the server's board replaces it.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { piecesFromFen, squareName, type ChessPanel, type Piece } from '../../lib/game-panel';

const SQUARE = 64;
const LIGHT = '#3b4048';
const DARK = '#2a2e35';
const COORD = '#6b7280';
const AMBER = '#f5a524';
const CYAN = '#22d3ee';
const RED = '#ff6b6b';

const GLYPHS: Record<string, string> = {
  k: '♚',
  q: '♛',
  r: '♜',
  b: '♝',
  n: '♞',
  // Without the text selector Windows draws the pawn as a colour emoji.
  p: '♟︎',
};

function PieceGlyph({ piece, size = SQUARE }: { piece: Piece; size?: number }) {
  return (
    <span
      aria-hidden
      style={{
        fontFamily: '"Segoe UI Symbol", "Noto Sans Symbols 2", serif',
        fontSize: size * 0.78,
        lineHeight: 1,
        userSelect: 'none',
        color: piece.white ? '#f4f4f5' : '#0b0b0d',
        textShadow: piece.white ? '0 2px 0 rgba(0,0,0,0.45)' : undefined,
        // The outline under the fill: only the rim shows, so Black's pieces
        // read solid black, not grey (16 October, first look in the app).
        WebkitTextStroke: piece.white ? undefined : '3px #b4b4bc',
        paintOrder: piece.white ? undefined : 'stroke fill',
      }}
    >
      {GLYPHS[piece.code.toLowerCase()]}
    </span>
  );
}

const PIECE_NAMES: Record<string, string> = {
  k: 'king',
  q: 'queen',
  r: 'rook',
  b: 'bishop',
  n: 'knight',
  p: 'pawn',
};

interface Props {
  panel: ChessPanel;
  /** Black at the bottom. */
  flipped: boolean;
  /** A chat turn is running: no moves until it ends. */
  busy: boolean;
  /** False if it could not be sent (the board then stays as it was). */
  onMove: (notation: string) => boolean;
}

interface Drag {
  from: string;
  piece: Piece;
  x: number;
  y: number;
  moved: boolean;
}

export function ChessBoard({ panel, flipped, busy, onMove }: Props) {
  const board = useMemo(() => piecesFromFen(panel.fen), [panel.fen]);
  const [selected, setSelected] = useState('');
  const [pending, setPending] = useState<{ from: string; to: string } | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const boardRef = useRef<HTMLDivElement>(null);

  // A new position from the server ends any move shown ahead of it.
  useEffect(() => {
    setPending(null);
    setSelected('');
  }, [panel]);

  const canMove = panel.turn === 'user' && !panel.over && !busy && !pending;
  const userIsWhite = panel.user === 'white';
  const targets = selected && canMove ? panel.legal[selected] ?? [] : [];

  const onBoard = (name: string): Piece | null => {
    const file = name.charCodeAt(0) - 97;
    const rank = Number(name[1]) - 1;
    return board[7 - rank]?.[file] ?? null;
  };
  // The move just sent, shown before the server's board arrives.
  const pieceAt = (name: string): Piece | null => {
    if (pending && name === pending.from) return null;
    if (pending && name === pending.to) return onBoard(pending.from);
    return onBoard(name);
  };

  const isMine = (piece: Piece | null) => !!piece && piece.white === userIsWhite;

  const play = (from: string, to: string) => {
    const notation = panel.san[from + to];
    if (!notation) return false;
    setSelected('');
    if (!onMove(notation)) return false;
    setPending({ from, to });
    return true;
  };

  const squareFromPoint = (clientX: number, clientY: number): string => {
    const rect = boardRef.current?.getBoundingClientRect();
    if (!rect) return '';
    const col = Math.floor(((clientX - rect.left) / rect.width) * 8);
    const row = Math.floor(((clientY - rect.top) / rect.height) * 8);
    if (col < 0 || col > 7 || row < 0 || row > 7) return '';
    const file = flipped ? 7 - col : col;
    const rank = flipped ? row : 7 - row;
    return squareName(file, rank);
  };

  const onPointerDown = (event: React.PointerEvent, name: string) => {
    if (!canMove) return;
    const piece = pieceAt(name);
    if (selected && targets.includes(name)) {
      play(selected, name);
      return;
    }
    if (!isMine(piece) || !piece) {
      setSelected('');
      return;
    }
    setSelected(name);
    (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
    setDrag({ from: name, piece, x: event.clientX, y: event.clientY, moved: false });
  };

  const onPointerMove = (event: React.PointerEvent) => {
    if (!drag) return;
    const moved = drag.moved || Math.hypot(event.clientX - drag.x, event.clientY - drag.y) > 4;
    setDrag({ ...drag, x: event.clientX, y: event.clientY, moved });
  };

  const onPointerUp = (event: React.PointerEvent) => {
    if (!drag) return;
    const was = drag;
    setDrag(null);
    if (!was.moved) return; // a click: the piece stays picked
    const to = squareFromPoint(event.clientX, event.clientY);
    if (to && to !== was.from && (panel.legal[was.from] ?? []).includes(to)) play(was.from, to);
  };

  const rect = boardRef.current?.getBoundingClientRect();
  const squares = [];
  for (let row = 0; row < 8; row++) {
    for (let col = 0; col < 8; col++) {
      const file = flipped ? 7 - col : col;
      const rank = flipped ? row : 7 - row;
      const name = squareName(file, rank);
      const light = (file + rank) % 2 === 1;
      const piece = pieceAt(name);
      const hidden = drag?.moved && drag.from === name;
      const last = pending ? [pending.from, pending.to] : panel.last;
      const shadows: string[] = [];
      if (last.includes(name)) shadows.push(`inset 0 -4px 0 ${AMBER}`, 'inset 0 0 0 999px rgba(245,165,36,0.10)');
      if (name === selected) shadows.push('inset 0 0 0 999px rgba(34,211,238,0.22)');
      if (name === panel.check && !pending) shadows.push(`inset 0 0 0 3px ${RED}`, 'inset 0 0 0 999px rgba(255,107,107,0.18)');
      const target = targets.includes(name);
      squares.push(
        <div
          key={name}
          role="gridcell"
          aria-label={`${name}${piece ? ` ${piece.white ? 'white' : 'black'} ${PIECE_NAMES[piece.code.toLowerCase()]}` : ''}`}
          data-square={name}
          onPointerDown={(event) => onPointerDown(event, name)}
          style={{
            position: 'relative',
            width: SQUARE,
            height: SQUARE,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: light ? LIGHT : DARK,
            boxShadow: shadows.join(', ') || undefined,
            cursor: canMove && (isMine(piece) || target) ? 'pointer' : 'default',
            touchAction: 'none',
          }}
        >
          {col === 0 && (
            <span style={{ position: 'absolute', left: 4, top: 2, fontSize: 10, fontWeight: 700, color: COORD }}>{rank + 1}</span>
          )}
          {row === 7 && (
            <span style={{ position: 'absolute', right: 4, bottom: 2, fontSize: 10, fontWeight: 700, color: COORD }}>
              {'abcdefgh'[file]}
            </span>
          )}
          {piece && !hidden && <PieceGlyph piece={piece} />}
          {target &&
            (piece ? (
              <span style={{ position: 'absolute', inset: 4, borderRadius: '50%', border: `3px solid ${CYAN}`, opacity: 0.75 }} />
            ) : (
              <span style={{ position: 'absolute', width: 12, height: 12, borderRadius: '50%', background: CYAN }} />
            ))}
        </div>,
      );
    }
  }

  return (
    <div
      ref={boardRef}
      role="grid"
      aria-label="Chess board"
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={() => setDrag(null)}
      style={{
        position: 'relative',
        display: 'grid',
        gridTemplateColumns: `repeat(8, ${SQUARE}px)`,
        gridTemplateRows: `repeat(8, ${SQUARE}px)`,
        borderRadius: 10,
        overflow: 'hidden',
        flex: 'none',
      }}
    >
      {squares}
      {drag?.moved && rect && (
        <div
          style={{
            position: 'absolute',
            left: drag.x - rect.left - SQUARE / 2,
            top: drag.y - rect.top - SQUARE / 2,
            width: SQUARE,
            height: SQUARE,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            pointerEvents: 'none',
            zIndex: 5,
            transform: 'scale(1.08)',
          }}
        >
          <PieceGlyph piece={drag.piece} />
        </div>
      )}
    </div>
  );
}

/** A tiny board for the chat card. */
export function MiniBoard({ fen }: { fen: string }) {
  const board = piecesFromFen(fen);
  return (
    <div
      aria-hidden
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(8, 7px)',
        gridTemplateRows: 'repeat(8, 7px)',
        borderRadius: 3,
        overflow: 'hidden',
        flex: 'none',
      }}
    >
      {board.flatMap((row, r) =>
        row.map((piece, f) => (
          <div
            key={`${r}${f}`}
            style={{
              background: piece ? (piece.white ? '#f4f4f5' : '#0b0b0d') : (r + f) % 2 ? DARK : LIGHT,
              boxShadow: piece && !piece.white ? 'inset 0 0 0 1px #b4b4bc' : undefined,
              borderRadius: piece ? 2 : 0,
            }}
          />
        )),
      )}
    </div>
  );
}
