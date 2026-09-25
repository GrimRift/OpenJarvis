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

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PLEXUS_HEARTBEAT, PLEXUS_LISTEN_PULSE, PLEXUS_SPARKS, PLEXUS_THORNS, PLEXUS_LOOK, PLEXUS_PATCHES, PLEXUS_RIPPLE, PLEXUS_SUSTAIN, createPlexusState, drawPlexus, startRipple } from './orb-plexus';
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

  it('throws them on sharp onsets only', () => {
    // Soft syllables, each a rise under the threshold: the body reaches no
    // further than with thorns switched off. Sharp ones throw them.
    const run = (spikes: boolean, high: number) => {
      resetRecord();
      const target = stubContext(shared);
      const canvas = { width: 394, height: 394 } as HTMLCanvasElement;
      const S = createPlexusState();
      S.spikes = spikes;
      let thrown = 0;
      for (let f = 0; f < 240; f++) {
        drawPlexus(target, canvas, S, 'speaking', f, 1, f < 20 ? 0.1 : (f % 14) < 7 ? high : 0.1);
        thrown += S.particles.filter((p) => p.out > 0.05).length;
      }
      return thrown;
    };
    expect(run(true, 0.22)).toBe(run(false, 0.22));
    expect(run(true, 0.6)).toBeGreaterThan(run(false, 0.6));
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

describe('the inside of the orb', () => {
  it('keeps every candidate pair, the inner webs included', () => {
    // The list once filled at 32,000 with about 70,000 wanted, surface
    // first: the inner webs had next to no lines among themselves.
    const { S } = run('listening', 12);
    expect(S.pairCount).toBeLessThan(S.pairs.length >> 1);
    let inner = 0;
    for (let i = 0; i < S.pairCount; i++) {
      const a = S.particles[S.pairs[i * 2]];
      const c = S.particles[S.pairs[i * 2 + 1]];
      if (!a.rigid && !c.rigid) inner++;
    }
    expect(inner).toBeGreaterThan(3000);
  });
});

describe("listening's pulse", () => {
  it('rings again and again while listening, and not otherwise', () => {
    const period = PLEXUS_LISTEN_PULSE.length + PLEXUS_LISTEN_PULSE.rest;
    let starts = 0;
    let last = Infinity;
    frames('listening', period * 4, (s) => {
      const phase = s.listenAt >= 0 ? s.listenAt % period : Infinity;
      if (phase < last) starts++;
      last = phase;
    });
    expect(starts).toBeGreaterThanOrEqual(3);
    expect(period / 60).toBeGreaterThan(PLEXUS_RIPPLE.length / 60);
    const idle = frames('idle', period * 2, () => {});
    expect(idle.listenGlow).toBe(0);
  });
});

describe('a held sound', () => {
  function speak(level: (f: number) => number, n: number) {
    resetRecord();
    const target = stubContext(shared);
    const canvas = { width: 394, height: 394 } as HTMLCanvasElement;
    const S = createPlexusState();
    const lit: number[] = [];
    for (let f = 0; f < n; f++) {
      drawPlexus(target, canvas, S, 'speaking', f, 1, level(f));
      lit.push(S.lobes[S.lastHit]);
    }
    return { S, lit };
  }

  it('swells the patch it lit instead of letting it go out', () => {
    // Silence, then one long vowel. Its patch is held above anything its
    // own slow drift reaches (base + swing), which is where it would sit
    // without the swell.
    const { S, lit } = speak((f) => (f < 80 ? 0 : 0.7), 200);
    const { base, swing } = PLEXUS_PATCHES.speaking;
    const want = base + PLEXUS_SUSTAIN.lift * 0.7 * 0.9;
    expect(want).toBeGreaterThan(base + swing);
    expect(S.lobeTargets[S.lastHit]).toBeGreaterThanOrEqual(want);
    expect(lit[199]).toBeGreaterThan(base + swing);
  });

  it('leaves ordinary syllables to their onsets', () => {
    // Syllables every 12 frames: none is held long enough to count.
    const { S } = speak((f) => (f < 80 ? 0 : f % 12 < 6 ? 0.7 : 0.1), 200);
    expect(S.holdFrames).toBeLessThan(PLEXUS_SUSTAIN.after);
  });
});

describe('speaking lights', () => {
  function speakState(level: (f: number) => number, n: number, each?: (S: ReturnType<typeof createPlexusState>, f: number) => void) {
    resetRecord();
    const target = stubContext(shared);
    const canvas = { width: 394, height: 394 } as HTMLCanvasElement;
    const S = createPlexusState();
    for (let f = 0; f < n; f++) {
      drawPlexus(target, canvas, S, 'speaking', f, 1, level(f));
      each?.(S, f);
    }
    return S;
  }

  it('sparks on a sharp onset, rising and falling rather than popping', () => {
    // The lit patch is random and now and then mostly round the back, with
    // no front node to spark; so this is judged over several bodies.
    const end = 90 + PLEXUS_SPARKS.length + 2;
    let sparked = 0;
    let first = 0;
    let top = 0;
    let after = 0;
    for (let body = 0; body < 5; body++) {
      speakState((f) => (f < 90 ? 0 : 0.8), end + 1, (S, f) => {
        const sparks = S.particles.filter((p) => p.spark > 0);
        const strength = Math.max(0, ...sparks.map((p) => Math.sin(Math.PI * p.spark)));
        if (f === 90) {
          if (sparks.length >= PLEXUS_SPARKS.count[0]) sparked++;
          first = Math.max(first, strength);
        }
        top = Math.max(top, strength);
        if (f === end) after += sparks.length;
      });
    }
    expect(sparked).toBeGreaterThanOrEqual(3);
    // A glitch is full strength in one frame; a glint builds.
    expect(first).toBeLessThan(0.4);
    expect(top).toBeGreaterThan(0.9);
    expect(after).toBe(0);
  });

  it('does not spark on a soft onset', () => {
    let lit = 0;
    speakState((f) => (f < 90 ? 0 : 0.12), 110, (S) => {
      lit = Math.max(lit, S.particles.filter((p) => p.spark > 0).length);
    });
    expect(lit).toBe(0);
  });
});

describe('the thorns in loud, fast speech', () => {
  // 24 September: thorns fired from every side at once, "too overwhelming".
  // Every patch a syllable lit stayed lit through fast speech, and each
  // threw. Only the few the latest syllables lit may throw now.
  it('rise from a few regions at a time, not all sides', () => {
    // Seeded: which patches a syllable lights is random, and the worst
    // moment of a run is what this measures.
    let seed = 12345;
    const random = vi.spyOn(Math, 'random').mockImplementation(() => {
      seed = (seed * 1103515245 + 12345) % 2147483648;
      return seed / 2147483648;
    });
    resetRecord();
    const target = stubContext(shared);
    const canvas = { width: 394, height: 394 } as HTMLCanvasElement;
    const S = createPlexusState();
    S.spikes = true;
    let most = 0;
    let thrownFrames = 0;
    // Settled into speaking first: this is about speech, not the morph into
    // it, whose length changes which random draws land where.
    for (let f = -120; f < 0; f++) drawPlexus(target, canvas, S, 'speaking', f, 1, 0);
    for (let f = 0; f < 600; f++) {
      // A sharp, loud syllable every 8 frames: fast, emphatic speech.
      drawPlexus(target, canvas, S, 'speaking', f, 1, (f % 8) < 3 ? 1 : 0.1);
      const regions = new Set(
        S.particles
          .filter((p) => p.out > 0.05)
          .map((p) => (p.lobeW >= 0.5 ? p.lobeA : p.lobeB)),
      );
      if (regions.size > 0) thrownFrames++;
      most = Math.max(most, regions.size);
    }
    random.mockRestore();
    expect(thrownFrames).toBeGreaterThan(0); // still lively
    expect(most).toBeLessThanOrEqual(PLEXUS_THORNS.regions + 2);
  });
});

describe('how the speaking orb follows a smooth voice', () => {
  // 25 September: Frieren on Turbo runs her words together, and her orb
  // held one size; its motion is per voice now (audio-level.ts OrbShaping).
  const canvas = { width: 394, height: 394 } as HTMLCanvasElement;
  const run = (motion?: { release: number; kick: number }) => {
    resetRecord();
    const target = stubContext(shared);
    const S = createPlexusState();
    for (let f = 0; f < 90; f++) drawPlexus(target, canvas, S, 'speaking', f, 1, 0.9, motion);
    const held = S.voiceEnv;
    // A short dip between two words, 100 ms.
    for (let f = 90; f < 96; f++) drawPlexus(target, canvas, S, 'speaking', f, 1, 0.3, motion);
    const dipped = S.voiceEnv;
    drawPlexus(target, canvas, S, 'speaking', 96, 1, 0.9, motion);
    return { drawIn: held - dipped, pulse: S.pulse };
  };

  it('draws in over a short dip and swells harder on the next word', () => {
    const neutral = run();
    const frieren = run({ release: 0.12, kick: 1.6 });
    expect(frieren.drawIn).toBeGreaterThan(neutral.drawIn * 2.5);
    expect(frieren.pulse).toBeGreaterThan(neutral.pulse * 1.5);
  });

  it('leaves Nano Jarvis exactly as it was when nothing is given', () => {
    expect(run({ release: 0.035, kick: 1 })).toEqual(run());
  });
});
