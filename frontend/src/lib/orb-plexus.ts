/**
 * The constellation orb: a particle shell webbed with lines, lit in patches.
 *
 * Built against a reference clip frame by frame rather than by eye, and the
 * numbers below are measurements off it, not taste:
 *
 * - The outline stays a circle because the outer shell is pinned to its
 *   sphere and dense enough that its links trace the limb continuously. The
 *   reference's outline varies by 3.0% of its radius; a freely drifting
 *   shell gave 7.8%, this gives 4.0%.
 * - The middle is not empty space behind a surface. It is further webs at
 *   smaller radii, which is what gives the reference its layered texture and
 *   keeps its centre nearly as bright as its edge (rim/centre 1.45, where a
 *   single shell gives 3.7).
 * - The rim glows because a shell seen edge on stacks far more particles per
 *   pixel, measured at about four times the centre. Nothing draws a halo:
 *   a gradient centred on the orb puts haze in the middle, and one peaking
 *   outside its edge draws a ring around a dark disc.
 * - Brightness is depth and the patch a particle belongs to, never anything
 *   of its own. A per-particle flicker, however subtle, reads as dots
 *   switching on and off.
 *
 * Design after ethanplusai/jarvis (personal, non-commercial licence, (c) 2026
 * Ethan Rogers) and the user's own reference footage. No code is taken from
 * it -- that project is three.js and licensed incompatibly with this one;
 * this is written against Sage's own 2D canvas.
 */

import { approach } from './orb-motion';
import type { OrbState } from './orb-state';

/** Per state: radius, how hard the flow stirs, spin, brightness, wiring. */
export interface PlexusStateConfig {
  r: number;
  flow: number;
  spin: number;
  bright: number;
  links: number;
}

export const PLEXUS_STATES: Record<OrbState, PlexusStateConfig> = {
  // "idle" is the state Sage sits in whenever it has nothing to do and
  // someone is there -- the one the UI labels STANDING BY and the one a user
  // sees nearly all the time. It carries the preview's standing-by settings.
  // The preview's own "idle" button is a different thing: an orb with nobody
  // watching, which here is "away".
  idle: { r: 0.8, flow: 0.55, spin: 0.003, bright: 0.97, links: 0.65 },
  // Nobody at the desk: smaller and dimmer, so sitting down is a visible
  // waking up.
  away: { r: 0.65, flow: 0.55, spin: 0.003, bright: 0.68, links: 0.6 },
  // Only a little larger than standing by, and never as large as a speaking
  // orb at full voice.
  listening: { r: 0.86, flow: 1.0, spin: 0.0042, bright: 1.12, links: 0.8 },
  speaking: { r: 0.97, flow: 1.55, spin: 0.005, bright: 1.45, links: 1.15 },
};

/**
 * How the lit patches behave per state: resting level, how far they drift on
 * their own, how hard a syllable throws one up, how fast it falls back.
 *
 * Speaking's base was 0.62 at first, set low so a syllable had room to rise.
 * But speaking also rests at standing-by size, and the dim-on-contraction
 * rule fires with it -- three multipliers stacked, and a silent speaking orb
 * came out dimmer than standing by. It was the dimmest thing on screen
 * exactly when Sage was mid-sentence.
 */
export const PLEXUS_PATCHES: Record<OrbState, { base: number; swing: number; kick: number; fall: number }> = {
  away: { base: 0.8, swing: 0.16, kick: 0, fall: 0.97 },
  idle: { base: 0.8, swing: 0.16, kick: 0, fall: 0.97 },
  listening: { base: 0.72, swing: 0.22, kick: 0.8, fall: 0.945 },
  // Falls slower than the light can rise, or the target is gone before
  // the patch reaches it and a syllable shows as a 14% nudge.
  speaking: { base: 0.8, swing: 0.2, kick: 1.05, fall: 0.932 },
};

/** How deep the breath goes, per state. An unwatched orb holds still; one
 * that keeps inflating with nobody there reads as restless. Speaking does not
 * breathe at all -- its size is the voice, which is a different motion and
 * fights a cycle running underneath it. */
export const BREATH_DEPTH: Record<OrbState, number> = {
  idle: 0.07,
  away: 0,
  listening: 0.12,
  speaking: 0,
};

/** Measured off the reference: it inhales and exhales on a four-second
 * cycle. In 60Hz frames. */
export const BREATH_PERIOD = 240;

const NODE_LAYERS = [
  { count: 560, rMin: 0.98, rMax: 1.0 },
  { count: 260, rMin: 0.7, rMax: 0.82 },
  { count: 200, rMin: 0.42, rMax: 0.58 },
];
const NODES = 1020;
const DUST = 640;

const LINK_DIST = 0.43;
/** A floor under a pair's mean radius, so the innermost shell still webs
 * densely instead of thinning to nothing and leaving the middle bare. */
const LINK_RMEAN_FLOOR = 0.68;
const LINK_MAX_ALPHA = 0.45;
const LINK_BANDS = 6;
/** Rebuilt on a timer, not every frame: the nodes drift slowly, so the
 * candidate set is stale by almost nothing after ten frames, and the exact
 * distance is still recomputed every frame for the ones on the list. */
const LINK_REBUILD_FRAMES = 10;
const LINK_MARGIN = 1.15;

