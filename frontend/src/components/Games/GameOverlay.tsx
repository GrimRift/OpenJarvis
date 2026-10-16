/**
 * The game panel over the app (16 October). The user's picks: the panel,
 * its side column and everything round the board in mockup A ("dark glass +
 * cyan"); the chess board and pieces in mockup C (`ChessBoard`).
 *
 * Every button is a chat message ("Undo", "Play hard", "New game of chess as
 * black"), answered by play_game without a model round, so the chat and the
 * voice follow the panel. Flip is the panel's own. It stays open until the
 * user closes it (Close, Esc, "close it"); a click outside does nothing, as
 * a stray click mid-drag must not lose the board.
 */

import { useEffect, useState } from 'react';
import { useAppStore } from '../../lib/store';
import { newGameText, type GamePanel, type PingPongPanel } from '../../lib/game-panel';
import { sayLine, sendToChat, useGamePresenter } from '../../lib/game-presenter';
import { ChessBoard } from './ChessBoard';
import { PingPongCourt } from './PingPongCourt';
import { TicTacToeBoard } from './TicTacToeBoard';

const CYAN = '#22d3ee';
const TEXT = '#e0f2fe';
const SOFT = 'rgba(125,211,252,0.6)';

const STYLE = `
.gm-btn { padding: 7px 13px; border-radius: 10px; font-size: 12px; font-weight: 600; cursor: pointer;
  border: 1px solid rgba(34,211,238,0.35); background: rgba(34,211,238,0.08); color: #cffafe; }
.gm-btn:hover:not(:disabled) { background: rgba(34,211,238,0.16); }
.gm-btn:disabled { opacity: 0.4; cursor: default; }
.gm-pill { padding: 4px 11px; border-radius: 999px; font-size: 12px; cursor: pointer;
  border: 1px solid rgba(34,211,238,0.25); background: transparent; color: #a5f3fc; }
.gm-pill.on { background: ${CYAN}; color: #04121d; font-weight: 700; border-color: ${CYAN}; }
.gm-pill:disabled { opacity: 0.45; cursor: default; }
.gm-k { font-size: 10px; text-transform: uppercase; letter-spacing: .12em; margin: 14px 0 6px; color: ${SOFT}; }
.gm-row { display: flex; gap: 8px; flex-wrap: wrap; }
`;

const TITLES: Record<string, string> = { chess: 'Chess', 'tic-tac-toe': 'Tic-tac-toe', 'ping pong': 'Ping pong' };

function status(panel: GamePanel, busy: boolean): { text: string; sub: string; tone: 'cyan' | 'red' | 'plain' } {
  if (panel.ended) return { text: 'Game ended.', sub: '', tone: 'plain' };
  if (panel.over) {
    const text =
      panel.winner === 'user' ? 'You win!' : panel.winner === 'sage' ? 'Sage wins.' : 'Draw.';
    return { text, sub: panel.detail, tone: panel.winner === 'user' ? 'cyan' : 'plain' };
  }
  if (panel.game === 'ping pong') {
    if (panel.paused) return { text: 'Paused.', sub: 'Say "resume" or press Resume.', tone: 'plain' };
    return { text: panel.server === 'user' ? 'Your serve.' : "Sage's serve.", sub: '', tone: 'cyan' };
  }
  if (panel.turn === 'sage' || busy) return { text: 'Sage is thinking…', sub: '', tone: 'plain' };
  if (panel.note) {
    return {
      text: `${panel.note} Your move.`,
      sub: panel.game === 'chess' && panel.check ? `Your king on ${panel.check} is attacked.` : '',
      tone: 'red',
    };
  }
  return { text: 'Your move.', sub: '', tone: 'cyan' };
}

