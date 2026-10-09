/**
 * The Memory orb's layout, without three.js (so it can be tested).
 *
 * Facts sit on a sphere like Sage's orb: About you, Interests and Other are
 * sectors of the shell (120 degrees each, topics at their own latitudes);
 * About Sage is the core. Each fact's place is seeded from its id, so adding
 * or forgetting one never reshuffles the rest. Links: each fact joins its two
 * nearest facts in the same topic (the local web), plus up to three facts
 * that say something similar (word overlap, TF-IDF cosine).
 */

import type { MemoryFact } from './api';

export type RegionId = 'me' | 'sage' | 'interests' | 'other';
export type Vec3 = [number, number, number];

export const REGIONS: Record<RegionId, { name: string; topics: string[] }> = {
  me: { name: 'About you', topics: ['personal', 'people', 'school', 'research', 'routines'] },
  sage: { name: 'About Sage', topics: ['Sage', 'answer style'] },
  interests: { name: 'Interests', topics: ['games', 'music', 'stories & shows', 'sports', 'interests'] },
  other: { name: 'Other', topics: ['computer', 'other'] },
};

// Sage's cyan mixed with a colour per region (the mockup's "Neural orb").
export const REGION_COLORS: Record<RegionId, string> = {
  me: '#8cc3bc',
  sage: '#22d3ee',
  interests: '#65b6ec',
  other: '#35d7b3',
};

export const CORE_TOPICS = new Set(REGIONS.sage.topics);

const REGION_OF: Record<string, RegionId> = {};
for (const [id, def] of Object.entries(REGIONS) as [RegionId, { topics: string[] }][]) {
  for (const t of def.topics) REGION_OF[t] = id;
}

/** A fact with no topic yet (just added, not tagged) shows under "other". */
export const topicOf = (topic: string | undefined): string => (topic && REGION_OF[topic] ? topic : 'other');
export const regionOf = (topic: string | undefined): RegionId => REGION_OF[topicOf(topic)];

const at = (lon: number, lat: number): Vec3 => {
  const a = (lon * Math.PI) / 180, b = (lat * Math.PI) / 180;
  return [Math.sin(a) * Math.cos(b), Math.sin(b), Math.cos(a) * Math.cos(b)];
};

/** Shell topics: directions. Core topics: offsets from the centre. */
export const TOPIC_DIR: Record<string, Vec3> = {
  personal: at(0, 25), people: at(30, 52), school: at(-35, 8), research: at(28, -12), routines: at(-8, -42),
  games: at(100, 22), music: at(128, 50), sports: at(150, 5), 'stories & shows': at(112, -28), interests: at(142, -40),
  computer: at(232, 8), other: at(262, 42),
  Sage: [0.13, 0.04, 0], 'answer style': [-0.13, -0.04, 0],
};

export const REGION_DIR: Record<RegionId, Vec3> = { me: at(0, 5), sage: [0, 0, 0], interests: at(125, 0), other: at(245, 18) };

export interface GraphFact {
  id: string;
  text: string;
  topic: string;
  region: RegionId;
  pinned: boolean;
  private: boolean;
  isNew: boolean;
  source: string;
  createdAt: number;
}

export function toGraphFact(f: MemoryFact): GraphFact {
  const topic = topicOf(f.topic);
  return {
    id: f.id, text: f.text, topic, region: regionOf(topic), pinned: f.pinned, private: f.private,
    isNew: f.pending, source: f.source, createdAt: f.created_at,
  };
}

// -- seeded randomness -------------------------------------------------------

export function hashSeed(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  return (h >>> 0) % 2147483646 + 1;
}

export function seeded(seed: number): () => number {
  let s = seed;
  return () => (s = (s * 16807) % 2147483647) / 2147483647;
}

