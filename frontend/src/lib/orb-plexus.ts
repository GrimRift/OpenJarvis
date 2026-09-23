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

/** Per state: radius, how hard the flow stirs, spin, brightness, wiring,
 * and pace -- how fast the web's own clock runs: the drift of its currents
 * and of its lit patches, the shimmer that reads as electricity. */
export interface PlexusStateConfig {
  r: number;
  flow: number;
  spin: number;
  bright: number;
  links: number;
  pace: number;
}

export const PLEXUS_STATES: Record<OrbState, PlexusStateConfig> = {
  // "idle" is the state Sage sits in whenever it has nothing to do and
  // someone is there -- the one the UI labels STANDING BY and the one a user
  // sees nearly all the time. It carries the preview's standing-by settings.
  // The preview's own "idle" button is a different thing: an orb with nobody
  // watching, which here is "away".
  idle: { r: 0.8, flow: 0.47, spin: 0.0012, bright: 0.97, links: 0.65, pace: 0.8 },
  // Spin is radians per 60Hz frame. Standing by turns once every 87s --
  // slow enough to read as resting, never stopped; it was once every 35s,
  // the same as an empty desk. Away turns slower still, once every 131s.
  //
  // Nobody at the desk: smaller and dimmer, so sitting down is a visible
  // waking up.
  away: { r: 0.65, flow: 0.45, spin: 0.0008, bright: 0.68, links: 0.6, pace: 0.75 },
  // Only a little larger than standing by, and never as large as a speaking
  // orb at full voice.
  listening: { r: 0.86, flow: 1.0, spin: 0.0042, bright: 1.12, links: 0.8, pace: 1 },
  // Speaking's own marks are the fine mesh now, as muted as listening's;
  // its light comes from the currents moving over it (PLEXUS_ENERGY).
  speaking: { r: 0.97, flow: 1.55, spin: 0.005, bright: 1.05, links: 0.85, pace: 1 },
};

/**
 * How the slow patches behave per state: resting level, how far they drift
 * on their own, how fast a raised one falls back. Syllables no longer kick
 * them -- the moving currents carry speech (PLEXUS_ENERGY).
 *
 * Speaking's base was 0.62 at first, set low so a syllable had room to rise.
 * But speaking also rests at standing-by size, and the dim-on-contraction
 * rule fires with it -- three multipliers stacked, and a silent speaking orb
 * came out dimmer than standing by. It was the dimmest thing on screen
 * exactly when Sage was mid-sentence.
 */
