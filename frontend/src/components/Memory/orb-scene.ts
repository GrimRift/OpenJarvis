/**
 * The Memory orb's three.js scene. Owns the renderer, the points, links and
 * sparks, the topic/region labels (DOM, placed each frame), picking and the
 * camera. React (MemoryOrb.tsx) owns the data and the panels.
 *
 * Cost: one canvas, drawn at most 60 times a second, only while the Memory
 * page is open and the window is visible; dispose() frees the GPU context.
 */

import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import {
  CORE_TOPICS, REGIONS, REGION_COLORS, REGION_DIR, curve, fold, gaussian, layout, onShell, seeded, similarLinks, webLinks,
  type GraphFact, type Hub, type Link, type RegionId, type Vec3,
} from '../../lib/memory-graph';

const v3 = (p: Vec3) => new THREE.Vector3(p[0], p[1], p[2]);

// A per-point random number from its position (no extra attribute).
const HASH = /* glsl */ `
  float hash(vec3 p) { return fract(sin(dot(p, vec3(12.9898, 78.233, 37.719))) * 43758.5453); }`;

// Shockwaves, like the voice orb's wake ripple but bigger: A opens the Brain
// (from the core, everything pushed out as the front passes, the orb kicks
// in size), B pulses from a clicked fact every 3 s. w = origin + progress
// (0..1, <0 none); k = max radius, front width, push, strength.
const WAVE = /* glsl */ `
  uniform vec4 uWaveA, uWaveAK, uWaveB, uWaveBK;
  uniform float uKick;
  float waveAmp(vec3 p, vec4 w, vec4 k) {
    if (w.w < 0.0 || w.w >= 1.0) return 0.0;
    float front = k.x * (1.0 - (1.0 - w.w) * (1.0 - w.w));
    float d = distance(p, w.xyz);
    return exp(-pow((d - front) / k.y, 2.0)) * pow(1.0 - w.w, 1.3) * k.w;
  }
  vec3 waved(vec3 p, out float amp) {
    float a = waveAmp(p, uWaveA, uWaveAK), b = waveAmp(p, uWaveB, uWaveBK);
    amp = a + b;
    vec3 q = p + normalize(p - uWaveA.xyz + 1e-4) * a * uWaveAK.z + normalize(p - uWaveB.xyz + 1e-4) * b * uWaveBK.z;
    return q * uKick;
  }`;

const VERTEX = /* glsl */ `
  attribute vec3 color; attribute float size; attribute vec4 flags; // pinned, new, dim, selected
  uniform float uTime, uPR;
  varying vec3 vColor; varying vec4 vFlags; varying float vRim; varying float vPhase; varying float vBeat;
  varying float vTwinkle; varying float vStar; varying float vWave;
  ${HASH}
  ${WAVE}
  void main() {
    float wv;
    vec3 pos = waved(position, wv);
    vWave = wv;
    vRim = 1.0;
    vPhase = fract(uTime * 0.55 + position.x * 1.7 + position.z);
    vBeat = 0.5 + 0.5 * sin(uTime * 2.2 + position.z * 4.0);
    // Dust twinkles like the voice orb's particles: each on its own seed and
    // pace (0.4 + 0.6 sin), a few of them sharper, like stars.
    float h = hash(position);
    vTwinkle = 0.4 + 0.6 * sin(h * 6.2832 + uTime * (0.6 + 1.6 * fract(h * 7.31)));
    vStar = step(0.92, fract(h * 13.7));
    #ifdef RIM
    vRim = 1.0 - abs(normalize(normalMatrix * position).z);
    #endif
    vec4 mv = modelViewMatrix * vec4(pos, 1.0);
    float s = size * (1.0 + 1.6 * wv);
    if (flags.x > 0.5) s *= 2.2 + 0.3 * sin(uTime * 2.2 + position.z * 4.0);
    if (flags.y > 0.5) s *= 2.4;
    if (flags.w > 0.5) s *= flags.x > 0.5 ? 1.35 : 2.2; // a pinned one is already large
    #ifdef SOFT
    s *= 1.0 + 0.6 * vStar;
    #endif
    gl_PointSize = s * uPR * (4.0 / -mv.z);
    gl_Position = projectionMatrix * mv;
    vColor = color; vFlags = flags;
  }`;

