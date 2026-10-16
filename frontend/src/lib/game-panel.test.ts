import { describe, expect, it } from 'vitest';
import { gameFromToolCall, gameIn, newGameText, piecesFromFen } from './game-panel';

const chess = {
  id: 'g1',
  game: 'chess',
  fen: 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1',
  last: ['e2', 'e4'],
};

describe('game panel data', () => {
  it('is read off a finished play_game call only', () => {
    const call = { tool: 'play_game', status: 'success' as const, metadata: { game_panel: chess } };
    expect(gameFromToolCall(call)?.game).toBe('chess');
    expect(gameFromToolCall({ ...call, status: 'running' })).toBeNull();
    expect(gameFromToolCall({ ...call, tool: 'weather' })).toBeNull();
    expect(gameFromToolCall({ ...call, metadata: {} })).toBeNull();
    // A refused move still shows the board as it stands.
    expect(gameFromToolCall({ ...call, status: 'error' })?.id).toBe('g1');
  });

  it('a reply with two game calls shows the later one', () => {
    const first = { id: 'a', tool: 'play_game', arguments: '{}', status: 'success' as const, metadata: { game_panel: chess } };
    const later = { ...first, id: 'b', metadata: { game_panel: { ...chess, id: 'g2' } } };
    expect(gameIn([first, later])?.id).toBe('g2');
    expect(gameIn(undefined)).toBeNull();
  });

  it('reads a FEN, rank 8 first', () => {
    const board = piecesFromFen(chess.fen);
    expect(board[0][4]).toEqual({ code: 'k', white: false });
    expect(board[4][4]).toEqual({ code: 'P', white: true }); // e4
    expect(board[6][4]).toBeNull(); // e2 is empty
  });

  it('writes the new-game message the server reads without a model', () => {
    expect(newGameText('chess', 'black')).toBe('New game of chess as black');
    expect(newGameText('ping pong', '', 'unlimited')).toBe('New game of ping pong, unlimited');
    expect(newGameText('ping pong', '', 'eleven')).toBe('New game of ping pong, first to 11');
  });
});
