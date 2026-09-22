/**
 * The renderer is exercised against a stub canvas.
 *
 * Every earlier defect in this orb was a runtime one the type checker could
 * not see: a value read before it was assigned (NaN through every
 * coordinate), a cull that removed the whole web instead of its faintest
 * fifth, a link reach that tripled the line count as the body contracted.
 * None of them throw. So this runs the real draw and checks the numbers it
 * hands the canvas.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { createPlexusState, drawPlexus } from './orb-plexus';
import type { OrbState } from './orb-state';

interface Recorded {
  strokes: number;
  fills: number;
  lineSegments: number;
  alphas: number[];
  coords: number[];
}

function stubContext(record: Recorded) {
  const ctx: Record<string, unknown> = {
    canvas: null,
    globalAlpha: 1,
    globalCompositeOperation: 'source-over',
    fillStyle: '#000',
    strokeStyle: '#000',
    lineWidth: 1,
    filter: 'none',
    imageSmoothingEnabled: true,
    save: () => {},
    restore: () => {},
    clearRect: () => {},
    beginPath: () => {},
    arc: () => {},
    moveTo: (x: number, y: number) => { record.coords.push(x, y); },
    lineTo: (x: number, y: number) => { record.coords.push(x, y); record.lineSegments++; },
    stroke: () => { record.strokes++; record.alphas.push(ctx.globalAlpha as number); },
    fill: () => {},
    fillRect: (x: number, y: number) => {
      record.fills++;
      record.coords.push(x, y);
      record.alphas.push(ctx.globalAlpha as number);
    },
    drawImage: () => {},
    createRadialGradient: () => ({ addColorStop: () => {} }),
  };
  return ctx as unknown as CanvasRenderingContext2D;
}

const globals = globalThis as unknown as { document?: unknown };
let hadDocument = false;
let previousDocument: unknown;

// One record, mutated in place. The renderer draws to offscreen surfaces it
// creates itself and caches by size, so the contexts outlive any one test --
// replacing the object would leave them writing into a discarded one.
const shared: Recorded = { strokes: 0, fills: 0, lineSegments: 0, alphas: [], coords: [] };
function resetRecord() {
  shared.strokes = 0;
  shared.fills = 0;
  shared.lineSegments = 0;
  shared.alphas.length = 0;
  shared.coords.length = 0;
}

beforeEach(() => {
  hadDocument = 'document' in globals;
  previousDocument = globals.document;
  globals.document = {
    createElement: () => ({ width: 0, height: 0, getContext: () => stubContext(shared) }),
  };
});

afterEach(() => {
  if (hadDocument) globals.document = previousDocument;
  else delete globals.document;
});

function run(state: OrbState, frames: number, speech = 0) {
  resetRecord();
  const target = stubContext(shared);
  const canvas = { width: 394, height: 394 } as HTMLCanvasElement;
  const S = createPlexusState();
  for (let f = 0; f < frames; f++) {
    drawPlexus(target, canvas, S, state, f, 1, speech);
  }
  return { record: shared, S };
}

describe('the renderer runs', () => {
  it('draws a web and a body in every state without throwing', () => {
    for (const state of ['idle', 'away', 'listening', 'speaking'] as OrbState[]) {
      const { S } = run(state, 12);
      expect(S.pairCount).toBeGreaterThan(500);
      expect(Number.isFinite(S.radius)).toBe(true);
      expect(Number.isFinite(S.spinY)).toBe(true);
    }
  });

  it('never hands the canvas a NaN coordinate or alpha', () => {
    // A value used before assignment put NaN through every coordinate once,
    // and the page simply rendered nothing.
    for (const state of ['idle', 'speaking'] as OrbState[]) {
      const { record } = run(state, 8, state === 'speaking' ? 0.7 : 0);
      expect(record.coords.length).toBeGreaterThan(0);
      expect(record.coords.every((v) => Number.isFinite(v))).toBe(true);
      expect(record.alphas.every((v) => Number.isFinite(v) && v >= 0 && v <= 1)).toBe(true);
    }
  });
});

describe('the web survives the dim states', () => {
  it('keeps most of its lines when standing by', () => {
    // The cull is a fraction of the current brightness, not a flat number.
    // Flat, it stopped removing the faintest fifth in dim states and
    // removed nearly everything: 334 lines of 7,000 survived.
    const bright = run('listening', 30).record.lineSegments;
    const dim = run('away', 30).record.lineSegments;
    expect(dim).toBeGreaterThan(bright * 0.5);
  });
});

describe('the body does not densify as it shrinks', () => {
  it('draws a comparable number of lines whatever the state radius', () => {
    // Link reach is scaled by the orb's own size. Fixed in object space, a
    // contracted idle orb drew 20,780 lines against listening's 7,400 --
    // densest and slowest exactly where it should be calmest.
    const small = run('idle', 120).record.lineSegments;
    const large = run('listening', 120).record.lineSegments;
    expect(small).toBeLessThan(large * 1.6);
  });
});