export const PLEXUS_PATCHES: Record<OrbState, { base: number; swing: number; fall: number }> = {
  away: { base: 0.8, swing: 0.16, fall: 0.97 },
  idle: { base: 0.8, swing: 0.16, fall: 0.97 },
  listening: { base: 0.72, swing: 0.22, fall: 0.945 },
  speaking: { base: 0.8, swing: 0.2, fall: 0.932 },
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
 * rampTop   the highest point of the colour ramp any mark is drawn in. The
 *           top of the ramp is pale and then white, and a mark reaches it by
 *           being bright and near: speaking's lines land there far more
 *           than listening's, so its peaks went pale (red over green 0.40
 *           among its brightest pixels, against listening's 0.25) even with
 *           the whitening switched off. It is additive clipping: stacked
 *           turquoise saturates green and blue while red keeps climbing,
 *           and the peak burns white. A cap works but dulls the state; the
 *           'red' control below is how speaking avoids it.
 * red       how much of the ramp's red is kept, 0..1. See RAMPS.
 * energy    how much the moving currents light the picture (PLEXUS_ENERGY)
 * lobes     how much the old fixed patches still light it -- the slow
 *           shimmer standing by and listening keep
 * wires     the share of the web's lines drawn, 0..1: a fixed choice per pair
 *           of nodes. A lit speaking patch piles so many lines on each
 *           other that it turns into a solid slab; drawing fewer, each as
 *           bright, keeps the knots where lines cross and opens the slab
 *           back into a web.
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
  rampTop: number;
  red: number;
  wires: number;
  energy: number;
  lobes: number;
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
  //   listening         40  166  225      -      -  -
  //   speaking          52  202  238      -      -  -
  //   a pause in it     40  124  211      -      -  -
  //   (these three measured inside the shell at full size, not at 300px)
  //
  // A screenshot of the version before scored sharpness 0.75 and
  // whiteness 0.61 -- half again as sharp as the reference and three times
  // as white; the one after it (soft 1, haze 0.8) was asked to be less
  // blurry. Standing by now sits on the reference.
  //
  // Speaking is a fine mesh with energy moving over it (PLEXUS_ENERGY): at
  // least three quarters of its disc shows web in every frame, pulse or
  // not, and its brightest accents still outshine listening's peaks. Solid
  // 16 px blocks averaging over 200 are 3-4% of its disc -- they were 16%
  // when syllables lit whole patches at once.
  states: {
    idle: { exposure: 3.4, curve: 0.45, white: 0.25, bloom: 1.05, glow: 2, soft: 0.45, dots: 0.4, haze: 0.5, rampTop: 1, red: 1, wires: 1, energy: 0, lobes: 1 },
    away: { exposure: 2.4, curve: 0.45, white: 0.25, bloom: 1.05, glow: 2, soft: 0.45, dots: 0.4, haze: 0.5, rampTop: 1, red: 1, wires: 1, energy: 0, lobes: 1 },
    listening: { exposure: 2.9, curve: 0.6, white: 0.35, bloom: 1.1, glow: 2, soft: 0.3, dots: 0.55, haze: 0.35, rampTop: 1, red: 1, wires: 1, energy: 0, lobes: 1 },
    speaking: { exposure: 2.2, curve: 0.6, white: 0.35, bloom: 1.1, glow: 2, soft: 0.3, dots: 0.85, haze: 0.35, rampTop: 1, red: 0.5, wires: 0.9, energy: 1, lobes: 0.2 },
  } as Record<OrbState, PlexusLook>,
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
/**
 * The spikes. A pulse (see PLEXUS_ENERGY) throws a few interior nodes out
 * through the pinned shell from the lead current's region, and the web
 * strung to them from the surface draws each spike as a fan. They rise and
 * retreat with the pulse's envelope rather than from impulses, so a spike
 * is felt as the pulse travelling out, not as the orb coming apart.
 *
 * They used to be kicked outward on every syllable, from wherever a patch
 * happened to light, and allowed to 1.3x the radius: several unrelated
 * thorns at once, long enough to make the silhouette look unstable.
 *
 * length  how far past the shell a spike's tip reaches at the pulse's peak,
 *         as a fraction of the orb's radius
 * share   the share of middle-shell nodes that can become a spike
 * width   how tightly they gather round the lead current (1 - cos units)
 * reach   how much further a thrown node's links reach, so a spike is
 *         strung to more of the surface and reads as a fan, not a line
 * glow    how much brighter a spike's node and its lines are, per unit it
 *         is thrown past the radius
 * edge    where the canvas's soft edge begins, as a fraction of its half
 *         width
 */
export const PLEXUS_THORNS = { length: 0.22, share: 0.3, width: 0.16, reach: 0.3, glow: 2.5, edge: 0.96 };

/**
 * Energy moving inside the sphere, while Sage speaks.
 *
 * Syllables used to kick one of ten fixed patches at random -- scattered
 * hotspots switching on and off across the body. Now a couple of currents
 * drift over the sphere, each a soft region of light; the voice swells them
 * gently and continuously, and a strong syllable, once the orb has been
 * calm for a moment, fires a pulse on the lead current that rises, holds
 * and fades (and throws the spikes). Between pulses there is a calm
 * interval, so each one reads as an event.
 *
 * currents  how many drift over the sphere
 * width     their size, in (1 - cos) of the angle from their centre
 * travel    how fast they drift, radians per 60Hz frame
 * rest      their level with Sage speaking but between words
 * voice     how much the voice adds on top, at most (it saturates)
 * pulse     the level a pulse lifts the lead current to at its peak
 * rise/hold/fall  the pulse's envelope, in 60Hz frames
 * calm      frames after a pulse before another may fire
 * trigger   how sharp a syllable onset has to be to fire one
 * accent    the share of lines that can carry a current's light; the rest
 *           stay the fine mesh, which is what keeps the orb whole between
 *           pulses. Few, on purpose: at 30% a lit region packed so many
 *           bright lines together under loud speech that it read as solid
 * gain      how bright an accent line gets at full current
 */
export const PLEXUS_ENERGY = {
  currents: 2,
  width: 0.2,
  travel: 0.004,
  rest: 0.35,
  voice: 0.5,
  pulse: 1.2,
  rise: 18,
  hold: 10,
  fall: 48,
  calm: 72,
  trigger: 0.14,
  accent: 0.18,
  gain: 4.2,
};

const RAMP_STOPS: Array<[number, number, number, number]> = [
  [0, 10, 76, 107],
  [0.38, 18, 136, 184],
  [0.68, 56, 224, 247],
  [0.88, 168, 242, 252],
  [1, 255, 255, 255],
];
const RAMP_STEPS = 18;

/** A fixed number in 0..1 for a pair of nodes. */
function pairHash(a: number, b: number): number {
  let h = Math.imul(a, 0x9e3779b1) ^ Math.imul(b + 0x7f4a7c15, 0x85ebca77);
  h ^= h >>> 15;
  h = Math.imul(h, 0x2c1b3c6d);
  h ^= h >>> 12;
  return (h >>> 0) / 4294967296;
}

/** A second fixed number for a pair, independent of pairHash: which lines
 * can carry a current's light is not tied to which are drawn at all. */
function accentHash(a: number, b: number): number {
  return pairHash(b + 7919, a + 104729);
}

function buildRamp(red = 1): string[] {
  const ramp: string[] = [];
  for (let i = 0; i < RAMP_STEPS; i++) {
    const u = i / (RAMP_STEPS - 1);
    let k = 0;
    while (k < RAMP_STOPS.length - 2 && u > RAMP_STOPS[k + 1][0]) k++;
    const a = RAMP_STOPS[k];
    const b = RAMP_STOPS[k + 1];
    const f = (u - a[0]) / (b[0] - a[0] || 1);
    ramp.push(
      `rgb(${Math.round((a[1] + (b[1] - a[1]) * f) * red)},${Math.round(a[2] + (b[2] - a[2]) * f)},${Math.round(
        a[3] + (b[3] - a[3]) * f,
      )})`,
    );
  }
  return ramp;
}
/**
 * The ramp at five strengths of red, chosen per state by the look's 'red'.
 * Red is the only channel that turns stacked cyan white: where marks pile
 * up, green and blue saturate and red alone keeps climbing. Taking it out
 * lets a state be driven as bright as it likes and saturate to vivid cyan.
 * Precomputed, since a colour string per mark is a CSS parse per mark.
 */
const RED_LEVELS = 4;
const RAMPS = Array.from({ length: RED_LEVELS + 1 }, (_, k) => buildRamp(k / RED_LEVELS));

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
  /** How far past the orb's radius this node has been thrown, 0 inside. */
  out: number;
  /** Can be thrown out as a spike: a few middle-shell nodes. */
  spike: boolean;
  /** How much the moving currents light it this frame. */
  en: number;
  lobeA: number; lobeB: number; lobeW: number;
}

