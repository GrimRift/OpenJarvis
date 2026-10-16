/**
 * The game panel's data, as it arrives on a `play_game` tool call
 * (`metadata.game_panel`, written by tools/play_game.py `_panel`).
 *
 * The server holds the rules: legal moves, check, the score. The panel only
 * draws them and sends what a click did as an ordinary chat message ("Nc3",
 * "5", "Undo"), which the server answers without a model round
 * (routes._game_reply_stream). So the chat, Sage's voice and the board stay
 * one game, whether the move was clicked, typed or said.
 */

import type { ToolCallInfo } from '../types';

export const GAME_TOOL = 'play_game';

interface GameBase {
  /** One per game: a new game replaces the board, an update moves it. */
  id: string;
  difficulty: string;
  levels: string[];
  /** The user's side ("white", "X", "you") and Sage's. */
  user: string;
  sage: string;
  turn: 'user' | 'sage' | '';
  over: boolean;
  /** "user", "sage" or "" (a draw, or not over). */
  winner: string;
  detail: string;
  /** "Check!" and the like. */
  note: string;
  /** What Sage said about it. */
  line: string;
  can_undo: boolean;
  /** This move took a piece (chess): a clicked move is spoken only then, or at the end. */
  capture?: boolean;
  /** Set when the user ended the game (the board is cleared on the server). */
  ended?: boolean;
}

export interface ChessPanel extends GameBase {
  game: 'chess';
  fen: string;
  /** The last move's two squares, ["c5", "b4"], or []. */
  last: string[];
  /** The king in check ("e1"), or "". */
  check: string;
  /** Where each of the user's pieces may go: {"b1": ["c3", "d2"]}. */
  legal: Record<string, string[]>;
  /** "b1c3" -> "Nc3": what a click sends. */
  san: Record<string, string>;
}

export interface TicTacToePanel extends GameBase {
  game: 'tic-tac-toe';
  cells: string[];
  /** The winning three, [0, 4, 8], or []. */
  win_line: number[];
  last: number | null;
}

export interface PingPongPanel extends GameBase {
  game: 'ping pong';
  mode: 'eleven' | 'unlimited';
  user_score: number;
  sage_score: number;
  server: 'user' | 'sage';
  paused: boolean;
  best_rally: number;
  record_rally: number;
  record_score: number;
}

export type GamePanel = ChessPanel | TicTacToePanel | PingPongPanel;

/** The panel data on a play_game call, or null. */
export function gameFromToolCall(
  call: Pick<ToolCallInfo, 'tool' | 'status' | 'metadata'>,
): GamePanel | null {
  if (call.tool !== GAME_TOOL || call.status === 'running') return null;
  const raw = call.metadata?.game_panel;
  if (!raw || typeof raw !== 'object') return null;
  const panel = raw as Record<string, unknown>;
  if (typeof panel.game !== 'string' || typeof panel.id !== 'string') return null;
  return panel as unknown as GamePanel;
}

/** The last game state a reply carried (for its chat card). */
export function gameIn(toolCalls: ToolCallInfo[] | undefined): GamePanel | null {
  let found: GamePanel | null = null;
  for (const call of toolCalls ?? []) found = gameFromToolCall(call) ?? found;
  return found;
}

export interface Piece {
  /** "K", "q", ... uppercase = White. */
  code: string;
  white: boolean;
}

/** The 8x8 board from a FEN, rank 8 first: board[0][0] is a8. */
export function piecesFromFen(fen: string): (Piece | null)[][] {
  const rows = (fen.split(' ')[0] ?? '').split('/');
  return Array.from({ length: 8 }, (_, r) => {
    const row: (Piece | null)[] = [];
    for (const ch of rows[r] ?? '') {
      if (/\d/.test(ch)) for (let i = 0; i < Number(ch); i++) row.push(null);
      else row.push({ code: ch, white: ch === ch.toUpperCase() });
    }
    while (row.length < 8) row.push(null);
    return row.slice(0, 8);
  });
}

export function squareName(file: number, rank: number): string {
  return `${'abcdefgh'[file]}${rank + 1}`;
}

/** The sentence to put in the chat for a game event from the panel. */
export function newGameText(game: string, side?: string, mode?: string): string {
  const parts = [`New game of ${game}`];
  if (side) parts.push(`as ${side}`);
  let text = parts.join(' ');
  if (mode) text += `, ${mode === 'unlimited' ? 'unlimited' : 'first to 11'}`;
  return text;
}