const FRAGMENT = /* glsl */ `
  uniform float uTime;
  varying vec3 vColor; varying vec4 vFlags; varying float vRim; varying float vPhase; varying float vBeat;
  varying float vTwinkle; varying float vStar; varying float vWave;
  void main() {
    vec2 q = gl_PointCoord - 0.5;
    float d = length(q);
    if (d > 0.5) discard;
    float core = smoothstep(0.2, 0.0, d);
    float glow = smoothstep(0.5, 0.0, d); glow *= glow;
    vec3 c = vColor;
    float a = core + glow * 0.5;
    #ifdef SOFT
    float soft = smoothstep(0.5, 0.0, d);
    a = soft * soft * 0.8;
    // a star: a small bright centre and a faint four-point glint
    float glint = vStar * (smoothstep(0.1, 0.0, d) * 1.6
      + (smoothstep(0.05, 0.0, abs(q.x)) + smoothstep(0.05, 0.0, abs(q.y))) * smoothstep(0.5, 0.0, d) * 0.5);
    a = (a + glint) * vTwinkle;
    #endif
    if (vFlags.x > 0.5) {
      // Pinned: drawn ~2.2x. A hot core, soft rays turning slowly, ripples
      // running outward and a soft glow that beats (rings removed, user 10 Oct).
      float ang = atan(q.y, q.x);
      float hot = smoothstep(0.13, 0.0, d);
      float rays = pow(abs(sin(ang * 4.0 + uTime * 0.35)), 10.0) * smoothstep(0.42, 0.1, d) * 0.4;
      float ripple = (0.5 + 0.5 * sin(d * 60.0 - uTime * 3.2)) * smoothstep(0.08, 0.14, d) * smoothstep(0.36, 0.22, d) * 0.35;
      float halo = smoothstep(0.5, 0.0, d); halo = halo * halo * (0.18 + 0.18 * vBeat);
      vec3 warm = vec3(1.0, 0.92, 0.72);
      c = mix(c, warm, 0.6);
      a = hot * 1.05 + rays + ripple + halo;
      // the outer glow leans cyan, like Sage's orb
      c = mix(c, vec3(0.55, 0.95, 1.0), smoothstep(0.25, 0.45, d) * 0.6);
    }
    if (vFlags.y > 0.5) {
      // drawn 2.4x larger: a small core plus a ring that expands and fades
      float small = smoothstep(0.09, 0.0, d);
      float wave = smoothstep(0.045, 0.0, abs(d - vPhase * 0.48)) * (1.0 - vPhase);
      c = mix(c, vec3(0.65, 1.0, 1.0), 0.35);
      a = small * 1.4 + glow * 0.25 + wave * 1.3;
    }
    if (vFlags.w > 0.5) a += glow * 0.8;
    float wave = clamp(vWave, 0.0, 2.0);
    a += wave * (core + glow) * 1.4;
    c = mix(c, vec3(0.8, 1.0, 1.0), min(wave, 1.0) * 0.5);
    a *= mix(1.0, 0.08, vFlags.z);
    #ifdef RIM
    a *= mix(0.45, 1.5, pow(vRim, 1.5));
    #endif
    gl_FragColor = vec4(c * a, a);
  }`;

// Links breathe with the voice orb's rhythm (0.75 + 0.25 sin, ~5 s) and
// carry pulses: a glow that runs along each link at its own phase.
const LINE_VERTEX = /* glsl */ `
  attribute vec3 color; attribute vec2 along; // x: 0..1 along the link, y: the link's phase
  uniform float uTime;
  varying vec3 vColor; varying float vPulse;
  ${WAVE}
  void main() {
    float wv;
    vec3 pos = waved(position, wv);
    float w = fract(uTime * 0.28 + along.y);
    float pulse = exp(-pow((along.x - w) * 9.0, 2.0)) * smoothstep(0.0, 0.1, w) * smoothstep(1.0, 0.9, w);
    vPulse = pulse;
    vColor = color * (0.75 + 0.25 * sin(uTime * 1.2)) * (1.0 + 3.2 * pulse + 4.0 * wv);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(pos, 1.0);
  }`;

const LINE_FRAGMENT = /* glsl */ `
  varying vec3 vColor; varying float vPulse;
  void main() {
    vec3 c = mix(vColor, vec3(0.75, 1.0, 1.0) * length(vColor), vPulse * 0.4);
    gl_FragColor = vec4(c, 1.0);
  }`;

function makePoints(count: number): THREE.BufferGeometry {
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(count * 3), 3));
  g.setAttribute('color', new THREE.BufferAttribute(new Float32Array(count * 3), 3));
  g.setAttribute('size', new THREE.BufferAttribute(new Float32Array(count), 1));
  g.setAttribute('flags', new THREE.BufferAttribute(new Float32Array(count * 4), 4));
  return g;
}

const markDirty = (g: THREE.BufferGeometry) => {
  for (const key of ['position', 'color', 'size', 'flags']) g.getAttribute(key).needsUpdate = true;
};

interface Label { el: HTMLDivElement; pos: THREE.Vector3; region: RegionId; kind: 'hub' | 'region'; w: number; h: number; o: number; x: number; y: number; facing: number }
interface Spark { c: THREE.Vector3[] | null; t: number; v: number }

export interface OrbCallbacks {
  onHover: (fact: GraphFact | null, x: number, y: number) => void;
  onSelect: (fact: GraphFact | null) => void;
}

