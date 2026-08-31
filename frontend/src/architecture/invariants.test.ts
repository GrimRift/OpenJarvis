import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import ts from 'typescript';

/**
 * Structural invariants for the frontend.
 *
 * These assert how the code is wired, not what it returns. Each exists because
 * the same failure shape has cost this project real debugging time: the code
 * was correct, it just was not the code being run.
 *
 * Parsed with the TypeScript compiler rather than matched with a regex, and
 * that is not fussiness. `InputArea.tsx` contains the comment "This used to
 * call synthesizeSpeech directly" — a grep-shaped assertion would trip over
 * the very comment describing the bug it is meant to prevent.
 */

const SRC = join(process.cwd(), 'src');

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      sourceFiles(full, out);
    } else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) {
      out.push(full);
    }
  }
  return out;
}

function parse(file: string): ts.SourceFile {
  return ts.createSourceFile(
    file,
    readFileSync(file, 'utf8'),
    ts.ScriptTarget.Latest,
    true,
    file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
}

/**
 * The nearest *named* enclosing function, as a node so its whole subtree can
 * be searched.
 *
 * Two shapes matter here and both were got wrong first time:
 *
 * 1. `const sendMessage = useCallback(async () => {...})` — the arrow's parent
 *    is the `useCallback` call, not the declaration. Missing that walked
 *    straight past `sendMessage` up to the `InputArea` component, collapsing
 *    every handler in the file into one owner and making the streaming-first
 *    check below pass vacuously: the original Ultra bug lived in this same
 *    component and would not have been caught.
 * 2. Anonymous inline arrows are skipped, because the fallback legitimately
 *    lives inside `speakStreaming(...).then((spoke) => ...)`.
 *
 * Returning the node rather than the name lets a check ask "is the guard
 * anywhere inside this function", which covers a local helper such as
 * `releasePlayback` defined beside the call it guards.
 */
function owningFunction(node: ts.Node): { name: string; node: ts.Node } | null {
  for (let cur = node.parent; cur; cur = cur.parent) {
    if (ts.isFunctionDeclaration(cur) && cur.name) {
      return { name: cur.name.text, node: cur };
    }
    if (ts.isMethodDeclaration(cur) && ts.isIdentifier(cur.name)) {
      return { name: cur.name.text, node: cur };
    }
    if (ts.isArrowFunction(cur) || ts.isFunctionExpression(cur)) {
      const parent = cur.parent;
      if (parent && ts.isVariableDeclaration(parent) && ts.isIdentifier(parent.name)) {
        return { name: parent.name.text, node: cur };
      }
      // useCallback / useMemo and friends.
      if (
        parent &&
        ts.isCallExpression(parent) &&
        parent.parent &&
        ts.isVariableDeclaration(parent.parent) &&
        ts.isIdentifier(parent.parent.name)
      ) {
        return { name: parent.parent.name.text, node: cur };
      }
    }
  }
  return null;
}

/** Does this function's body call `callee` anywhere inside it? */
function subtreeCalls(
  owner: ts.Node,
  predicate: (callee: string, call: ts.CallExpression) => boolean,
): boolean {
  let found = false;
  const visit = (node: ts.Node) => {
    if (found) return;
    if (ts.isCallExpression(node)) {
      const expr = node.expression;
      const callee = ts.isIdentifier(expr)
        ? expr.text
        : ts.isPropertyAccessExpression(expr)
          ? expr.name.text
          : '';
      if (callee && predicate(callee, node)) {
        found = true;
        return;
      }
    }
    ts.forEachChild(node, visit);
  };
  ts.forEachChild(owner, visit);
  return found;
}

interface CallSite {
  file: string;
  owner: string;
  ownerNode: ts.Node;
  callee: string;
  line: number;
  secondArgIsTrue: boolean;
}

/** Every call site in the app source, tagged with its owning function. */
function allCallSites(): CallSite[] {
  const sites: CallSite[] = [];
  for (const file of sourceFiles(SRC)) {
    const sf = parse(file);
    const visit = (node: ts.Node) => {
      if (ts.isCallExpression(node)) {
        const expr = node.expression;
        const callee = ts.isIdentifier(expr)
          ? expr.text
          : ts.isPropertyAccessExpression(expr)
            ? expr.name.text
            : '';
        const owner = callee ? owningFunction(node) : null;
        if (callee && owner) {
          sites.push({
            file: relative(SRC, file).split(sep).join('/'),
            owner: owner.name,
            ownerNode: owner.node,
            callee,
            line: sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1,
            secondArgIsTrue:
              node.arguments.length > 1 &&
              node.arguments[1].kind === ts.SyntaxKind.TrueKeyword,
          });
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(sf);
  }
  return sites;
}

describe('every spoken reply tries the streaming path first', () => {
  /**
   * Flux Ultra replies take `releaseSpeculativeAnswer`, a second send path
   * from `sendMessage`. All the streamed-TTS work landed in the first, so
   * every Ultra reply used the batch endpoint, left a stray player behind, and
   * ignored the mute setting. 38 tests passed; none touched that path.
   *
   * Both paths live in one file, so this is scoped per function on purpose —
   * a file-level check would not have caught the original bug.
   */
  const sites = allCallSites();

  it('no function reaches batch TTS without first trying to stream', () => {
    const offenders = sites
      .filter((c) => c.callee === 'synthesizeSpeech')
      .filter(
        (c) => !subtreeCalls(c.ownerNode, (callee) => callee === 'speakStreaming'),
      )
      .map((c) => `${c.file}::${c.owner} (line ${c.line})`);
    expect(offenders, 'batch TTS without a streaming attempt').toEqual([]);
  });

  it('has something to guard', () => {
    expect(
      sites.some((c) => c.callee === 'synthesizeSpeech'),
      'no synthesizeSpeech call sites — did it move or get renamed?',
    ).toBe(true);
    expect(
      sites.some((c) => c.callee === 'speakStreaming'),
      'no speakStreaming call sites — did it move or get renamed?',
    ).toBe(true);
  });
});

describe('anything that claims audio playback also releases it', () => {
  /**
   * `audioPlaying` strands the orb mid-speech and keeps the wake word
   * suspended when it is never cleared. It is now derived from an owners map
   * in the store, so a claim without a matching release in the same function
   * is the way the old bug comes back.
   */
  const sites = allCallSites();
  const claims = sites.filter(
    (c) => c.callee === 'setAudioPlayback' && c.secondArgIsTrue,
  );

  it('every setAudioPlayback(owner, true) has a release beside it', () => {
    // Searching the owner's whole subtree, not just its direct calls: the
    // release is legitimately wrapped in a local `releasePlayback` helper.
    const offenders = claims
      .filter(
        (c) =>
          !subtreeCalls(
            c.ownerNode,
            (callee, call) =>
              callee === 'setAudioPlayback' &&
              call.arguments.length > 1 &&
              call.arguments[1].kind === ts.SyntaxKind.FalseKeyword,
          ),
      )
      .map((c) => `${c.file}::${c.owner} (line ${c.line})`);
    expect(offenders, 'playback claimed but never released').toEqual([]);
  });

  it('has something to guard', () => {
    expect(
      claims.length,
      'no setAudioPlayback(owner, true) call sites — did it move?',
    ).toBeGreaterThan(0);
  });
});

describe('audioPlaying has a single writer', () => {
  /**
   * It is a derived value: the store computes it from the owners map. Any
   * other assignment is a second source of truth for whether Sage is
   * speaking, which is what the orb and the wake-word gate both read.
   */
  it('is only assigned inside the store', () => {
    const offenders: string[] = [];
    for (const file of sourceFiles(SRC)) {
      const rel = relative(SRC, file).split(sep).join('/');
      if (rel === 'lib/store.ts') continue;
      const sf = parse(file);
      const visit = (node: ts.Node) => {
        if (
          ts.isPropertyAssignment(node) &&
          ts.isIdentifier(node.name) &&
          node.name.text === 'audioPlaying'
        ) {
          offenders.push(
            `${rel}:${sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1}`,
          );
        }
        ts.forEachChild(node, visit);
      };
      visit(sf);
    }
    expect(offenders, 'audioPlaying assigned outside the store').toEqual([]);
  });
});