/** Everything a state change blends, captured as it was when the change
 * began -- mid-morph too, so a second change starts from what is on screen
 * rather than from either state's settings. */
interface Morph {
  radius: number; flow: number; bright: number; links: number;
  look: PlexusLook; breath: number; spin: number; pace: number;
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
  lastSpeech: number; voiceEnv: number;
  /** The currents: where each is (body frame, unit), the axis it drifts
   * round, and how bright it is. */
  currents: Current[];
  /** The pulse: its envelope now (0..1 x strength), frames into it (-1
   * when none is running), its strength, how long it has been calm,
   * and which current leads it. */
  burst: number; burstAt: number; burstAmp: number; calmFor: number; lead: number;
  /** Spikes allowed at all -- the user's setting. */
  spikes: boolean;
  lobes: number[];
  lobeTargets: number[];
  lobeAxes: Array<[number, number, number]>;
  spinX: number; spinY: number; spinZ: number;
  lastState: OrbState;
  /** Where the last state change started from, and how far through the
   * morph to the new state it is (0..1). */
  from: Morph; trans: number;
  lastBreath: number; lastSpin: number;
  /** The web's own clock, advanced at the state's pace. */
  phase: number; lastPace: number;
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
    let spike = false;
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
      spike = index === 1 && Math.random() < PLEXUS_THORNS.share;
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
      px: 0, py: 0, pd: 0, ps: 0, pa: 0, reg: 1, out: 0, spike, en: 0,
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

interface Current {
  x: number; y: number; z: number;
  ax: number; ay: number; az: number;
  level: number;
}

function unit(x: number, y: number, z: number): [number, number, number] {
  const m = Math.hypot(x, y, z) || 1;
  return [x / m, y / m, z / m];
}

function makeCurrents(n: number): Current[] {
  const out: Current[] = [];
  for (let i = 0; i < n; i++) {
    // Spread apart to start, each drifting round its own tilted axis.
    const phi = (i / Math.max(1, n)) * Math.PI * 2 + 0.7;
    const [x, y, z] = unit(Math.cos(phi), 0.35 - 0.7 * i, Math.sin(phi));
    const [ax, ay, az] = unit(0.3 + i * 0.5, 1, -0.4 + i * 0.9);
    out.push({ x, y, z, ax, ay, az, level: 0 });
  }
  return out;
}

/** Rotate unit vector v about unit axis (kx, ky, kz) by angle a. */
function turn(v: { x: number; y: number; z: number }, kx: number, ky: number, kz: number, a: number): void {
  const c = Math.cos(a), sn = Math.sin(a);
  const dot = kx * v.x + ky * v.y + kz * v.z;
  const crx = ky * v.z - kz * v.y, cry = kz * v.x - kx * v.z, crz = kx * v.y - ky * v.x;
  const x = v.x * c + crx * sn + kx * dot * (1 - c);
  const y = v.y * c + cry * sn + ky * dot * (1 - c);
  const z = v.z * c + crz * sn + kz * dot * (1 - c);
  const m = Math.hypot(x, y, z) || 1;
  v.x = x / m; v.y = y / m; v.z = z / m;
}

/** The pulse's envelope f frames in: a raised-cosine rise, a hold, a
 * raised-cosine fall. Smooth at both ends, so it neither snaps on nor off. */
export function pulseEnvelope(f: number): number {
  const E = PLEXUS_ENERGY;
  if (f < 0) return 0;
  if (f < E.rise) return 0.5 - 0.5 * Math.cos((Math.PI * f) / E.rise);
  if (f < E.rise + E.hold) return 1;
  const g = f - E.rise - E.hold;
  if (g < E.fall) return 0.5 + 0.5 * Math.cos((Math.PI * g) / E.fall);
  return 0;
}

/** How much light the currents throw on a point of the body (unit
 * direction, body frame): each a soft region round its centre. */
function energyAt(S: PlexusState, hx: number, hy: number, hz: number): number {
  let e = 0;
  const width = PLEXUS_ENERGY.width;
  for (let k = 0; k < S.currents.length; k++) {
    const c = S.currents[k];
    if (c.level < 0.005) continue;
    const near = hx * c.x + hy * c.y + hz * c.z;
    if (near < 0) continue;
    e += c.level * Math.exp(-(1 - near) / width);
  }
  return e;
}

export function createPlexusState(): PlexusState {
  const built = makeParticles();
  const axes = built.axes;
  return {
    radius: PLEXUS_STATES.idle.r, flow: PLEXUS_STATES.idle.flow, bright: PLEXUS_STATES.idle.bright,
    links: PLEXUS_STATES.idle.links,
    look: { ...PLEXUS_LOOK.states.idle },
    lastSpeech: 0, voiceEnv: 0,
    currents: makeCurrents(PLEXUS_ENERGY.currents),
    burst: 0, burstAt: -1, burstAmp: 0, calmFor: 1e9, lead: 0,
    spikes: true,
    lobes: new Array(LOBES).fill(0.8),
    lobeTargets: new Array(LOBES).fill(0.8),
    lobeAxes: axes,
    spinX: 0, spinY: 0, spinZ: 0,
    lastState: 'idle',
    from: {
      radius: PLEXUS_STATES.idle.r, flow: PLEXUS_STATES.idle.flow, bright: PLEXUS_STATES.idle.bright,
      links: PLEXUS_STATES.idle.links, look: { ...PLEXUS_LOOK.states.idle }, breath: 1,
      spin: PLEXUS_STATES.idle.spin, pace: PLEXUS_STATES.idle.pace,
    },
    trans: 1, lastBreath: 1, lastSpin: PLEXUS_STATES.idle.spin,
    phase: 0, lastPace: PLEXUS_STATES.idle.pace,
    z: 0, zVel: 0, linkAge: 0,
    particles: built.particles,
    pairs: new Int32Array(64000),
    pairCount: 0,
  };
}

function rebuildPairs(S: PlexusState): void {
  const span = LINK_DIST * LINK_MARGIN * (1 + PLEXUS_THORNS.reach) * S.radius;
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
      look: { ...S.look }, breath: S.lastBreath, spin: S.lastSpin, pace: S.lastPace,
    };
    S.trans = 0;
    S.lastState = state;
  }
  S.trans = Math.min(1, S.trans + dt / MORPH_FRAMES);
  const e = easeMorph(S.trans);
  const F = S.from;
  const pace = lerp(F.pace, cfg.pace, e);
  S.lastPace = pace;
  S.phase += pace * dt;
  const tw = S.phase;
  S.radius = lerp(F.radius, cfg.r, e);
  S.flow = lerp(F.flow, cfg.flow, e);
  S.bright = lerp(F.bright, cfg.bright, e);
  S.links = lerp(F.links, cfg.links, e);
  const want = PLEXUS_LOOK.states[state];
  S.look.exposure = lerp(F.look.exposure, want.exposure, e);
  S.look.curve = lerp(F.look.curve, want.curve, e);
  S.look.white = lerp(F.look.white, want.white, e);
  S.look.bloom = lerp(F.look.bloom, want.bloom, e);
  S.look.glow = lerp(F.look.glow, want.glow, e);
  S.look.soft = lerp(F.look.soft, want.soft, e);
  S.look.dots = lerp(F.look.dots, want.dots, e);
  S.look.haze = lerp(F.look.haze, want.haze, e);
  S.look.rampTop = lerp(F.look.rampTop, want.rampTop, e);
  S.look.red = lerp(F.look.red, want.red, e);
  S.look.wires = lerp(F.look.wires, want.wires, e);
  S.look.energy = lerp(F.look.energy, want.energy, e);
  S.look.lobes = lerp(F.look.lobes, want.lobes, e);

  const rise = syllableRise(speech, S.lastSpeech);
  S.lastSpeech = speech;

  // Each patch has a target it swells toward rather than a level set
  // outright. A syllable used to raise the level in a single frame, which
  // is a blink -- a switch being thrown, not a voice. The target jumps, the
  // light takes a few frames to reach it and falls back on its own, and
  // each patch decays at a slightly different rate so a phrase never lights
  // the same way twice.
  const patch = PLEXUS_PATCHES[state];
  for (let i = 0; i < LOBES; i++) {
    const slow = patch.base + patch.swing * Math.sin(tw * (0.01 + i * 0.0021) + i * 2.4);
    const fall = patch.fall + (i % 3) * 0.012;
    S.lobeTargets[i] = Math.min(1.55, Math.max(slow, S.lobeTargets[i] * Math.pow(fall, dt)));
    S.lobes[i] = approach(S.lobes[i], S.lobeTargets[i], LOBE_ATTACK, dt);
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

  // The currents drift, and swell with the voice. A pulse fires on a sharp
  // onset once the orb has been calm long enough, and runs its envelope.
  const EN = PLEXUS_ENERGY;
  if (S.burstAt >= 0) {
    S.burstAt += dt;
    S.burst = pulseEnvelope(S.burstAt) * S.burstAmp;
    if (S.burstAt >= EN.rise + EN.hold + EN.fall) {
      S.burstAt = -1;
      S.burst = 0;
      S.calmFor = 0;
    }
  } else {
    S.calmFor += dt;
    if (state === 'speaking' && rise > EN.trigger && S.calmFor >= EN.calm) {
      S.burstAt = 0;
      S.burstAmp = Math.min(1, 0.55 + rise);
      // Led by whichever current is nearest the edge as seen: a spike
      // points outward from its region, so one facing the viewer is
      // foreshortened to nothing and one behind is hidden. At the edge it
      // stands out sideways against the dark. The currents drift, so the
      // side still changes from pulse to pulse.
      let best = Infinity;
      const cY = Math.cos(S.spinY), sY = Math.sin(S.spinY);
      const cX = Math.cos(S.spinX), sX = Math.sin(S.spinX);
      for (let k = 0; k < S.currents.length; k++) {
        const c = S.currents[k];
        const z0 = -c.x * sY + c.z * cY;
        const facing = Math.abs(c.y * sX + z0 * cX);
        if (facing < best) { best = facing; S.lead = k; }
      }
    }
  }
  for (let k = 0; k < S.currents.length; k++) {
    const cur = S.currents[k];
    turn(cur, cur.ax, cur.ay, cur.az, EN.travel * (1 + 0.35 * k) * dt);
    // The axis wanders too, so a current's path is not a fixed ring.
    const axis = { x: cur.ax, y: cur.ay, z: cur.az };
    turn(axis, 0, 1, 0, EN.travel * 0.3 * dt);
    cur.ax = axis.x; cur.ay = axis.y; cur.az = axis.z;
    // The voice's part saturates, and a pulse fills the room left under a
    // ceiling rather than stacking on top: loud, sustained speech held the
    // currents near full and a pulse on top packed their regions solid (a
    // harsh test voice clipped 13% of the disc). Normal speech is barely
    // changed by either.
    const voiced = state === 'speaking' ? EN.rest + EN.voice * (1 - Math.exp(-2.5 * S.voiceEnv)) : 0;
    const share = k === S.lead ? 1 : 0.6;
    const base = voiced * share;
    const target = base + (k === S.lead ? Math.max(0, EN.pulse - base) * S.burst : 0);
    cur.level = approach(cur.level, target, 0.08, dt);
  }

  const breath = lerp(S.from.breath, breathScale(state, t, S.voiceEnv), e);
  S.lastBreath = breath;
  // A twentieth either way. It was 0.78 + 0.22*sin, a 1.8x swing inherited
  // from the cloud orb, which over time took the whole body down to nearly
  // nothing for seconds at a stretch.
  const breathe = 0.95 + 0.05 * Math.sin(t * 0.02);
  // Dimmer as it contracts: the same marks packed into a smaller disc would
  // otherwise glare.
  const bright =
    S.bright * breathe * (1 + 0.12 * S.burst) * Math.pow(breath, 1.35);

  const sR = R * 0.8 * breath;
  const sizeScale = w / 560;
  const wires = Math.min(1, Math.max(0, S.look.wires));
  const rampTop = Math.round(Math.min(1, Math.max(0, S.look.rampTop)) * (RAMP_STEPS - 1));
  const ramp = RAMPS[Math.round(Math.min(1, Math.max(0, S.look.red)) * RED_LEVELS)];
  const damp = Math.pow(DAMP, dt);
  const cosY = Math.cos(S.spinY), sinY = Math.sin(S.spinY);
  const cosX = Math.cos(S.spinX), sinX = Math.sin(S.spinX);
  const P = S.particles;
  const n = P.length;

  const fA = tw * 0.011, fB = tw * 0.014, fC = tw * 0.009;
  const flow = FLOW * S.flow;
  const lead = S.currents[S.lead];
  const spiking = S.spikes && S.burst > 0.001;
  const energyOn = S.look.energy > 0.001;

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
    p.vx += Math.sin(tw * 0.037 + ph) * DRIFT * dt;
    p.vy += Math.sin(tw * 0.041 + ph * 1.7 + 2.1) * DRIFT * dt;
    p.vz += Math.sin(tw * 0.033 + ph * 0.6 + 4.2) * DRIFT * dt;

    const dist = Math.sqrt(p.x * p.x + p.y * p.y + p.z * p.z) || 0.001;
    const spring = (p.home * S.radius - dist) * SPRING * dt;
    const radial = spring / dist;
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
      const limit = S.radius * 1.12;
      if (d2 > limit) {
        const k = limit / d2;
        p.x *= k; p.y *= k; p.z *= k;
        p.vx *= 0.5; p.vy *= 0.5; p.vz *= 0.5;
      }
    }

    // A spike: a spike-carrying node in the lead current's region is drawn
    // pushed out through the shell by the pulse. Drawn there, not moved
    // there: it keeps its neighbours, so the surface strings a fan to the
    // tip, and the rise and retreat are exactly the pulse's envelope.
    let sx = p.x, sy = p.y, sz = p.z;
    p.out = 0;
    if (spiking && p.spike) {
      const near = p.hx * lead.x + p.hy * lead.y + p.hz * lead.z;
      // A solid core with a soft edge. A spike node starts at three
      // quarters of the radius, so a bell-shaped weight only carried the
      // node or two at its very centre out through the shell: at a pulse's
      // peak three nodes cleared it, the furthest by 9%.
      const bell = Math.exp(-(1 - near) / PLEXUS_THORNS.width);
      const u = Math.min(1, Math.max(0, (bell - 0.1) / 0.5));
      const wgt = u * u * (3 - 2 * u);
      if (wgt > 0.001) {
        const d = Math.sqrt(sx * sx + sy * sy + sz * sz) || 1e-6;
        const tip = S.radius * (1 + PLEXUS_THORNS.length * (0.6 + 0.4 * p.heat));
        const to = d + (tip - d) * Math.min(1, S.burst) * wgt;
        const k = to / d;
        sx *= k; sy *= k; sz *= k;
        p.out = Math.max(0, to / S.radius - 1);
      }
    }
    const rx = sx * cosY + sz * sinY;
    let rz = -sx * sinY + sz * cosY;
    const ry = sy * cosX - rz * sinX;
    rz = sy * sinX + rz * cosX + S.z;
    const persp = FOCAL / (FOCAL - rz);
    p.px = cx + rx * persp * sR;
    p.py = cy + ry * persp * sR;
    p.pd = Math.max(0, Math.min(1, (rz + 1) / 2));
    p.ps = p.size * persp * sizeScale * breath;
    p.reg = patch.base + (S.lobes[p.lobeA] * p.lobeW + S.lobes[p.lobeB] * (1 - p.lobeW) - patch.base) * S.look.lobes;
    p.en = energyOn ? energyAt(S, p.hx, p.hy, p.hz) * S.look.energy : 0;
    p.pa = Math.min(0.95, (PA0 + PA1 * p.pd) * bright * (p.reg + p.en) * S.look.dots * S.look.exposure * (1 + PLEXUS_THORNS.glow * p.out));
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
    q.reg = patch.base + (S.lobes[q.lobeA] * q.lobeW + S.lobes[q.lobeB] * (1 - q.lobeW) - patch.base) * S.look.lobes;
    q.en = energyOn ? energyAt(S, q.hx, q.hy, q.hz) * S.look.energy : 0;
    q.pa = Math.min(0.95, (PA0 + PA1 * q.pd) * bright * (q.reg + q.en) * S.look.dots * S.look.exposure);
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
      const ia = S.pairs[i * 2];
      const ic = S.pairs[i * 2 + 1];
      // Thinned by pair, not by position in the list: the list is rebuilt
      // every few frames, and a line chosen by index would blink.
      if (wires < 1 && pairHash(ia, ic) >= wires) continue;
      const a = P[ia];
      const c = P[ic];
      const ddx = a.x - c.x, ddy = a.y - c.y, ddz = a.z - c.z;
      const sq = ddx * ddx + ddy * ddy + ddz * ddz;
      const rmean = Math.max(LINK_RMEAN_FLOOR, (a.home + c.home) * 0.5);
      // Reach scales with the orb, or contracting the body triples the link
      // count: idle once drew 20,780 lines where listening drew 7,400.
      // A thrown node reaches further, so its thorn is strung to more of the
      // surface and reads as a fan. Only thrown nodes: lengthening every
      // line with the voice roughly doubled the line count under loud
      // speech and burned the whole body out (74% of it clipped).
      const thrown = Math.min(1, Math.max(a.out, c.out) * 6);
      const reach = LINK_DIST * rmean * S.radius * speechReach * (1 + PLEXUS_THORNS.reach * thrown);
      const pairSq = reach * reach;
      if (sq > pairSq) continue;
      const near = 1 - Math.sqrt(sq / pairSq) * 0.75;
      const depth = (a.pd + c.pd) / 2;
      // Two layers. Every line carries the fine mesh, lit by the slow
      // patches only; a fixed share of lines can also carry a current's
      // light, strongest where both ends sit in it. So the mesh is there
      // at all times, and the bright accents travel over it.
      const shade = near * (0.118 + 0.155 * depth);
      const mesh = shade * bright * S.links * (a.reg + c.reg) * 0.5;
      const lit = a.en > 0.01 && c.en > 0.01 && accentHash(ia, ic) < PLEXUS_ENERGY.accent
        ? shade * PLEXUS_ENERGY.gain * Math.sqrt(a.en * c.en)
        : 0;
      const alpha = (mesh + lit) * (1 + PLEXUS_THORNS.glow * Math.max(a.out, c.out));
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
      ctx.strokeStyle = ramp[Math.min(rampTop, 3 + b * 3)];
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
    rampBuckets[Math.min(rampTop, (u * RAMP_STEPS) | 0)].push(q);
  }
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  for (let i = 0; i < RAMP_STEPS; i++) {
    const bucket = rampBuckets[i];
    if (!bucket.length) continue;
    ctx.fillStyle = ramp[i];
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
  ctx.drawImage(edgeMask(w, h, PLEXUS_THORNS.edge), 0, 0);
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