const PA0 = 0.28;
const PA1 = 0.2;
const FOCAL = 3.1;
const LOBES = 10;
const LOBE_SHARP = 3;
const DUST_TURN = 1;
/**
 * How the finished frame is lit, after the marks are drawn. Per state, and
 * eased between states like everything else.
 *
 * exposure  scales the marks as they are drawn -- every line's and dot's
 *           alpha -- so the squared layers built from them carry it squared.
 * curve     the frame squared, unblurred: contrast. A knot at full
 *           brightness gains the full amount, a mid tone a quarter of it,
 *           the faint wash between the webs almost nothing -- so highlights
 *           climb while the gaps stay dark.
 * white     how far the contrast layer is drained of colour. Saturated cyan
 *           clips at a luminance of 201 however hard it is pushed; the
 *           reference's brightest knots reach 211-246 by burning through to
 *           white. That burn is the electric look on speaking's lines; on a
 *           resting orb it turns the whole body pale.
 * bloom     a wide, weak blur of the whole frame: the halo every line in
 *           the reference carries, and what softens it.
 * glow      a tighter blur of the squared frame: light spilling off the
 *           bright knots only, so it does not fog the gaps.
 * dots      how strongly the particles themselves are drawn. Every junction
 *           dot is a hard point of light; at full strength a resting orb
 *           reads as speckle, where the reference's dots barely show and its
 *           lines carry the picture.
 * haze      a very wide blur of the whole frame: the soft body of light the
 *           reference's web sits inside. It fills the orb, not the gaps
 *           between orbs -- at this radius it stays within the disc.
 * soft      a blur on the frame itself, in pixels at 764 (scaled with the
 *           canvas). At zero every line is a one-pixel hairline and every
 *           dot a hard square, which at full size reads as a sharp wire
 *           model rather than something glowing.
 */
export interface PlexusLook {
  exposure: number;
  curve: number;
  white: number;
  bloom: number;
  glow: number;
  soft: number;
  dots: number;
  haze: number;
}

export const PLEXUS_LOOK = {
  // Scored at 300px inside the orb's disc on the page's background colour
  // (luminance 0-255; sharpness = mean |laplacian| over mean luminance;
  // whiteness = red over green, since saturated cyan has no red), 240
  // frames per state:
  //
  //                 median  p90  p99  sharp  white  colour
  //   reference GIF     50  118  211   0.51   0.20  15,75,98
  //   standing by       52  133  212   0.40   0.18  14,75,97
  //   lab speaking      52  189  245   0.28   0.22  22,99,120
  //   speaking          76  223  254   0.30   0.34  39,119,135
  //   a pause in it     70  207  236   0.35   0.24  27,108,128
  //
  // A screenshot of the version before scored sharpness 0.75 and
  // whiteness 0.61 -- half again as sharp as the reference and three times
  // as white; the one after it (soft 1, haze 0.8) was asked to be less
  // blurry. Standing by now sits on the reference; speaking keeps its
  // white-hot, electric lines.
  states: {
    idle: { exposure: 3.4, curve: 0.45, white: 0.25, bloom: 1.05, glow: 2, soft: 0.45, dots: 0.4, haze: 0.5 },
    away: { exposure: 2.4, curve: 0.45, white: 0.25, bloom: 1.05, glow: 2, soft: 0.45, dots: 0.4, haze: 0.5 },
    listening: { exposure: 2.9, curve: 0.6, white: 0.35, bloom: 1.1, glow: 2, soft: 0.3, dots: 0.55, haze: 0.35 },
    speaking: { exposure: 1.1, curve: 0.9, white: 0.4, bloom: 0.85, glow: 2, soft: 0, dots: 0.8, haze: 0 },
  } as Record<OrbState, PlexusLook>,
  /** Speaking's exposure at full voice; its own exposure above is the one in
   * a pause. The lit patches already brighten the body several times over
   * on a word, so one fixed exposure had to choose: set for the words, a
   * pause mid-sentence scored median 37 against standing by's 53 -- the
   * dimmest thing on screen exactly while Sage was talking; set for the
   * pauses, every word washed the orb out. It follows the voice envelope
   * between the two. */
  speakingVoiced: 0.45,
  bloomRadius: 190,
  glowRadius: 420,
  hazeRadius: 60,
};

/** Per 60Hz frame: a patch reaches most of a syllable's brightness in
 * about four frames. Instant is a blink; this is a voice. */
const LOBE_ATTACK = 0.34;

/** Forces, in units where 1.0 is the orb's own radius. */
const FLOW = 7.2e-5;
const DRIFT = 5.0e-5;
const SPRING = 0.0016;
const DAMP = 0.965;
const TETHER = 1.2e-4;
const SYLLABLE_KICK = 9.0e-4;
const HARD_LIMIT = 1.22;

const RAMP_STOPS: Array<[number, number, number, number]> = [
  [0, 10, 76, 107],
  [0.38, 18, 136, 184],
  [0.68, 56, 224, 247],
  [0.88, 168, 242, 252],
  [1, 255, 255, 255],
];
const RAMP_STEPS = 18;

