import { describe, expect, it } from 'vitest';
import type { MemoryFact } from './api';
import { CORE_TOPICS, curve, layout, regionOf, similarLinks, toGraphFact, topicOf, webLinks, type GraphFact, type Vec3 } from './memory-graph';

const fact = (id: string, text: string, topic: string, extra: Partial<MemoryFact> = {}): GraphFact =>
  toGraphFact({
    id, text, topic, source: 'auto', trust: 'auto', created_at: 0, day: '', pinned: false, private: false,
    pending: false, removed_at: null, removed_reason: '', ...extra,
  });

describe('regions', () => {
  it('maps the tagger topics to the four regions', () => {
    expect(regionOf('school')).toBe('me');
    expect(regionOf('answer style')).toBe('sage');
    expect(regionOf('stories & shows')).toBe('interests');
    expect(regionOf('computer')).toBe('other');
  });

  it('puts a fact with no topic yet under "other"', () => {
    expect(topicOf('')).toBe('other');
    expect(topicOf(undefined)).toBe('other');
    expect(topicOf('something new')).toBe('other');
  });
});

describe('layout', () => {
  const facts = [
    fact('a1', 'Studies civil engineering', 'school'),
    fact('a2', 'Sage runs on Ollama', 'Sage'),
    fact('a3', 'Likes Monster Hunter', 'games'),
  ];

  it('keeps each fact where it was when others are added', () => {
    const before = layout(facts).positions[0];
    const after = layout([...facts, fact('b9', 'Owns a cat', 'personal')]).positions[0];
    expect(after).toEqual(before);
  });

  it('puts About Sage in the core and everything else on the shell', () => {
    const { positions } = layout(facts);
    const r = positions.map((p) => Math.hypot(...p));
    expect(r[1]).toBeLessThan(0.7);
    expect(r[0]).toBeGreaterThan(0.9);
    expect(r[2]).toBeGreaterThan(0.9);
  });

  it('widens and dims a crowded core, but not a small one', () => {
    const small = layout(facts);
    expect(small.coreSpread).toBe(1);
    expect(small.coreDim).toBe(0);
    const many = Array.from({ length: 600 }, (_, i) => fact(`s${i}`, `Sage fact ${i}`, i % 2 ? 'Sage' : 'answer style'));
    const big = layout(many);
    expect(big.coreSpread).toBeGreaterThan(1.5);
    expect(big.coreDim).toBeGreaterThan(0.3);
    expect(many.every((f) => CORE_TOPICS.has(f.topic))).toBe(true);
  });

  it('has one hub per topic in use, counting its facts', () => {
    const hubs = layout([...facts, fact('a4', 'Plays chess', 'games')]).hubs;
    expect(hubs.find((h) => h.topic === 'games')?.n).toBe(2);
    expect(hubs.some((h) => h.topic === 'music')).toBe(false);
  });
});

describe('links', () => {
  it('links facts that say the same thing, not unrelated ones', () => {
    // Word weights need a corpus: rare shared words are what make two facts alike.
    const background = ['Plays chess on weekends', 'Listens to OPM playlists', 'Watches Formula 1 highlights',
      'Uses Opera GX as a browser', 'Prefers short answers', 'Has a class on Tuesdays', 'Owns a Maono microphone'];
    const links = similarLinks([
      'Owns AirPods 2 and finds the battery life frustrating',
      'AirPods 2 battery life has been a frustration',
      'Studies civil engineering at NU Laguna',
      ...background,
    ]);
    expect(links.map(([i, j]) => [i, j])).toEqual([[0, 1]]);
  });

  it('joins each fact to its nearest facts in the same topic only', () => {
    const facts = [fact('x1', 'a', 'games'), fact('x2', 'b', 'games'), fact('x3', 'c', 'games'), fact('y1', 'd', 'music')];
    const links = webLinks(facts, layout(facts).positions);
    expect(links.length).toBeGreaterThan(0);
    expect(links.every(([i, j]) => facts[i].topic === facts[j].topic)).toBe(true);
  });

  it('draws a curve from one fact to the other', () => {
    const pts = curve([1, 0, 0], [0, 0, 1]);
    expect(pts[0][0]).toBeCloseTo(1, 1);
    expect(pts[pts.length - 1][2]).toBeCloseTo(1, 1);
  });

  // 10 Oct: the ends sat 0.012 off the shell, so links missed their dots.
  it.each([
    [[0.9, 0.1, 0.2], [0.8, 0.3, 0.25]], // close: over the shell
    [[1, 0, 0], [-0.2, 0.1, 0.95]], // far: through the inside
  ] as [Vec3, Vec3][])('starts and ends exactly on the two facts', (a, b) => {
    const pts = curve(a, b);
    expect(pts[0]).toEqual(a);
    expect(pts[pts.length - 1]).toEqual(b);
  });

  it('a close link bows out over the facts between, smoothly', () => {
    const a: Vec3 = [1, 0, 0], b: Vec3 = [0.92, 0.39, 0];
    const pts = curve(a, b);
    const r = (v: Vec3) => Math.hypot(v[0], v[1], v[2]);
    expect(r(pts[Math.floor(pts.length / 2)])).toBeGreaterThan(1);
    // no corners: consecutive pieces turn only a little
    for (let k = 1; k < pts.length - 1; k++) {
      const u = pts[k].map((x, i) => x - pts[k - 1][i]), w = pts[k + 1].map((x, i) => x - pts[k][i]);
      const cos = (u[0] * w[0] + u[1] * w[1] + u[2] * w[2]) / (Math.hypot(...u) * Math.hypot(...w));
      expect(cos).toBeGreaterThan(0.97);
    }
  });

  it('a far link uses enough points to look curved', () => {
    expect(curve([1, 0, 0], [-0.5, 0.5, 0.7]).length).toBeGreaterThan(30);
  });
});