export class OrbScene {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera: THREE.PerspectiveCamera;
  private controls: OrbitControls;
  private uniforms = {
    uTime: { value: 0 }, uPR: { value: 1 }, uKick: { value: 1 },
    uWaveA: { value: new THREE.Vector4(0, 0, 0, -1) }, uWaveAK: { value: new THREE.Vector4(1.35, 0.16, 0.14, 1.6) },
    uWaveB: { value: new THREE.Vector4(0, 0, 0, -1) }, uWaveBK: { value: new THREE.Vector4(2.4, 0.14, 0.07, 1.1) },
  };
  // Shockwave clocks (ms since page load; -1 = none) and the click pulse's fact.
  private waveA = -1;
  private waveB = -1;
  private pulseFact = -1;
  private nextPulse = 0;
  private opened = false;
  private openIn = 0; // frames until the opening wave starts
  private pointMat: THREE.ShaderMaterial;
  private rimMat: THREE.ShaderMaterial;
  private softMat: THREE.ShaderMaterial;
  private lineMat = new THREE.ShaderMaterial({
    uniforms: this.uniforms, vertexShader: LINE_VERTEX, fragmentShader: LINE_FRAGMENT,
    transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
  });
  private factGeo = makePoints(1);
  private factPts: THREE.Points;
  private hubGeo = makePoints(1);
  private hubPts: THREE.Points;
  private coreGeo = makePoints(1);
  private corePts: THREE.Points;
  private sparkGeo = makePoints(1);
  private sparkPts: THREE.Points;
  private lines: THREE.LineSegments | null = null;
  private hiLines: THREE.LineSegments | null = null;

  private facts: GraphFact[] = [];
  private pos: THREE.Vector3[] = [];
  private hubs: Hub[] = [];
  private sim: Link[] = [];
  private web: [number, number][] = [];
  private neighbours: [number, number][][] = [];
  private coreDim = 0;
  private coreSpread = 1;
  private curves: THREE.Vector3[][] = [];
  private sparks: Spark[] = [];
  private flashes: { p: THREE.Vector3; life: number }[] = [];
  private nextFlash = 0;
  private labels: Label[] = [];

  private query = '';
  private hidden = new Set<RegionId>();
  private selected = -1;
  // p1 null: only the orbit centre moves (the camera stays where the user put it)
  private flight: { t0: number; p0: THREE.Vector3; p1: THREE.Vector3 | null; q0: THREE.Vector3; q1: THREE.Vector3 } | null = null;
  private idleTimer = 0;
  private raf = 0;
  private lastFrame = 0;
  private lastDraw = 0;
  private down: [number, number] | null = null;
  private lastHover = 0;
  private readonly calm = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  private readonly ro: ResizeObserver;
  private readonly host: HTMLElement;
  private readonly labelHost: HTMLElement;
  private readonly cb: OrbCallbacks;