function buildRamp(): string[] {
  const ramp: string[] = [];
  for (let i = 0; i < RAMP_STEPS; i++) {
    const u = i / (RAMP_STEPS - 1);
    let k = 0;
    while (k < RAMP_STOPS.length - 2 && u > RAMP_STOPS[k + 1][0]) k++;
    const a = RAMP_STOPS[k];
    const b = RAMP_STOPS[k + 1];
    const f = (u - a[0]) / (b[0] - a[0] || 1);
    ramp.push(
      `rgb(${Math.round(a[1] + (b[1] - a[1]) * f)},${Math.round(a[2] + (b[2] - a[2]) * f)},${Math.round(
        a[3] + (b[3] - a[3]) * f,
      )})`,
    );
  }
  return ramp;
}
const RAMP = buildRamp();

interface Node {
  x: number; y: number; z: number;
  vx: number; vy: number; vz: number;
  hx: number; hy: number; hz: number;
  home: number;
  rigid: boolean;
  node: boolean;
  phase: number;
  heat: number;
  size: number;
  px: number; py: number; pd: number; ps: number; pa: number; reg: number;
  lobeA: number; lobeB: number; lobeW: number;
}

/** Everything a state change blends, captured as it was when the change
 * began -- mid-morph too, so a second change starts from what is on screen
 * rather than from either state's settings. */
interface Morph {
  radius: number; flow: number; bright: number; links: number;
  look: PlexusLook; breath: number; spin: number;
}

/**
 * How long a state change takes, in 60Hz frames, and its easing.
 *
 * Every parameter used to chase its new value on its own exponential: the
 * biggest step on the very first frame, then a long crawl, each at its own
 * rate. The size was worse -- the breathing and voice scale switched to the
 * new state's outright, so standing by to speaking dropped the orb 17% in a
 * single frame, and its brightness with it, since brightness follows size.
 * And the tumble set its energy to full at once, quadrupling the spin in a
 * frame. Now one morph carries all of it: it starts gently, moves most in
 * the middle and settles, and every parameter is at the same point of it.
 */
export const MORPH_FRAMES = 66;
export function easeMorph(x: number): number {
  const u = Math.min(1, Math.max(0, x));
  return u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2;
}
const lerp = (a: number, b: number, e: number) => a + (b - a) * e;

export interface PlexusState {
  radius: number; flow: number; bright: number; links: number;
  pulse: number; lastSpeech: number; voiceEnv: number;
  lobes: number[];
  lobeTargets: number[];
  lobeAxes: Array<[number, number, number]>;
  spinX: number; spinY: number; spinZ: number;
  lastState: OrbState;
  /** Where the last state change started from, and how far through the
   * morph to the new state it is (0..1). */
  from: Morph; trans: number;
  lastBreath: number; lastSpin: number;
  z: number; zVel: number; linkAge: number;
  particles: Node[];
  pairs: Int32Array; pairCount: number;
  look: PlexusLook;
}

/** A syllable's onset, not its level: a steady push only inflates the body. */
export function syllableRise(speech: number, lastSpeech: number): number {
  return Math.max(0, speech - lastSpeech);
}

/**
 * Size while speaking, as a fraction of the speaking radius.
 *
 * Runs the whole way from standing-by size up to full, driven by an envelope
 * with a quick attack and a slow release. Through fast speech the envelope
 * never falls between syllables, so the orb holds its size and the lit
 * patches carry the talking; it draws in over a real pause and swells on the
 * first word after one.
 */
export function speakingScale(voiceEnv: number): number {
  const rest = PLEXUS_STATES.idle.r;
  const full = PLEXUS_STATES.speaking.r;
  return (rest + (full - rest) * voiceEnv) / full;
}

export function breathScale(state: OrbState, t: number, voiceEnv: number): number {
  if (state === 'speaking') return speakingScale(voiceEnv);
  const depth = BREATH_DEPTH[state] || 0;
  return 1 - depth / 2 + (depth / 2) * Math.sin(t * ((2 * Math.PI) / BREATH_PERIOD));
}

/**
 * The shortest gap between draws, in milliseconds: every state is drawn at
 * 60 frames a second or better, whatever the display runs at.
 *
 * Standing by and away used to redraw at half rate -- 30 a second -- to
 * save their cost, and it showed. And the gate was "a whole 60Hz frame has
 * passed", which on a 180Hz display is three frames of a third each: they
 * sum to 0.9999..., so it waited a fourth. This is a floor with slack
 * instead, and 11.8 is the one window that gives 60 or better on every
 * common rate: 180Hz and 120Hz draw at 60, 165Hz at 82, 144Hz at 72, 240Hz
 * at 80, 60Hz and 75Hz every frame. At 13 a 165Hz display fell to 55.
 */
export const MIN_DRAW_GAP_MS = 11.8;