export function gaussian(rnd: () => number): number {
  let u = 0, v = 0;
  while (!u) u = rnd();
  while (!v) v = rnd();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

// -- shape -------------------------------------------------------------------

const len = (v: Vec3) => Math.hypot(v[0], v[1], v[2]);
const scale = (v: Vec3, k: number): Vec3 => [v[0] * k, v[1] * k, v[2] * k];
const add = (a: Vec3, b: Vec3): Vec3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
export const normalize = (v: Vec3): Vec3 => scale(v, 1 / (len(v) || 1));

/** ~1 inside a fold of the orb's surface, ~0 on a ridge. */
export function fold(d: Vec3): number {
  const n = Math.sin(13 * d[0] + 3 * Math.sin(5 * d[1] + 1)) * Math.sin(12 * d[2] + 3 * Math.sin(6 * d[0]))
    * Math.sin(11 * d[1] + 2.5 * Math.sin(7 * d[2]));
  return Math.pow(1 - Math.abs(n), 6);
}

/** A point on (frac = 1) or near the orb's shell in direction *dir*. */
export function onShell(dir: Vec3, frac: number): Vec3 {
  const d = normalize(dir);
  return scale(d, (1 - 0.03 * fold(d)) * frac);
}

export interface Hub { topic: string; region: RegionId; pos: Vec3; n: number }

export interface Layout {
  positions: Vec3[];
  hubs: Hub[];
  /** How much wider the core is than at ~120 Sage facts (1 to 1.7). */
  coreSpread: number;
  /** How far unpinned core facts are dimmed once the core is crowded. */
  coreDim: number;
}

export function layout(facts: GraphFact[]): Layout {
  const coreN = facts.filter((f) => CORE_TOPICS.has(f.topic)).length;
  const coreSpread = Math.min(1.7, Math.max(1, Math.sqrt(coreN / 120)));
  const coreDim = Math.min(0.45, Math.max(0, (coreN - 150) / 700));
  const positions = facts.map((f) => {
    const rnd = seeded(hashSeed(f.id));
    const jitter: Vec3 = [gaussian(rnd), gaussian(rnd), gaussian(rnd)];
    if (CORE_TOPICS.has(f.topic)) {
      const r = (0.12 + 0.24 * Math.cbrt(rnd())) * coreSpread;
      return add(TOPIC_DIR[f.topic], scale(normalize(jitter), r));
    }
    return onShell(add(normalize(TOPIC_DIR[f.topic]), scale(jitter, 0.17)), 1 + 0.025 * rnd());
  });
  const hubs: Hub[] = [];
  for (const topic of Object.keys(TOPIC_DIR)) {
    const n = facts.filter((f) => f.topic === topic).length;
    if (!n) continue;
    const pos = CORE_TOPICS.has(topic) ? TOPIC_DIR[topic] : onShell(TOPIC_DIR[topic], 1);
    hubs.push({ topic, region: regionOf(topic), pos, n });
  }
  return { positions, hubs, coreSpread, coreDim };
}

// -- links -------------------------------------------------------------------

const STOP = new Set(`a an the and or of to in on for with is are was were be been it its this that these those as at by
from user users he his him she her they their them mark sage prefers prefer wants want interested interest about into
especially including include includes has have had uses use using also than then more most very can may should would
not no only such other which who when where what how while rather`.split(/\s+/));

export function tokens(text: string): string[] {
  return (text.toLowerCase().match(/[a-z0-9]+/g) ?? []).filter((w) => w.length > 2 && !STOP.has(w));
}

export type Link = [number, number, number];

/**
 * Up to *perFact* most similar facts for each fact, cosine >= *min*,
 * deduplicated as [i, j, score] with i < j. Uses an inverted index, so only
 * pairs that share a word are scored.
 */
export function similarLinks(texts: string[], perFact = 3, min = 0.28): Link[] {
  const docs = texts.map(tokens);
  const df = new Map<string, number>();
  for (const d of docs) for (const w of new Set(d)) df.set(w, (df.get(w) ?? 0) + 1);
  const n = docs.length;
  const vecs = docs.map((d) => {
    const tf = new Map<string, number>();
    for (const w of d) tf.set(w, (tf.get(w) ?? 0) + 1);
    const v = new Map<string, number>();
    let norm = 0;
    for (const [w, c] of tf) {
      const x = (1 + Math.log(c)) * Math.log(n / (df.get(w) ?? 1));
      v.set(w, x);
      norm += x * x;
    }
    norm = Math.sqrt(norm) || 1;
    for (const [w, x] of v) v.set(w, x / norm);
    return v;
  });
  const postings = new Map<string, [number, number][]>();
  vecs.forEach((v, i) => { for (const [w, x] of v) { if (x > 0) (postings.get(w) ?? postings.set(w, []).get(w)!).push([i, x]); } });
  const out = new Map<string, Link>();
  vecs.forEach((v, i) => {
    const dot = new Map<number, number>();
    for (const [w, x] of v) for (const [j, y] of postings.get(w) ?? []) if (j !== i) dot.set(j, (dot.get(j) ?? 0) + x * y);
    [...dot].filter(([, s]) => s >= min).sort((a, b) => b[1] - a[1]).slice(0, perFact).forEach(([j, s]) => {
      const a = Math.min(i, j), b = Math.max(i, j);
      out.set(`${a}-${b}`, [a, b, Math.round(s * 100) / 100]);
    });
  });
  return [...out.values()];
}

/** Each fact joined to its two nearest facts in the same topic. */
export function webLinks(facts: GraphFact[], positions: Vec3[]): [number, number][] {
  const byTopic = new Map<string, number[]>();
  facts.forEach((f, i) => (byTopic.get(f.topic) ?? byTopic.set(f.topic, []).get(f.topic)!).push(i));
  const seen = new Set<string>();
  const out: [number, number][] = [];
  for (const members of byTopic.values()) {
    for (const i of members) {
      const p = positions[i];
      const near = members.filter((j) => j !== i)
        .map((j) => [j, (positions[j][0] - p[0]) ** 2 + (positions[j][1] - p[1]) ** 2 + (positions[j][2] - p[2]) ** 2] as const)
        .sort((a, b) => a[1] - b[1]).slice(0, 2);
      for (const [j] of near) {
        const key = `${Math.min(i, j)}-${Math.max(i, j)}`;
        if (!seen.has(key)) { seen.add(key); out.push([Math.min(i, j), Math.max(i, j)]); }
      }
    }
  }
  return out;
}

/**
 * Points along a link: hugging the surface when the two facts are close,
 * bending through the inside (a fibre tract) when they are far apart.
 */
export function curve(a: Vec3, b: Vec3): Vec3[] {
  const dist = Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
  const far = dist > 0.55, steps = far ? 14 : 6;
  const pts: Vec3[] = [];
  const ctrl = scale(add(a, b), far ? 0.22 : 0.5);
  for (let k = 0; k <= steps; k++) {
    const t = k / steps;
    if (far) {
      pts.push(add(add(scale(a, (1 - t) ** 2), scale(ctrl, 2 * t * (1 - t))), scale(b, t * t)));
    } else {
      const q: Vec3 = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
      pts.push(scale(normalize(q), len(a) * (1 - t) + len(b) * t + 0.012));
    }
  }
  return pts;
}