  constructor(host: HTMLElement, labelHost: HTMLElement, cb: OrbCallbacks) {
    this.host = host;
    this.labelHost = labelHost;
    this.cb = cb;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'low-power' });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(0x0a0a0b, 1);
    this.uniforms.uPR.value = this.renderer.getPixelRatio();
    host.appendChild(this.renderer.domElement);
    this.renderer.domElement.style.display = 'block';

    this.camera = new THREE.PerspectiveCamera(45, 1, 0.05, 50);
    this.camera.position.set(2.6, 1.0, 2.6);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.minDistance = 1.4;
    this.controls.maxDistance = 7;
    this.controls.autoRotate = !this.calm;
    this.controls.autoRotateSpeed = 0.45;
    this.controls.addEventListener('start', () => { this.controls.autoRotate = false; window.clearTimeout(this.idleTimer); });
    this.controls.addEventListener('end', () => this.resumeRotationLater());
    // Middle button: turn around the point under the cursor (onDown/onMove),
    // not the centre. OrbitControls would dolly with it; the wheel still does.
    this.controls.mouseButtons = { LEFT: THREE.MOUSE.ROTATE, MIDDLE: -1 as THREE.MOUSE, RIGHT: THREE.MOUSE.PAN };

    const shader = { uniforms: this.uniforms, vertexShader: VERTEX, fragmentShader: FRAGMENT, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending };
    this.pointMat = new THREE.ShaderMaterial(shader);
    this.rimMat = new THREE.ShaderMaterial({ ...shader, defines: { RIM: 1, SOFT: 1 } });
    this.softMat = new THREE.ShaderMaterial({ ...shader, defines: { SOFT: 1 } });

    this.addShellDust();
    this.corePts = new THREE.Points(this.coreGeo, this.softMat);
    this.hubPts = new THREE.Points(this.hubGeo, this.pointMat);
    this.factPts = new THREE.Points(this.factGeo, this.pointMat);
    this.sparkPts = new THREE.Points(this.sparkGeo, this.pointMat);
    this.scene.add(this.corePts, this.hubPts, this.factPts, this.sparkPts);

    const el = this.renderer.domElement;
    el.addEventListener('pointerdown', this.onDown);
    el.addEventListener('pointerup', this.onUp);
    el.addEventListener('pointermove', this.onMove);
    el.addEventListener('pointerleave', this.onLeave);
    document.addEventListener('visibilitychange', this.onVisibility);
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(host);
    this.resize();
    this.raf = requestAnimationFrame(this.frame);
    // For probes over CDP (Sage-Staging-Tools/test); dev builds only.
    if (import.meta.env.DEV) (window as unknown as { __memoryOrb?: OrbScene }).__memoryOrb = this;
  }

  /** Where fact *id* is on screen, in page pixels (probes and tests). */
  screenOf(id: string): [number, number] | null {
    const k = this.facts.findIndex((f) => f.id === id);
    if (k < 0) return null;
    const rect = this.renderer.domElement.getBoundingClientRect();
    const v = this.pos[k].clone().project(this.camera);
    return [rect.left + (v.x * 0.5 + 0.5) * rect.width, rect.top + (-v.y * 0.5 + 0.5) * rect.height];
  }

  // -- data --------------------------------------------------------------

  setFacts(facts: GraphFact[]): void {
    const selectedId = this.selected >= 0 ? this.facts[this.selected]?.id : null;
    this.facts = facts;
    const lay = layout(facts);
    this.pos = lay.positions.map(v3);
    this.hubs = lay.hubs;
    this.coreDim = lay.coreDim;
    this.coreSpread = lay.coreSpread;
    this.sim = similarLinks(facts.map((f) => f.text));
    this.web = webLinks(facts, lay.positions);
    this.neighbours = facts.map(() => []);
    for (const [i, j, s] of this.sim) { this.neighbours[i].push([j, s]); this.neighbours[j].push([i, s]); }
    this.selected = selectedId ? facts.findIndex((f) => f.id === selectedId) : -1;

    this.factGeo.dispose(); this.factGeo = makePoints(Math.max(1, facts.length)); this.factPts.geometry = this.factGeo;
    this.hubGeo.dispose(); this.hubGeo = makePoints(Math.max(1, this.hubs.length)); this.hubPts.geometry = this.hubGeo;
    this.buildCore();
    const sparkCount = this.calm ? 0 : Math.round(Math.min(160, 80 + facts.length / 25));
    this.sparks = Array.from({ length: sparkCount }, (_, k) => ({ c: null, t: seeded(k + 1)(), v: 0.35 + 0.55 * seeded(k + 99)() }));
    this.flashes = Array.from({ length: this.calm ? 0 : 48 }, () => ({ p: new THREE.Vector3(), life: 0 }));
    this.sparkGeo.dispose(); this.sparkGeo = makePoints(Math.max(1, sparkCount * 3 + this.flashes.length)); this.sparkPts.geometry = this.sparkGeo;
    this.buildLabels();
    this.refresh();
    // The Brain opens with a shockwave from the core (each time it is shown:
    // the view remounts when the tab comes back).
    // Started from the frame loop once frames are on screen: the first one
    // (shader compile, building links) can take over a second, and a clock
    // started here would run the wave out before anything is seen.
    if (!this.opened && facts.length) { this.opened = true; if (!this.calm) this.openIn = 3; }
  }

  setQuery(q: string): void { this.query = q.trim().toLowerCase(); this.refresh(); }
  setHidden(hidden: Set<RegionId>): void { this.hidden = new Set(hidden); this.refresh(); }

  select(id: string | null, fly: boolean): void {
    this.selected = id ? this.facts.findIndex((f) => f.id === id) : -1;
    // A clicked fact pulses at once, then every 3 s while it stays open.
    this.pulseFact = this.calm ? -1 : this.selected;
    this.nextPulse = performance.now();
    if (this.selected >= 0) {
      this.controls.autoRotate = false;
      if (fly) {
        const p = this.pos[this.selected];
        const len = p.length();
        // A core fact: look at it from just outside the shell; a shell fact: from in front of it.
        const dir = len > 0.6 ? p.clone().normalize() : this.camera.position.clone().normalize();
        this.flyTo(dir.multiplyScalar(len > 0.6 ? 2.7 : 2.3).add(new THREE.Vector3(0, 0.25, 0)), p.clone().multiplyScalar(0.5));
      }
    } else if (fly) {
      this.flyTo(this.camera.position.clone().setLength(3.8), new THREE.Vector3());
      this.resumeRotationLater();
    }
    this.refresh();
  }

  flyToRegion(r: RegionId): void {
    if (r === 'sage') this.flyTo(this.camera.position.clone().setLength(2.1), new THREE.Vector3());
    else this.flyTo(v3(REGION_DIR[r]).normalize().multiplyScalar(3.3).add(new THREE.Vector3(0, 0.4, 0)), new THREE.Vector3());
  }

  /** Similar facts first (with their score), then others in the same topic. */
  linked(id: string): { fact: GraphFact; score: number | null }[] {
    const i = this.facts.findIndex((f) => f.id === id);
    if (i < 0) return [];
    const near = [...this.neighbours[i]].sort((a, b) => b[1] - a[1]).map(([j, s]) => ({ fact: this.facts[j], score: s }));
    const nearIds = new Set(near.map((n) => n.fact.id));
    const same = this.facts.filter((f, j) => j !== i && f.topic === this.facts[i].topic && !nearIds.has(f.id)).slice(0, 4);
    return [...near, ...same.map((fact) => ({ fact, score: null }))];
  }

  dispose(): void {
    cancelAnimationFrame(this.raf);
    window.clearTimeout(this.idleTimer);
    this.ro.disconnect();
    document.removeEventListener('visibilitychange', this.onVisibility);
    const el = this.renderer.domElement;
    el.removeEventListener('pointerdown', this.onDown);
    el.removeEventListener('pointerup', this.onUp);
    el.removeEventListener('pointermove', this.onMove);
    el.removeEventListener('pointerleave', this.onLeave);
    this.controls.dispose();
    this.scene.traverse((o) => { if (o instanceof THREE.Points || o instanceof THREE.LineSegments) o.geometry.dispose(); });
    for (const m of [this.pointMat, this.rimMat, this.softMat, this.lineMat]) m.dispose();
    for (const l of this.labels) l.el.remove();
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    el.remove();
  }

  // -- building ------------------------------------------------------------

  private addShellDust(): void {
    const n = 4500;
    const g = makePoints(n);
    const rnd = seeded(7);
    const col = new THREE.Color('#38e0f7');
    for (let k = 0; k < n; k++) {
      const d: Vec3 = [gaussian(rnd), gaussian(rnd), gaussian(rnd)];
      const p = onShell(d, 0.985 + 0.03 * rnd());
      const shade = 0.5 + 0.5 * (1 - fold(p));
      g.getAttribute('position').setXYZ(k, p[0], p[1], p[2]);
      g.getAttribute('color').setXYZ(k, col.r * shade, col.g * shade, col.b * shade);
      g.getAttribute('size').setX(k, 2.6 + 1.4 * rnd());
    }
    this.scene.add(new THREE.Points(g, this.rimMat));
  }

  private buildCore(): void {
    const n = 600;
    this.coreGeo.dispose();
    this.coreGeo = makePoints(n + 1);
    this.corePts.geometry = this.coreGeo;
    const rnd = seeded(11);
    for (let k = 0; k < n; k++) {
      const p = new THREE.Vector3(gaussian(rnd), gaussian(rnd), gaussian(rnd)).normalize().multiplyScalar(0.45 * this.coreSpread * Math.cbrt(rnd()));
      this.coreGeo.getAttribute('position').setXYZ(k, p.x, p.y, p.z);
      this.coreGeo.getAttribute('size').setX(k, 2.6 + 1.4 * rnd());
    }
    this.coreGeo.getAttribute('size').setX(n, 150); // the core's glow
  }

  private buildLabels(): void {
    for (const l of this.labels) l.el.remove();
    this.labels = [];
    const make = (text: string, pos: THREE.Vector3, region: RegionId, kind: 'hub' | 'region') => {
      const el = document.createElement('div');
      el.textContent = text;
      el.className = kind === 'region' ? 'memory-orb-label memory-orb-region' : 'memory-orb-label';
      if (kind === 'region') el.style.color = REGION_COLORS[region];
      this.labelHost.appendChild(el);
      this.labels.push({ el, pos, region, kind, w: 0, h: 0, o: 0, x: 0, y: 0, facing: 0 });
    };
    for (const h of this.hubs) make(h.topic, v3(h.pos), h.region, 'hub');
    for (const r of Object.keys(REGIONS) as RegionId[]) {
      if (!this.facts.some((f) => f.region === r)) continue;
      make(REGIONS[r].name, r === 'sage' ? new THREE.Vector3(0, 0.5 * this.coreSpread, 0) : v3(onShell(REGION_DIR[r], 1.14)), r, 'region');
    }
  }

  private color(f: GraphFact): THREE.Color {
    return new THREE.Color(REGION_COLORS[f.region]).offsetHSL((((f.topic.length * 37) % 9) - 4) / 300, 0, 0);
  }

  private matches = (f: GraphFact) => !this.query || f.text.toLowerCase().includes(this.query) || f.topic.toLowerCase().includes(this.query);
  private visible = (f: GraphFact) => !this.hidden.has(f.region);

  private refresh(): void {
    const g = this.factGeo;
    const sel = this.selected;
    const near = new Set(sel >= 0 ? this.neighbours[sel].map(([j]) => j) : []);
    this.facts.forEach((f, k) => {
      const c = this.color(f), p = this.pos[k];
      g.getAttribute('position').setXYZ(k, p.x, p.y, p.z);
      g.getAttribute('color').setXYZ(k, c.r, c.g, c.b);
      g.getAttribute('size').setX(k, 9);
      let dim = !this.visible(f) ? 1
        : this.query && !this.matches(f) ? 0.92
        : sel >= 0 && sel !== k && !near.has(k) && f.topic !== this.facts[sel].topic ? 0.75 : 0;
      if (!dim && CORE_TOPICS.has(f.topic) && !f.pinned && sel !== k && !this.query) dim = this.coreDim;
      g.getAttribute('flags').setXYZW(k, f.pinned ? 1 : 0, f.isNew ? 1 : 0, dim, sel === k ? 1 : 0);
    });
    markDirty(g);

    this.hubs.forEach((h, k) => {
      const c = new THREE.Color(REGION_COLORS[h.region]);
      this.hubGeo.getAttribute('position').setXYZ(k, h.pos[0], h.pos[1], h.pos[2]);
      this.hubGeo.getAttribute('color').setXYZ(k, c.r, c.g, c.b);
      this.hubGeo.getAttribute('size').setX(k, 30 + Math.min(16, h.n / 4));
      this.hubGeo.getAttribute('flags').setXYZW(k, 0, 0, this.hidden.has(h.region) ? 1 : this.query ? 0.9 : 0.72, 0);
    });
    markDirty(this.hubGeo);

    const cc = new THREE.Color(REGION_COLORS.sage);
    const coreN = this.coreGeo.getAttribute('size').count;
    const coreOff = this.hidden.has('sage');
    for (let k = 0; k < coreN; k++) {
      this.coreGeo.getAttribute('color').setXYZ(k, cc.r, cc.g, cc.b);
      this.coreGeo.getAttribute('flags').setXYZW(k, 0, 0, coreOff ? 1 : k === coreN - 1 ? 0.78 : 0.45, 0);
    }
    markDirty(this.coreGeo);
    this.buildLines();
  }

  private buildLines(): void {
    type Buf = { P: number[]; C: number[]; A: number[] };
    const main: Buf = { P: [], C: [], A: [] }, hi: Buf = { P: [], C: [], A: [] };
    let linkNo = 0;
    const pushTo = (B: Buf, pts: THREE.Vector3[], c: THREE.Color, k: number) => {
      // each link pulses at its own phase (golden-ratio spread: no two in step)
      const phase = (linkNo++ * 0.618034) % 1, last = pts.length - 1;
      for (let n = 0; n < last; n++) {
        const a = pts[n], b = pts[n + 1];
        B.P.push(a.x, a.y, a.z, b.x, b.y, b.z);
        B.C.push(c.r * k, c.g * k, c.b * k, c.r * k, c.g * k, c.b * k);
        B.A.push(n / last, phase, (n + 1) / last, phase);
      }
    };
    const push = (pts: THREE.Vector3[], c: THREE.Color, k: number) => pushTo(main, pts, c, k);
    const thin = Math.max(0.4, Math.min(1, Math.sqrt(450 / Math.max(1, this.facts.length))));
    const curveOf = (i: number, j: number) => curve(this.pos[i].toArray() as Vec3, this.pos[j].toArray() as Vec3).map(v3);
    this.curves = [];
    for (const [i, j] of this.web) {
      const a = this.facts[i], b = this.facts[j];
      if (!this.visible(a) || !this.visible(b)) continue;
      const pts = curveOf(i, j);
      push(pts, this.color(a), (this.query && !(this.matches(a) || this.matches(b)) ? 0.03 : 0.24) * thin);
      this.curves.push(pts);
    }
    for (const [i, j] of this.sim) {
      const a = this.facts[i], b = this.facts[j];
      if (!this.visible(a) || !this.visible(b) || (a.topic === b.topic && this.pos[i].distanceTo(this.pos[j]) < 0.3)) continue;
      const pts = curveOf(i, j);
      push(pts, this.color(a).lerp(this.color(b), 0.5), (this.query && !(this.matches(a) && this.matches(b)) ? 0.02 : 0.065) * thin);
      this.curves.push(pts);
    }
    this.lines = this.replaceLines(this.lines, main);

    const sel = this.selected;
    if (sel >= 0) {
      const glow = new THREE.Color(0.6, 0.95, 1);
      for (const [j] of this.neighbours[sel]) pushTo(hi, curveOf(sel, j), glow, 1);
      for (const [i, j] of this.web) if (i === sel || j === sel) pushTo(hi, curveOf(i, j), glow, 0.7);
    }
    this.hiLines = this.replaceLines(this.hiLines, hi);
  }

  private replaceLines(old: THREE.LineSegments | null, b: { P: number[]; C: number[]; A: number[] }): THREE.LineSegments | null {
    if (old) { this.scene.remove(old); old.geometry.dispose(); }
    if (!b.P.length) return null;
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(b.P, 3));
    g.setAttribute('color', new THREE.Float32BufferAttribute(b.C, 3));
    g.setAttribute('along', new THREE.Float32BufferAttribute(b.A, 2));
    const lines = new THREE.LineSegments(g, this.lineMat);
    this.scene.add(lines);
    return lines;
  }

  // -- per frame -------------------------------------------------------------

  private frame = (now: number): void => {
    this.raf = requestAnimationFrame(this.frame);
    if (now - this.lastDraw < 1000 / 61) return; // at most 60 fps, whatever the display
    this.lastDraw = now;
    const dt = Math.min(0.05, (now - (this.lastFrame || now)) / 1000);
    this.lastFrame = now;
    this.uniforms.uTime.value = now / 1000;
    if (this.flight) {
      const t = Math.min(1, (now - this.flight.t0) / 900);
      const k = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
      if (this.flight.p1) this.camera.position.lerpVectors(this.flight.p0, this.flight.p1, k);
      this.controls.target.lerpVectors(this.flight.q0, this.flight.q1, k);
      if (t >= 1) this.flight = null;
    }
    this.moveSparks(dt);
    this.moveWaves(now);
    this.controls.update();
    this.placeLabels();
    this.renderer.render(this.scene, this.camera);
  };

  private moveWaves(now: number): void {
    const u = this.uniforms;
    if (this.openIn > 0 && --this.openIn === 0) this.waveA = now;
    if (this.waveA >= 0) {
      const p = (now - this.waveA) / 1300;
      if (p >= 1) { this.waveA = -1; u.uWaveA.value.w = -1; u.uKick.value = 1; }
      else {
        u.uWaveA.value.w = p;
        // the orb jumps outward and settles in the first ~0.6 s
        u.uKick.value = p < 0.45 ? 1 + 0.09 * Math.sin(Math.PI * (p / 0.45)) : 1;
      }
    }
    if (this.pulseFact >= 0 && now >= this.nextPulse) {
      const o = this.pos[this.pulseFact];
      u.uWaveB.value.set(o.x, o.y, o.z, 0);
      this.waveB = now;
      this.nextPulse = now + 3000;
    }
    if (this.waveB >= 0) {
      const p = (now - this.waveB) / 1800;
      const sizes = this.factGeo.getAttribute('size');
      const linked = this.pulseFact >= 0 ? this.neighbours[this.pulseFact] : [];
      if (p >= 1) {
        this.waveB = -1; u.uWaveB.value.w = -1;
        for (const [j] of linked) sizes.setX(j, 9);
      } else {
        u.uWaveB.value.w = p;
        // its linked facts flash as the front reaches them
        const k = u.uWaveBK.value, o = u.uWaveB.value;
        const front = k.x * (1 - (1 - p) ** 2);
        for (const [j] of linked) {
          const d = this.pos[j].distanceTo(new THREE.Vector3(o.x, o.y, o.z));
          sizes.setX(j, 9 * (1 + 2.5 * Math.exp(-(((d - front) / 0.2) ** 2))));
        }
      }
      sizes.needsUpdate = true;
    }
  }

  private moveSparks(dt: number): void {
    if (!this.sparks.length) return;
    const g = this.sparkGeo;
    const P = g.getAttribute('position'), C = g.getAttribute('color'), S = g.getAttribute('size');
    const rnd = Math.random;
    const at = (c: THREE.Vector3[], t: number) => {
      const f = Math.max(0, Math.min(t, 0.999)) * (c.length - 1), n = Math.floor(f);
      return c[n].clone().lerp(c[n + 1], f - n);
    };
    const lags = [0, 0.045, 0.09], bright = [1.5, 0.7, 0.3], sizes = [8, 6, 4.5];
    this.sparks.forEach((sp, k) => {
      if (sp.c && sp.t >= 1) {
        const fl = this.flashes[this.nextFlash++ % this.flashes.length];
        fl.p.copy(sp.c[sp.c.length - 1]); fl.life = 1;
      }
      if (!sp.c || sp.t >= 1) { sp.c = this.curves.length ? this.curves[Math.floor(rnd() * this.curves.length)] : null; sp.t = 0; }
      lags.forEach((lag, m) => {
        const idx = k * 3 + m;
        if (!sp.c || this.query || sp.t - lag < 0) { S.setX(idx, 0); return; }
        const q = at(sp.c, sp.t - lag);
        const b = bright[m] * Math.min(1, Math.sin(Math.PI * Math.min(sp.t, 1)) * 2.5);
        P.setXYZ(idx, q.x, q.y, q.z);
        C.setXYZ(idx, 0.7 * b, 0.97 * b, b);
        S.setX(idx, sizes[m]);
      });
      if (sp.c) sp.t += dt * sp.v;
    });
    this.flashes.forEach((fl, k) => {
      const idx = this.sparks.length * 3 + k;
      fl.life = Math.max(0, fl.life - dt * 2.2);
      const b = fl.life * fl.life * 1.4;
      P.setXYZ(idx, fl.p.x, fl.p.y, fl.p.z);
      C.setXYZ(idx, 0.7 * b, b, b);
      S.setX(idx, this.query ? 0 : 6 + 22 * (1 - fl.life));
    });
    P.needsUpdate = C.needsUpdate = S.needsUpdate = true;
  }

  private placeLabels(): void {
    const camDir = new THREE.Vector3();
    this.camera.getWorldDirection(camDir);
    const w = this.host.clientWidth, h = this.host.clientHeight, v = new THREE.Vector3();
    for (const l of this.labels) {
      v.copy(l.pos).project(this.camera);
      l.facing = -l.pos.clone().normalize().dot(camDir);
      l.x = (v.x * 0.5 + 0.5) * w;
      l.y = (-v.y * 0.5 + 0.5) * h;
      l.el.style.transform = `translate(${l.x}px, ${l.y}px) translate(-50%, -50%)`;
      let o = l.kind === 'region' ? 0.95 : Math.max(0, Math.min(1, l.facing * 2 + 0.2));
      if (this.hidden.has(l.region)) o *= 0.15;
      if (this.query && l.kind === 'hub') o *= 0.4;
      l.o = o;
    }
    // Region names first, then the hubs facing the camera most; a label that
    // would overlap one already shown waits until the orb turns.
    const shown: number[][] = [];
    const order = [...this.labels].sort((a, b) => Number(b.kind === 'region') - Number(a.kind === 'region') || b.facing - a.facing);
    for (const l of order) {
      if (!l.w) { l.w = l.el.offsetWidth; l.h = l.el.offsetHeight; }
      const box = [l.x - l.w / 2 - 4, l.y - l.h / 2 - 2, l.x + l.w / 2 + 4, l.y + l.h / 2 + 2];
      const hit = l.o > 0.05 && shown.some((b) => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1]);
      if (hit) l.o = 0;
      else if (l.o > 0.05) shown.push(box);
      l.el.style.opacity = String(l.o);
    }
  }

  // -- interaction ---------------------------------------------------------

  private flyTo(p1: THREE.Vector3, q1: THREE.Vector3): void {
    this.controls.autoRotate = false;
    this.flight = { t0: performance.now(), p0: this.camera.position.clone(), p1, q0: this.controls.target.clone(), q1 };
  }

  private resumeRotationLater(): void {
    window.clearTimeout(this.idleTimer);
    if (this.calm) return;
    this.idleTimer = window.setTimeout(() => { if (this.selected < 0) this.controls.autoRotate = true; }, 8000);
  }

  private pick(cx: number, cy: number): number {
    const rect = this.renderer.domElement.getBoundingClientRect();
    const x0 = cx - rect.left, y0 = cy - rect.top, v = new THREE.Vector3();
    let best = -1, bd = 14 * 14;
    this.facts.forEach((f, k) => {
      if (!this.visible(f)) return;
      v.copy(this.pos[k]).project(this.camera);
      if (v.z > 1) return;
      const d = ((v.x * 0.5 + 0.5) * rect.width - x0) ** 2 + ((-v.y * 0.5 + 0.5) * rect.height - y0) ** 2;
      if (d < bd) { bd = d; best = k; }
    });
    return best;
  }

  /** The point under the cursor: a fact if one is there, else where the ray meets the orb. */
  private pointUnder(cx: number, cy: number): THREE.Vector3 {
    const k = this.pick(cx, cy);
    if (k >= 0) return this.pos[k].clone();
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ray = new THREE.Raycaster();
    ray.setFromCamera(new THREE.Vector2(((cx - rect.left) / rect.width) * 2 - 1, -((cy - rect.top) / rect.height) * 2 + 1), this.camera);
    const hit = ray.ray.intersectSphere(new THREE.Sphere(new THREE.Vector3(), 1), new THREE.Vector3());
    return hit ?? ray.ray.at(this.camera.position.distanceTo(this.controls.target), new THREE.Vector3());
  }

  /** Rotate the camera rigidly about *pivot*, so the pivot stays where it is on screen. */
  private turnAround(pivot: THREE.Vector3, dx: number, dy: number): void {
    const yaw = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), -dx * 0.006);
    const right = new THREE.Vector3(1, 0, 0).applyQuaternion(this.camera.quaternion);
    const pitch = new THREE.Quaternion().setFromAxisAngle(right, -dy * 0.006);
    const q = yaw.multiply(pitch);
    // Stop short of the poles, where "up" would flip.
    const nextUp = new THREE.Vector3(0, 1, 0).applyQuaternion(this.camera.quaternion.clone().premultiply(q));
    if (nextUp.y < 0.08) q.copy(yaw.setFromAxisAngle(new THREE.Vector3(0, 1, 0), -dx * 0.006));
    this.camera.position.sub(pivot).applyQuaternion(q).add(pivot);
    this.controls.target.sub(pivot).applyQuaternion(q).add(pivot);
    this.camera.quaternion.premultiply(q);
  }

  private pivot: { p: THREE.Vector3; x: number; y: number } | null = null;

  private onDown = (e: PointerEvent) => {
    this.down = [e.clientX, e.clientY];
    // Left drag turns around the centre: after a middle-button turn moved
    // the orbit centre, glide it back to the middle of the orb.
    if (e.button === 0 && this.controls.target.length() > 0.02) {
      this.flight = { t0: performance.now(), p0: this.camera.position.clone(), p1: null, q0: this.controls.target.clone(), q1: new THREE.Vector3() };
    }
    if (e.button === 1) {
      e.preventDefault(); // no autoscroll cursor
      this.pivot = { p: this.pointUnder(e.clientX, e.clientY), x: e.clientX, y: e.clientY };
      try { this.renderer.domElement.setPointerCapture(e.pointerId); } catch { /* pointer already gone */ }
      this.flight = null;
      this.controls.autoRotate = false;
      window.clearTimeout(this.idleTimer);
    }
  };
  private onUp = (e: PointerEvent) => {
    if (e.button === 1 && this.pivot) {
      this.pivot = null;
      try { this.renderer.domElement.releasePointerCapture(e.pointerId); } catch { /* not captured */ }
      this.resumeRotationLater();
      return;
    }
    if (!this.down || Math.hypot(e.clientX - this.down[0], e.clientY - this.down[1]) > 5) return;
    const k = this.pick(e.clientX, e.clientY);
    this.cb.onSelect(k >= 0 ? this.facts[k] : null);
  };
  private onMove = (e: PointerEvent) => {
    if (this.pivot && e.buttons & 4) {
      this.turnAround(this.pivot.p, e.clientX - this.pivot.x, e.clientY - this.pivot.y);
      this.pivot.x = e.clientX;
      this.pivot.y = e.clientY;
      return;
    }
    const now = performance.now();
    if (now - this.lastHover < 40) return;
    this.lastHover = now;
    const k = e.buttons ? -1 : this.pick(e.clientX, e.clientY);
    this.renderer.domElement.style.cursor = k >= 0 ? 'pointer' : 'grab';
    this.cb.onHover(k >= 0 ? this.facts[k] : null, e.clientX, e.clientY);
  };
  private onLeave = () => this.cb.onHover(null, 0, 0);
  private onVisibility = () => {
    cancelAnimationFrame(this.raf);
    if (!document.hidden) { this.lastFrame = 0; this.raf = requestAnimationFrame(this.frame); }
  };

  private resize(): void {
    const w = Math.max(1, this.host.clientWidth), h = Math.max(1, this.host.clientHeight);
    this.renderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    if (!this.flight && this.selected < 0) this.camera.position.setLength(w / h < 1 ? 4.6 : 3.8);
  }
}