function makeParticles(): { particles: Node[]; axes: Array<[number, number, number]> } {
  const out: Node[] = [];
  const axes: Array<[number, number, number]> = [];
  for (let k = 0; k < LOBES; k++) {
    const u = Math.random() * 2 - 1;
    const phi = Math.random() * Math.PI * 2;
    const s = Math.sqrt(Math.max(0, 1 - u * u));
    axes.push([s * Math.cos(phi), u, s * Math.sin(phi)]);
  }
  for (let i = 0; i < NODES + DUST; i++) {
    const u = Math.random() * 2 - 1;
    const phi = Math.random() * Math.PI * 2;
    const s = Math.sqrt(Math.max(0, 1 - u * u));
    const isNode = i < NODES;
    let r: number;
    let rigid = false;
    if (isNode) {
      let seen = 0;
      let layer = NODE_LAYERS[0];
      let index = 0;
      for (index = 0; index < NODE_LAYERS.length; index++) {
        seen += NODE_LAYERS[index].count;
        if (i < seen) { layer = NODE_LAYERS[index]; break; }
      }
      r = layer.rMin + Math.random() * (layer.rMax - layer.rMin);
      rigid = index === 0;
    } else {
      r = Math.random() < 0.72 ? 0.97 + Math.random() * 0.03 : 0.45 + Math.random() * 0.45;
    }
    const hx = s * Math.cos(phi);
    const hy = u;
    const hz = s * Math.sin(phi);
    const p: Node = {
      x: r * hx, y: r * hy, z: r * hz,
      vx: 0, vy: 0, vz: 0,
      hx, hy, hz, home: r, rigid, node: isNode,
      phase: Math.random() * 1000,
      heat: Math.random(),
      size: isNode ? 0.85 + Math.random() * 0.4 : 0.55 + Math.random() * 0.4,
      px: 0, py: 0, pd: 0, ps: 0, pa: 0, reg: 1,
      lobeA: 0, lobeB: 0, lobeW: 1,
    };
    // Which two patches this belongs to, computed once: its home direction
    // never changes, so this is not ten dot products per particle per frame.
    let bestI = 0, bestW = -1, secondI = 0, secondW = -1;
    for (let k = 0; k < LOBES; k++) {
      const ax = axes[k];
      const d = hx * ax[0] + hy * ax[1] + hz * ax[2];
      const w = d > 0 ? Math.pow(d, LOBE_SHARP) : 0;
      if (w > bestW) { secondW = bestW; secondI = bestI; bestW = w; bestI = k; }
      else if (w > secondW) { secondW = w; secondI = k; }
    }
    const total = bestW + secondW;
    p.lobeA = bestI;
    p.lobeB = total <= 1e-6 ? bestI : secondI;
    p.lobeW = total <= 1e-6 ? 1 : bestW / total;
    out.push(p);
  }
  return { particles: out, axes };
}

export function createPlexusState(): PlexusState {
  const built = makeParticles();
  const axes = built.axes;
  return {
    radius: PLEXUS_STATES.idle.r, flow: PLEXUS_STATES.idle.flow, bright: PLEXUS_STATES.idle.bright,
    links: PLEXUS_STATES.idle.links,
    look: { ...PLEXUS_LOOK.states.idle },
    pulse: 0, lastSpeech: 0, voiceEnv: 0,
    lobes: new Array(LOBES).fill(0.8),
    lobeTargets: new Array(LOBES).fill(0.8),
    lobeAxes: axes,
    spinX: 0, spinY: 0, spinZ: 0,
    lastState: 'idle',
    from: {
      radius: PLEXUS_STATES.idle.r, flow: PLEXUS_STATES.idle.flow, bright: PLEXUS_STATES.idle.bright,
      links: PLEXUS_STATES.idle.links, look: { ...PLEXUS_LOOK.states.idle }, breath: 1,
      spin: PLEXUS_STATES.idle.spin,
    },
    trans: 1, lastBreath: 1, lastSpin: PLEXUS_STATES.idle.spin,
    z: 0, zVel: 0, linkAge: 0,
    particles: built.particles,
    pairs: new Int32Array(64000),
    pairCount: 0,
  };
}

function rebuildPairs(S: PlexusState): void {
  const span = LINK_DIST * LINK_MARGIN * S.radius;
  const maxSq = span * span;
  const cap = S.pairs.length >> 1;
  const P = S.particles;
  let count = 0;
  for (let i = 0; i < NODES && count < cap; i++) {
    const a = P[i];
    for (let j = i + 1; j < NODES && count < cap; j++) {
      const c = P[j];
      const dx = a.x - c.x, dy = a.y - c.y, dz = a.z - c.z;
      if (dx * dx + dy * dy + dz * dz > maxSq) continue;
      S.pairs[count * 2] = i;
      S.pairs[count * 2 + 1] = j;
      count++;
    }
  }
  S.pairCount = count;
}

const bandBuffers: number[][] = [];
for (let i = 0; i < LINK_BANDS; i++) bandBuffers.push([]);
const rampBuckets: Node[][] = [];
for (let i = 0; i < RAMP_STEPS; i++) rampBuckets.push([]);

interface Surfaces {
  full: HTMLCanvasElement; fctx: CanvasRenderingContext2D;
  half: HTMLCanvasElement; hctx: CanvasRenderingContext2D;
  glow: HTMLCanvasElement; gctx: CanvasRenderingContext2D;
  sq: HTMLCanvasElement; qctx: CanvasRenderingContext2D;
  small: HTMLCanvasElement; sctx: CanvasRenderingContext2D;
}
const surfaces = new Map<string, Surfaces>();

