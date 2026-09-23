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
import { PLEXUS_HEARTBEAT, PLEXUS_LOOK, PLEXUS_RIPPLE, createPlexusState, drawPlexus, startRipple } from './orb-plexus';
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

describe('a syllable swells rather than switching on', () => {
  it('takes several frames to reach the level it was given', () => {
    // Setting the level outright is a blink: a switch being thrown, not a
    // voice. The target jumps and the light walks toward it.
    const target = stubContext(shared);
    const canvas = { width: 394, height: 394 } as HTMLCanvasElement;
    const S = createPlexusState();
    for (let f = 0; f < 20; f++) drawPlexus(target, canvas, S, 'speaking', f, 1, 0);
    const before = Math.max(...S.lobes);

    // One loud syllable: a rise from silence.
    drawPlexus(target, canvas, S, 'speaking', 20, 1, 0.9);
    const firstFrame = Math.max(...S.lobes);
    const aimedAt = Math.max(...S.lobeTargets);

    // The target is already up; the light is not there yet.
    // The target is already up; the light is not there yet.
    expect(aimedAt).toBeGreaterThan(before + 0.2);
    expect(firstFrame).toBeLessThan(before + 0.05);

    // Over the next frames it swells toward it and falls back with it, so
    // the peak arrives later than the frame the syllable landed on.
    let peak = firstFrame;
    for (let f = 21; f < 32; f++) {
      drawPlexus(target, canvas, S, 'speaking', f, 1, 0.9);
      peak = Math.max(peak, Math.max(...S.lobes));
    }
    expect(peak).toBeGreaterThan(before + 0.2);
    expect(firstFrame).toBeLessThan(peak - 0.05);
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

describe("speaking's exposure steers itself", () => {
  it('opens up in a pause and eases back under loud speech, within bounds', () => {
    // A pause once fell below standing by, and a harsh sustained voice
    // clipped a fifth of the disc into solid slabs; exposure now steers the
    // light the web draws toward a set amount instead of following a rule.
    const settle = (speech: (f: number) => number) => {
      resetRecord();
      const target = stubContext(shared);
      const canvas = { width: 394, height: 394 } as HTMLCanvasElement;
      const S = createPlexusState();
      for (let f = 0; f < 240; f++) drawPlexus(target, canvas, S, 'speaking', f, 1, speech(f));
      return S.look.exposure;
    };
    const loud = settle((f) => 0.8 + 0.2 * Math.abs(Math.sin(f * 0.4)));
    const pause = settle(() => 0);
    const base = PLEXUS_LOOK.states.speaking.exposure;
    const [lo, hi] = PLEXUS_LOOK.speakingRange;
    expect(pause).toBeGreaterThan(loud);
    for (const e of [loud, pause]) {
      expect(e).toBeGreaterThanOrEqual(base * lo - 1e-6);
      expect(e).toBeLessThanOrEqual(base * hi + 1e-6);
    }
  });
});

describe('the spikes setting', () => {
  it('throws no thorns when they are turned off', () => {
    const run = (spikes: boolean) => {
      resetRecord();
      const target = stubContext(shared);
      const canvas = { width: 394, height: 394 } as HTMLCanvasElement;
      const S = createPlexusState();
      S.spikes = spikes;
      let furthest = 0;
      for (let f = 0; f < 240; f++) {
        drawPlexus(target, canvas, S, 'speaking', f, 1, (f % 14) < 2 ? 0.9 : 0.1);
        furthest = Math.max(furthest, ...S.particles.map((p) => p.out));
      }
      return furthest;
    };
    expect(run(true)).toBeGreaterThan(0.02);
    expect(run(false)).toBeLessThan(1e-6);
  });
});

function frames(state: 'idle' | 'listening', n: number, each?: (S: ReturnType<typeof createPlexusState>, f: number) => void) {
  resetRecord();
  const target = stubContext(shared);
  const canvas = { width: 394, height: 394 } as HTMLCanvasElement;
  const S = createPlexusState();
  for (let f = 0; f < n; f++) {
    each?.(S, f);
    drawPlexus(target, canvas, S, state, f, 1, 0);
  }
  return S;
}

describe('the heartbeat', () => {
  it('beats in standing by, then waits 20-40 s for the next', () => {
    let started = -1;
    const S = frames('idle', PLEXUS_HEARTBEAT.length + 20, (s, f) => {
      if (f === 0) s.beatIn = 1;
      if (s.beatAt === 0 && started < 0) started = f;
    });
    expect(started).toBeGreaterThanOrEqual(0);
    // Crossed and gone, with the next one a quiet while away.
    expect(S.beatAt).toBe(-1);
    expect(S.beatIn).toBeGreaterThanOrEqual(PLEXUS_HEARTBEAT.every[0] - 30);
    expect(S.beatIn).toBeLessThanOrEqual(PLEXUS_HEARTBEAT.every[1]);
  });

  it('never starts one outside standing by', () => {
    let started = false;
    frames('listening', 120, (S, f) => {
      if (f === 0) S.beatIn = 1;
      if (S.beatAt >= 0) started = true;
    });
    expect(started).toBe(false);
  });
});

describe('the wake ripple', () => {
  it('runs once and is gone', () => {
    const seen: number[] = [];
    const S = frames('listening', PLEXUS_RIPPLE.length + 20, (s, f) => {
      if (f === 5) startRipple(s);
      seen.push(s.rippleAt);
    });
    expect(seen.some((v) => v > 0)).toBe(true);
    expect(S.rippleAt).toBe(-1);
  });
});

describe('depth', () => {
  it('turns the inner webs against the surface', () => {
    // The layers slide past each other: an inner node's home direction
    // turns while the shell's stays put.
    const S = createPlexusState();
    const shell = S.particles.find((p) => p.rigid)!;
    const inner = S.particles.find((p) => p.node && !p.rigid)!;
    const before = [shell.hx, shell.hz, inner.hx, inner.hz];
    resetRecord();
    const target = stubContext(shared);
    const canvas = { width: 394, height: 394 } as HTMLCanvasElement;
    for (let f = 0; f < 120; f++) drawPlexus(target, canvas, S, 'idle', f, 1, 0);
    expect(shell.hx).toBe(before[0]);
    expect(shell.hz).toBe(before[1]);
    expect(Math.hypot(inner.hx - before[2], inner.hz - before[3])).toBeGreaterThan(0.01);
  });
});