export function GameOverlay({ panel, onClose }: { panel: GamePanel; onClose: () => void }) {
  const busy = useAppStore((s) => s.streamState.isStreaming);
  const update = useGamePresenter((s) => s.update);
  const [flipped, setFlipped] = useState(panel.game === 'chess' && panel.user === 'black');
  const [pongPaused, setPongPaused] = useState(false);
  const [pongLine, setPongLine] = useState('');

  // A new game sets the board's way round from the user's colour.
  useEffect(() => {
    setFlipped(panel.game === 'chess' && panel.user === 'black');
    setPongPaused(false);
    setPongLine('');
  }, [panel.id, panel.game, panel.user]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const send = (text: string) => !busy && sendToChat(text);
  const live = !panel.over && !panel.ended;
  const state = status(panel, busy);
  const line = panel.game === 'ping pong' ? pongLine || panel.line : panel.line;
  const sides =
    panel.game === 'chess'
      ? ['White', 'Black', 'Random']
      : panel.game === 'tic-tac-toe'
        ? ['X', 'O']
        : [];

  const statusStyle =
    state.tone === 'red'
      ? { background: 'rgba(255,107,107,0.10)', border: '1px solid rgba(255,107,107,0.45)', color: '#ffb4b4' }
      : state.tone === 'cyan'
        ? { background: 'rgba(34,211,238,0.08)', border: '1px solid rgba(34,211,238,0.35)', color: '#cffafe' }
        : { background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.12)', color: TEXT };

  const pong = panel.game === 'ping pong' ? (panel as PingPongPanel) : null;

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 80 }} role="presentation">
      <style>{STYLE}</style>
      <div style={{ position: 'absolute', inset: 0, background: 'rgba(10, 10, 11, 0.62)', backdropFilter: 'blur(2px)' }} />
      <div
        style={{
          position: 'absolute',
          inset: 0,
          boxSizing: 'border-box',
          padding: '56px 24px 28px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          overflow: 'auto',
        }}
        role="dialog"
        aria-modal="true"
        aria-label={`${TITLES[panel.game] ?? 'Game'} with Sage`}
      >
        <div
          style={{
            display: 'flex',
            gap: 26,
            flexWrap: 'wrap',
            alignItems: 'flex-start',
            padding: 22,
            borderRadius: 18,
            // A black panel (as in mockups B and C) with A's cyan outline:
            // the user's change, 16 October.
            background: '#111113',
            border: '1px solid rgba(34,211,238,0.32)',
            boxShadow: '0 0 50px rgba(34,211,238,0.10), 0 24px 70px rgba(0,0,0,0.6)',
            color: TEXT,
            maxWidth: '100%',
          }}
        >
          {panel.game === 'chess' && (
            <ChessBoard panel={panel} flipped={flipped} busy={busy} onMove={send} />
          )}
          {panel.game === 'tic-tac-toe' && <TicTacToeBoard panel={panel} busy={busy} onMove={send} />}
          {pong && (
            <PingPongCourt
              key={pong.id}
              panel={pong}
              paused={pongPaused}
              onPanel={update}
              onLine={(said) => {
                setPongLine(said);
                sayLine(said);
              }}
            />
          )}

          <div style={{ width: 260, flex: 'none' }}>
            <h2 style={{ margin: 0, fontSize: 18, letterSpacing: '.02em' }}>{TITLES[panel.game] ?? panel.game}</h2>
            <div style={{ fontSize: 12, marginTop: 2, color: SOFT }}>
              {pong
                ? `${pong.mode === 'eleven' ? 'First to 11, win by 2' : 'Unlimited'} · ${panel.difficulty}`
                : `You: ${cap(panel.user)} · Sage: ${cap(panel.sage)} · ${panel.difficulty}`}
            </div>

            {pong && (
              <div style={{ display: 'flex', gap: 18, alignItems: 'baseline', margin: '16px 0 0' }}>
                <span style={{ fontSize: 34, fontWeight: 700, color: '#e8fbff', fontVariantNumeric: 'tabular-nums' }}>
                  {pong.user_score}
                  <span style={{ color: SOFT, fontSize: 20 }}> – </span>
                  {pong.sage_score}
                </span>
                <span style={{ fontSize: 12, color: SOFT }}>you – Sage</span>
              </div>
            )}

            <div style={{ margin: '16px 0 10px', padding: '12px 14px', borderRadius: 12, fontWeight: 600, ...statusStyle }}>
              {state.text}
              {state.sub && <small style={{ display: 'block', fontWeight: 400, marginTop: 3, opacity: 0.85 }}>{state.sub}</small>}
            </div>
            {line && (
              <p style={{ fontSize: 13, margin: '0 0 14px' }}>
                Sage: <span style={{ color: '#6bd6ec' }}>“{firstLine(line)}”</span>
              </p>
            )}

            <div className="gm-row">
              {panel.game === 'chess' && (
                <button type="button" className="gm-btn" onClick={() => setFlipped((f) => !f)}>
                  ↻ Flip board
                </button>
              )}
              {!pong && (
                <button type="button" className="gm-btn" disabled={busy || !panel.can_undo} onClick={() => send('Undo')}>
                  ↶ Undo
                </button>
              )}
              {pong && live && (
                <button type="button" className="gm-btn" onClick={() => setPongPaused((p) => !p)}>
                  {pongPaused || pong.paused ? 'Resume' : 'Pause'}
                </button>
              )}
              {live && (
                <button type="button" className="gm-btn" disabled={busy} onClick={() => send('End the game')}>
                  End game
                </button>
              )}
            </div>

            {live && (
              <>
                <div className="gm-k">Difficulty</div>
                <div className="gm-row">
                  {panel.levels.map((level) => (
                    <button
                      key={level}
                      type="button"
                      className={`gm-pill${level === panel.difficulty ? ' on' : ''}`}
                      disabled={busy || level === panel.difficulty}
                      onClick={() => send(`Play ${level}`)}
                    >
                      {level}
                    </button>
                  ))}
                </div>
              </>
            )}

            <div className="gm-k">New game</div>
            <div className="gm-row">
              {sides.map((side) => (
                <button
                  key={side}
                  type="button"
                  className="gm-pill"
                  disabled={busy}
                  onClick={() => send(newGameText(panel.game, side.toLowerCase()))}
                >
                  {side === 'X' || side === 'O' ? `As ${side}` : side}
                </button>
              ))}
              {pong && (
                <>
                  <button type="button" className="gm-pill" disabled={busy} onClick={() => send(newGameText('ping pong', '', 'eleven'))}>
                    First to 11
                  </button>
                  <button type="button" className="gm-pill" disabled={busy} onClick={() => send(newGameText('ping pong', '', 'unlimited'))}>
                    Unlimited
                  </button>
                </>
              )}
            </div>

            {pong && (pong.record_rally > 0 || pong.record_score > 0) && (
              <div style={{ fontSize: 12, marginTop: 14, color: SOFT }}>
                Best rally {pong.record_rally}
                {pong.record_score > 0 && ` · best unlimited score ${pong.record_score}`}
                {pong.best_rally > 0 && ` · this game ${pong.best_rally}`}
              </div>
            )}

            <div style={{ fontSize: 11, marginTop: 16, lineHeight: 1.5, color: SOFT }}>
              {panel.game === 'chess' && (
                <>
                  Click a piece then a square, or drag it.
                  <br />
                  Or just say it: “knight to f3”.
                </>
              )}
              {panel.game === 'tic-tac-toe' && <>Click a square, or say its number.</>}
              {pong && (
                <>
                  Mouse moves your paddle; click or Space to serve.
                  <br />
                  Say “pause”, “resume” or “what’s the score”.
                </>
              )}
            </div>
          </div>
        </div>
      </div>

      <button
        type="button"
        aria-label="Close the game"
        onClick={(event) => {
          event.stopPropagation();
          onClose();
        }}
        style={{
          position: 'absolute',
          top: 18,
          right: 20,
          padding: '8px 16px',
          borderRadius: 999,
          border: '1px solid rgba(255,255,255,0.18)',
          background: 'rgba(24,24,27,0.85)',
          color: '#fafafa',
          fontSize: 13,
          fontWeight: 600,
          cursor: 'pointer',
        }}
      >
        Close
      </button>
    </div>
  );
}

function cap(text: string): string {
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}

/** Sage's line without the text board the chat shows under it. */
function firstLine(text: string): string {
  return text.split('```')[0].trim();
}