function surfacesFor(w: number, h: number): Surfaces {
  const key = `${w}x${h}`;
  const cached = surfaces.get(key);
  if (cached) return cached;
  const mk = (cw: number, ch: number) => {
    const c = document.createElement('canvas');
    c.width = Math.max(1, cw);
    c.height = Math.max(1, ch);
    return c;
  };
  const full = mk(w, h);
  const half = mk(w >> 1, h >> 1);
  const glow = mk(w >> 1, h >> 1);
  const sq = mk(w, h);
  const small = mk(w >> 2, h >> 2);
  const made: Surfaces = {
    full, fctx: full.getContext('2d')!,
    half, hctx: half.getContext('2d')!,
    glow, gctx: glow.getContext('2d')!,
    sq, qctx: sq.getContext('2d')!,
    small, sctx: small.getContext('2d')!,
  };
  surfaces.set(key, made);
  return made;
}

const masks = new Map<string, HTMLCanvasElement>();
function edgeMask(w: number, h: number, hold: number): HTMLCanvasElement {
  const key = `${w}x${h}x${hold}`;
  const cached = masks.get(key);
  if (cached) return cached;
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d')!;
  const g = ctx.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, w / 2);
  g.addColorStop(0, 'rgba(255,255,255,1)');
  g.addColorStop(hold, 'rgba(255,255,255,1)');
  g.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, h);
  masks.set(key, canvas);
  return canvas;
}

let blurOk: boolean | null = null;
function blurSupported(ctx: CanvasRenderingContext2D): boolean {
  if (blurOk === null) {
    try {
      ctx.filter = 'blur(1px)';
      blurOk = ctx.filter !== 'none' && ctx.filter !== undefined;
      ctx.filter = 'none';
    } catch {
      blurOk = false;
    }
  }
  return blurOk;
}

