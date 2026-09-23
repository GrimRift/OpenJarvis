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
import { PLEXUS_ENERGY, PLEXUS_THORNS, createPlexusState, drawPlexus, pulseEnvelope } from './orb-plexus';
import type { OrbState } from './orb-state';

interface Recorded {
  /** fillStyle of every fillRect on the visible canvas -- the backdrop. */
  targetFills: string[];
  strokes: number;
  fills: number;
  lineSegments: number;
  alphas: number[];
  coords: number[];
  /** alpha x width per stroked band: what a line actually puts on screen. */
  inks: number[];
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
    stroke: () => {
      record.strokes++;
      record.alphas.push(ctx.globalAlpha as number);
      record.inks.push((ctx.globalAlpha as number) * (ctx.lineWidth as number));
    },
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
const shared: Recorded = { targetFills: [], strokes: 0, fills: 0, lineSegments: 0, alphas: [], coords: [], inks: [] };
function resetRecord() {
  shared.targetFills.length = 0;
  shared.strokes = 0;
  shared.fills = 0;
  shared.lineSegments = 0;
  shared.alphas.length = 0;
  shared.coords.length = 0;
  shared.inks.length = 0;
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
    const small = run('away', 120).record.lineSegments;
    const large = run('listening', 120).record.lineSegments;
    expect(small).toBeLessThan(large * 1.6);
  });
});

describe('the web keeps its weight at any canvas size', () => {
  it('scales the drawn weight of a line with the canvas', () => {
    // Everything scales with the canvas -- the orb, the dots, the bloom --
    // so the lines must too, or the web thins as the orb grows. Held at a
    // fixed width they came out 27% light on the 764px Voice orb, which
    // showed a field of dots with barely a line between them.
    const weight = (w: number) => {
      resetRecord();
      const target = stubContext(shared);
      const canvas = { width: w, height: w } as HTMLCanvasElement;
      const S = createPlexusState();
      for (let f = 0; f < 20; f++) drawPlexus(target, canvas, S, 'listening', f, 1, 0);
      // alpha x width, averaged over the stroked bands: what one line puts
      // on screen per unit of its length.
      return shared.inks.reduce((a, b) => a + b, 0) / shared.inks.length;
    };
    const ratio = weight(764) / weight(473);
    expect(ratio).toBeGreaterThan((764 / 473) * 0.8);
    expect(ratio).toBeLessThan((764 / 473) * 1.2);
  });
});

describe('the orb sits on the page, not on a square', () => {
  it('never paints a background under itself', () => {
    // Everything outside the light must stay transparent. Composed on black
    // the orb was a dark square over the HUD grid; composed on the page's
    // colour it was a dark circle, because the grid and gradient behind it
    // are painted by a fixed backdrop the orb cannot see. So nothing may
    // fill the visible canvas -- the light is only ever added to it.
    resetRecord();
    const target = stubContext(shared);
    const t = target as unknown as { fillRect: (x: number, y: number) => void; fillStyle: string };
    const fillRect = t.fillRect;
    t.fillRect = (x, y) => {
      shared.targetFills.push(String(t.fillStyle));
      fillRect(x, y);
    };
    const canvas = { width: 394, height: 394 } as HTMLCanvasElement;
    const S = createPlexusState();
    for (const state of ['idle', 'speaking'] as OrbState[]) {
      for (let f = 0; f < 4; f++) drawPlexus(target, canvas, S, state, f, 1, state === 'speaking' ? 0.6 : 0);
    }
    expect(shared.targetFills).toEqual([]);
  });
});

/** Speak a sharp syllable every 14 frames, the way a voice does. */
const syllables = (f: number) => ((f % 14) < 2 ? 0.9 : 0.1);

function speak(frames: number, spikes = true) {
  resetRecord();
  const target = stubContext(shared);
  const canvas = { width: 394, height: 394 } as HTMLCanvasElement;
  const S = createPlexusState();
  S.spikes = spikes;
  const log: Array<{ burst: number; out: number; calm: number }> = [];
  for (let f = 0; f < frames; f++) {
    drawPlexus(target, canvas, S, 'speaking', f, 1, syllables(f));
    log.push({ burst: S.burst, out: Math.max(...S.particles.map((p) => p.out)), calm: S.calmFor });
  }
  return { S, log };
}

describe('energy moves inside the sphere while Sage speaks', () => {
  it('pulses rise and retreat smoothly, never snapping on or off', () => {
    // Syllables used to throw a patch to full in a frame and let it fall:
    // hotspots switching on and off. A pulse is a raised cosine each way.
    const total = PLEXUS_ENERGY.rise + PLEXUS_ENERGY.hold + PLEXUS_ENERGY.fall;
    expect(pulseEnvelope(0)).toBe(0);
    expect(pulseEnvelope(total)).toBe(0);
    let worst = 0;
    for (let f = 0; f < total; f += 0.5) worst = Math.max(worst, Math.abs(pulseEnvelope(f + 0.5) - pulseEnvelope(f)));
    // No half-frame step bigger than a smooth rise over the attack needs.
    expect(worst).toBeLessThan((Math.PI / 2 / PLEXUS_ENERGY.rise) * 0.5 + 1e-9);
  });

  it('leaves a calm interval between pulses, however fast the syllables', () => {
    const { log } = speak(600);
    const starts: number[] = [];
    for (let f = 1; f < log.length; f++) if (log[f].burst > 0 && log[f - 1].burst === 0) starts.push(f);
    expect(starts.length).toBeGreaterThan(1);
    const period = PLEXUS_ENERGY.rise + PLEXUS_ENERGY.hold + PLEXUS_ENERGY.fall + PLEXUS_ENERGY.calm;
    for (let i = 1; i < starts.length; i++) {
      expect(starts[i] - starts[i - 1]).toBeGreaterThanOrEqual(period - 1);
    }
  });

  it('throws spikes only during a pulse, and not far', () => {
    const { log } = speak(400);
    const during = log.filter((l) => l.burst > 0.5);
    const calm = log.filter((l) => l.burst === 0);
    expect(during.length).toBeGreaterThan(0);
    expect(Math.max(...during.map((l) => l.out))).toBeGreaterThan(0.02);
    // Between pulses the silhouette is the shell: a circle.
    expect(Math.max(...calm.map((l) => l.out))).toBe(0);
    // Short: the tip never reaches past the configured length.
    expect(Math.max(...log.map((l) => l.out))).toBeLessThanOrEqual(PLEXUS_THORNS.length + 1e-9);
  });

  it('throws no spikes at all when they are turned off', () => {
    const { log } = speak(400, false);
    expect(log.some((l) => l.burst > 0.5)).toBe(true);
    expect(Math.max(...log.map((l) => l.out))).toBe(0);
  });

  it('keeps the fine mesh drawn between pulses', () => {
    // The orb must stay whole when no current is lit: the mesh is drawn by
    // every line, not only by the ones a current is passing through.
    resetRecord();
    const target = stubContext(shared);
    const canvas = { width: 394, height: 394 } as HTMLCanvasElement;
    const S = createPlexusState();
    for (let f = 0; f < 30; f++) drawPlexus(target, canvas, S, 'speaking', f, 1, 0);
    expect(S.currents.every((c) => c.level < 0.5)).toBe(true);
    expect(shared.lineSegments).toBeGreaterThan(1000);
  });
});
