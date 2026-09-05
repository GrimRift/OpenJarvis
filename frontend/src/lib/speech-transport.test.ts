import { readFileSync } from 'node:fs';
import ts from 'typescript';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('./api', () => ({ getBase: () => 'http://server:8000', authHeaders: () => ({ Authorization: 'Bearer test-only' }) }));
vi.mock('./useAgentEvents', () => ({ buildWsProtocols: () => ['openjarvis.auth.v1', 'test-protocol'] }));
afterEach(() => vi.unstubAllGlobals());

describe('speech authentication', () => {
  it('wires streaming through the authenticated transport', () => {
    for (const [file, name] of [
      ['src/hooks/useStreamingTts.ts', 'openTtsSocket'],
    ]) {
      const tree = ts.createSourceFile(file, readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true);
      let found = false;
      const visit = (node: ts.Node) => {
        if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === name) found = true;
        ts.forEachChild(node, visit);
      };
      visit(tree);
      expect(found, file).toBe(true);
    }
  });

  it('uses configured server and websocket auth', async () => {
    vi.stubGlobal('window', { location: { origin: 'http://localhost:5173' } });
    const socket = vi.fn(function () {});
    vi.stubGlobal('WebSocket', socket);
    const { openTtsSocket } = await import('./speech-transport');
    openTtsSocket();
    expect(socket).toHaveBeenCalledWith('ws://server:8000/v1/speech/tts-stream', ['openjarvis.auth.v1', 'test-protocol']);
  });

});