export function drawPlexus(
  target: CanvasRenderingContext2D,
  canvas: HTMLCanvasElement,
  S: PlexusState,
  state: OrbState,
  t: number,
  dt: number,
  speech: number,
): void {
  const w = canvas.width;
  const h = canvas.height;
  const cx = w / 2;
  const cy = h / 2;
  const R = w / 2 - 10;
  const bufs = surfacesFor(w, h);
  const ctx = bufs.fctx;
  ctx.clearRect(0, 0, w, h);

  const cfg = PLEXUS_STATES[state];
  if (state !== S.lastState) {
    S.from = {
      radius: S.radius, flow: S.flow, bright: S.bright, links: S.links,
      look: { ...S.look }, breath: S.lastBreath, spin: S.lastSpin,
    };
    S.trans = 0;
    S.lastState = state;
  }
  S.trans = Math.min(1, S.trans + dt / MORPH_FRAMES);
  const e = easeMorph(S.trans);
  const F = S.from;
  S.radius = lerp(F.radius, cfg.r, e);
  S.flow = lerp(F.flow, cfg.flow, e);
  S.bright = lerp(F.bright, cfg.bright, e);
  S.links = lerp(F.links, cfg.links, e);
  const want = PLEXUS_LOOK.states[state];
  // Speaking's exposure follows the voice; the morph blends toward that
  // moving target, and once it lands it simply tracks it.
  const exposureTarget =
    state === 'speaking'
      ? want.exposure + (PLEXUS_LOOK.speakingVoiced - want.exposure) * S.voiceEnv
      : want.exposure;
  S.look.exposure = lerp(F.look.exposure, exposureTarget, e);
  S.look.curve = lerp(F.look.curve, want.curve, e);
  S.look.white = lerp(F.look.white, want.white, e);
  S.look.bloom = lerp(F.look.bloom, want.bloom, e);
  S.look.glow = lerp(F.look.glow, want.glow, e);
  S.look.soft = lerp(F.look.soft, want.soft, e);
  S.look.dots = lerp(F.look.dots, want.dots, e);
  S.look.haze = lerp(F.look.haze, want.haze, e);

  const rise = syllableRise(speech, S.lastSpeech);
  S.lastSpeech = speech;
  S.pulse = Math.max(S.pulse * Math.pow(0.88, dt), rise * 4.5);

  // Each patch has a target it swells toward rather than a level set
  // outright. A syllable used to raise the level in a single frame, which
  // is a blink -- a switch being thrown, not a voice. The target jumps, the
  // light takes a few frames to reach it and falls back on its own, and
  // each patch decays at a slightly different rate so a phrase never lights
  // the same way twice.
  const patch = PLEXUS_PATCHES[state];
  for (let i = 0; i < LOBES; i++) {
    const slow = patch.base + patch.swing * Math.sin(t * (0.01 + i * 0.0021) + i * 2.4);
    const fall = patch.fall + (i % 3) * 0.012;
    S.lobeTargets[i] = Math.min(1.55, Math.max(slow, S.lobeTargets[i] * Math.pow(fall, dt)));
    S.lobes[i] = approach(S.lobes[i], S.lobeTargets[i], LOBE_ATTACK, dt);
  }
  if (rise > 0.06 && patch.kick > 0) {
    // Toward whichever patches are facing the viewer. Picking uniformly put
    // half the syllables on the far side of the body, where a swell is a
    // vague smudge through the web rather than something being said.
    const cy0 = Math.cos(S.spinY), sy0 = Math.sin(S.spinY);
    let hit = 0;
    let bestFacing = -Infinity;
    for (let i = 0; i < LOBES; i++) {
      const ax = S.lobeAxes[i];
      const facing = -ax[0] * sy0 + ax[2] * cy0 + Math.random() * 0.8;
      if (facing > bestFacing) { bestFacing = facing; hit = i; }
    }
    const lift = patch.base + rise * patch.kick;
    S.lobeTargets[hit] = Math.max(S.lobeTargets[hit], lift);
    const other = (hit + 1 + ((Math.random() * (LOBES - 1)) | 0)) % LOBES;
    S.lobeTargets[other] = Math.max(S.lobeTargets[other], patch.base + rise * patch.kick * 0.5);
  }

  // The tumble rises and settles with the morph instead of starting at
  // full: a state change is felt as the body turning, not a jolt.
  const spin = lerp(F.spin, cfg.spin, e);
  S.lastSpin = spin;
  const tumble = S.trans < 1 ? Math.pow(Math.sin(Math.PI * S.trans), 2) : 0;
  S.spinY += (spin + tumble * 0.006) * dt;
  if (tumble > 0.01) {
    S.spinX += tumble * 0.005 * Math.sin(t * 0.09) * dt;
    S.spinZ += tumble * 0.0035 * Math.cos(t * 0.07) * dt;
  }

  const attack = speech > S.voiceEnv ? 0.22 : 0.035;
  S.voiceEnv += (speech - S.voiceEnv) * Math.min(1, attack * dt);

  const breath = lerp(S.from.breath, breathScale(state, t, S.voiceEnv), e);
  S.lastBreath = breath;
  // A twentieth either way. It was 0.78 + 0.22*sin, a 1.8x swing inherited
  // from the cloud orb, which over time took the whole body down to nearly
  // nothing for seconds at a stretch.
  const breathe = 0.95 + 0.05 * Math.sin(t * 0.02);
  // Dimmer as it contracts: the same marks packed into a smaller disc would
  // otherwise glare.
  const bright =
    S.bright * breathe * (1 + Math.min(1.2, S.pulse) * 0.22) * Math.pow(breath, 1.35);

  const sR = R * 0.8 * breath;
  const sizeScale = w / 560;
  const damp = Math.pow(DAMP, dt);
  const cosY = Math.cos(S.spinY), sinY = Math.sin(S.spinY);
  const cosX = Math.cos(S.spinX), sinX = Math.sin(S.spinX);
  const P = S.particles;
  const n = P.length;

  const fA = t * 0.011, fB = t * 0.014, fC = t * 0.009;
  const flow = FLOW * S.flow;
  const kick = S.pulse > 0.02 ? S.pulse * SYLLABLE_KICK : 0;

  // Only the nodes are simulated. The dust is fixed in the body and rides a
  // turn of its own, so the grain belongs to the surface for none of the
  // cost: they were each running a flow field, a spring, a tether and a
  // square root every frame to show motion the web was already showing.
  for (let i = 0; i < NODES; i++) {
    const p = P[i];
    const ph = p.phase;
    const fx = Math.sin(2.7 * p.y + fA) * Math.cos(3.4 * p.z + fB) + 0.45 * Math.sin(5.9 * p.z + fC);
    const fy = Math.sin(2.7 * p.z + fB) * Math.cos(3.4 * p.x + fC) + 0.45 * Math.sin(5.9 * p.x + fA);
    const fz = Math.sin(2.7 * p.x + fC) * Math.cos(3.4 * p.y + fA) + 0.45 * Math.sin(5.9 * p.y + fB);
    const own = 0.7 + 0.6 * p.heat;
    p.vx += fx * flow * own * dt;
    p.vy += fy * flow * own * dt;
    p.vz += fz * flow * own * dt;
    p.vx += Math.sin(t * 0.037 + ph) * DRIFT * dt;
    p.vy += Math.sin(t * 0.041 + ph * 1.7 + 2.1) * DRIFT * dt;
    p.vz += Math.sin(t * 0.033 + ph * 0.6 + 4.2) * DRIFT * dt;

    const dist = Math.sqrt(p.x * p.x + p.y * p.y + p.z * p.z) || 0.001;
    const spring = (p.home * S.radius - dist) * SPRING * dt;
    // The syllable reaches the outside after the inside, so it reads as a
    // wave through the body rather than one rigid heave.
    const wave = kick * (0.45 + 0.9 * p.heat) * (1 - 0.5 * p.home) * dt;
    const radial = (spring + wave) / dist;
    p.vx += p.x * radial;
    p.vy += p.y * radial;
    p.vz += p.z * radial;

    const tr = p.home * S.radius;
    p.vx += (p.hx * tr - p.x) * TETHER * dt;
    p.vy += (p.hy * tr - p.y) * TETHER * dt;
    p.vz += (p.hz * tr - p.z) * TETHER * dt;

    p.vx *= damp; p.vy *= damp; p.vz *= damp;
    p.x += p.vx * dt; p.y += p.vy * dt; p.z += p.vz * dt;

    const d2 = Math.sqrt(p.x * p.x + p.y * p.y + p.z * p.z);
    if (p.rigid) {
      // Held exactly on its sphere, free to slide across it. This is what
      // keeps the silhouette a clean circle while everything inside churns.
      const want = p.home * S.radius;
      const k = want / (d2 || 1e-6);
      p.x *= k; p.y *= k; p.z *= k;
      const inv = 1 / (want * want);
      const vr = (p.vx * p.x + p.vy * p.y + p.vz * p.z) * inv;
      p.vx -= p.x * vr; p.vy -= p.y * vr; p.vz -= p.z * vr;
    } else {
      const limit = S.radius * HARD_LIMIT;
      if (d2 > limit) {
        const k = limit / d2;
        p.x *= k; p.y *= k; p.z *= k;
        p.vx *= 0.5; p.vy *= 0.5; p.vz *= 0.5;
      }
    }

    const rx = p.x * cosY + p.z * sinY;
    let rz = -p.x * sinY + p.z * cosY;
    const ry = p.y * cosX - rz * sinX;
    rz = p.y * sinX + rz * cosX + S.z;
    const persp = FOCAL / (FOCAL - rz);
    p.px = cx + rx * persp * sR;
    p.py = cy + ry * persp * sR;
    p.pd = Math.max(0, Math.min(1, (rz + 1) / 2));
    p.ps = p.size * persp * sizeScale * breath;
    p.reg = S.lobes[p.lobeA] * p.lobeW + S.lobes[p.lobeB] * (1 - p.lobeW);
    p.pa = Math.min(0.95, (PA0 + PA1 * p.pd) * bright * p.reg * S.look.dots * S.look.exposure);
  }

  const dCosY = Math.cos(S.spinY * DUST_TURN), dSinY = Math.sin(S.spinY * DUST_TURN);
  for (let i = NODES; i < n; i++) {
    const q = P[i];
    const qx = q.x * dCosY + q.z * dSinY;
    let qz = -q.x * dSinY + q.z * dCosY;
    const qy = q.y * cosX - qz * sinX;
    qz = q.y * sinX + qz * cosX + S.z;
    const qp = FOCAL / (FOCAL - qz);
    q.px = cx + qx * qp * sR * S.radius;
    q.py = cy + qy * qp * sR * S.radius;
    q.pd = Math.max(0, Math.min(1, (qz + 1) / 2));
    q.ps = q.size * qp * sizeScale * breath;
    q.reg = S.lobes[q.lobeA] * q.lobeW + S.lobes[q.lobeB] * (1 - q.lobeW);
    q.pa = Math.min(0.95, (PA0 + PA1 * q.pd) * bright * q.reg * S.look.dots * S.look.exposure);
  }

  // Links first, so the particles sit on top of their own wiring.
  for (let i = 0; i < LINK_BANDS; i++) bandBuffers[i].length = 0;
  if (S.links > 0.02) {
    S.linkAge += dt;
    if (S.linkAge >= LINK_REBUILD_FRAMES || S.pairCount === 0) {
      rebuildPairs(S);
      S.linkAge = 0;
    }
    const speechReach = 1 + speech * 0.1;
    // Scaled by the current brightness, or the cull stops removing the
    // faintest fifth and starts removing the whole web.
    const cut = 0.045 * Math.min(1, bright);
    for (let i = 0; i < S.pairCount; i++) {
      const a = P[S.pairs[i * 2]];
      const c = P[S.pairs[i * 2 + 1]];
      const ddx = a.x - c.x, ddy = a.y - c.y, ddz = a.z - c.z;
      const sq = ddx * ddx + ddy * ddy + ddz * ddz;
      const rmean = Math.max(LINK_RMEAN_FLOOR, (a.home + c.home) * 0.5);
      // Reach scales with the orb, or contracting the body triples the link
      // count: idle once drew 20,780 lines where listening drew 7,400.
      const reach = LINK_DIST * rmean * S.radius * speechReach;
      const pairSq = reach * reach;
      if (sq > pairSq) continue;
      const near = 1 - Math.sqrt(sq / pairSq) * 0.75;
      const depth = (a.pd + c.pd) / 2;
      const alpha = near * (0.118 + 0.155 * depth) * bright * S.links * (a.reg + c.reg) * 0.5;
      if (alpha <= cut) continue;
      const band = Math.min(LINK_BANDS - 1, ((alpha / LINK_MAX_ALPHA) * LINK_BANDS) | 0);
      bandBuffers[band].push(a.px, a.py, c.px, c.py);
    }
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    // Line width scales with the canvas, like everything else does. Held at
    // a fixed 0.7 while the orb, the dots and the blur all grew with size,
    // the web came out 27% thinner per unit area on the 764px Voice orb and
    // 18% too thick on the 473px chat one -- the Voice page showed a field
    // of dots with barely a line between them.
    const idealWidth = 0.7 * sizeScale * Math.max(0.6, breath);
    // Canvas renders a sub-pixel stroke by reducing its coverage, so below
    // a pixel it fades instead of thinning. Draw it a pixel wide and take
    // the difference out of the alpha, which is the same amount of light.
    const drawWidth = Math.max(0.85, idealWidth);
    const widthAlpha = idealWidth / drawWidth;
    ctx.lineWidth = drawWidth;
    for (let b = 0; b < LINK_BANDS; b++) {
      const buf = bandBuffers[b];
      if (!buf.length) continue;
      ctx.strokeStyle = RAMP[Math.min(RAMP_STEPS - 1, 3 + b * 3)];
      ctx.globalAlpha = Math.min(1, ((b + 0.5) / LINK_BANDS) * LINK_MAX_ALPHA * widthAlpha * S.look.exposure);
      ctx.beginPath();
      for (let i = 0; i < buf.length; i += 4) {
        ctx.moveTo(buf[i], buf[i + 1]);
        ctx.lineTo(buf[i + 2], buf[i + 3]);
      }
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
    ctx.restore();
  }

  // Particles, additively, one fillStyle per colour band. White is never
  // assigned: it appears where the additive pass stacks, which is the rim
  // and the dense knots of the web.
  for (let i = 0; i < RAMP_STEPS; i++) rampBuckets[i].length = 0;
  for (let i = 0; i < n; i++) {
    const q = P[i];
    if (q.pa <= 0.02) continue;
    const u = 0.18 + q.pd * 0.74 + (q.node ? 0 : 0.06);
    rampBuckets[Math.min(RAMP_STEPS - 1, (u * RAMP_STEPS) | 0)].push(q);
  }
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  for (let i = 0; i < RAMP_STEPS; i++) {
    const bucket = rampBuckets[i];
    if (!bucket.length) continue;
    ctx.fillStyle = RAMP[i];
    for (let j = 0; j < bucket.length; j++) {
      const m = bucket[j];
      ctx.globalAlpha = m.pa;
      ctx.fillRect(m.px, m.py, m.ps, m.ps);
    }
  }
  ctx.globalAlpha = 1;
  ctx.restore();

  ctx.save();
  ctx.globalCompositeOperation = 'destination-in';
  ctx.drawImage(edgeMask(w, h, 0.92), 0, 0);
  ctx.restore();

  // Contrast without leaving the page. A mark's brightness here lives
  // almost entirely in its alpha: every mark is drawn in a full-strength
  // ramp colour at an alpha, and 'lighter' sums the alphas. So masking the
  // frame by itself ('destination-in') gives alpha squared on the same
  // colours -- the contrast curve -- and stays transparent wherever there
  // is no light. The first version squared colours instead, which needs
  // opaque layers: composed on black it was a dark square over the HUD
  // grid; composed on the page's colour it was a dark circle, because the
  // grid and gradient behind the orb are painted by a fixed backdrop the
  // orb's stacking context cannot see, and no blend mode reaches it.
  const L = S.look;
  const hw = bufs.half.width, hh = bufs.half.height;
  const squareOf = (c: CanvasRenderingContext2D, src: HTMLCanvasElement, cw: number, ch: number) => {
    c.globalCompositeOperation = 'source-over';
    c.globalAlpha = 1;
    c.filter = 'none';
    c.clearRect(0, 0, cw, ch);
    c.drawImage(src, 0, 0, cw, ch);
    c.globalCompositeOperation = 'destination-in';
    c.drawImage(src, 0, 0, cw, ch);
    c.globalCompositeOperation = 'source-over';
  };

  bufs.hctx.globalCompositeOperation = 'source-over';
  bufs.hctx.globalAlpha = 1;
  bufs.hctx.clearRect(0, 0, hw, hh);
  bufs.hctx.drawImage(bufs.full, 0, 0, hw, hh);
  squareOf(bufs.gctx, bufs.half, hw, hh);
  if (L.curve > 0.01) squareOf(bufs.qctx, bufs.full, w, h);

  target.clearRect(0, 0, w, h);
  target.save();
  target.globalCompositeOperation = 'lighter';
  target.imageSmoothingEnabled = true;
  // Exposure is applied to the marks as they are drawn, not here, so every
  // layer below is added at a gain of about one or two. Applied to the
  // finished frame it had to be drawn in repeated passes -- 'lighter'
  // clamps at 1 per draw -- and standing by's glow, at exposure squared
  // times two, re-ran its blur eighteen times a frame. The squared layers
  // still carry exposure squared: they are built from the exposed frame.
  const add = (img: CanvasImageSource, gain: number, sw: number, sh: number, filter: string) => {
    target.filter = filter || 'none';
    let left = gain;
    while (left > 1e-3) {
      target.globalAlpha = Math.min(1, left);
      target.drawImage(img, 0, 0, sw, sh, 0, 0, w, h);
      left -= 1;
    }
    target.filter = 'none';
  };
  const soft = L.soft * (w / 764);
  const canBlur = blurSupported(target);
  const softFilter = canBlur && soft > 0.05 ? `blur(${soft.toFixed(2)}px)` : '';
  add(bufs.full, 1, w, h, softFilter);
  if (L.curve > 0.01) {
    const drain = L.white > 0.01 ? `saturate(${Math.max(0, 1 - L.white).toFixed(2)})` : '';
    add(bufs.sq, L.curve, w, h, [softFilter, drain].filter(Boolean).join(' '));
  }
  if (canBlur) {
    add(bufs.half, L.bloom, hw, hh, `blur(${(w / PLEXUS_LOOK.bloomRadius).toFixed(1)}px)`);
    add(bufs.glow, L.glow, hw, hh, `blur(${(w / PLEXUS_LOOK.glowRadius).toFixed(1)}px)`);
    if (L.haze > 0.01) {
      add(bufs.half, L.haze, hw, hh, `blur(${(w / PLEXUS_LOOK.hazeRadius).toFixed(1)}px)`);
    }
  } else {
    const sw = bufs.small.width, sh = bufs.small.height;
    bufs.sctx.clearRect(0, 0, sw, sh);
    bufs.sctx.drawImage(bufs.half, 0, 0, sw, sh);
    add(bufs.small, L.bloom, sw, sh, '');
    add(bufs.glow, L.glow, hw, hh, '');
  }
  target.restore();
}

