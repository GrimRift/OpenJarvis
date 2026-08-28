import { useEffect, useRef } from 'react';
import {
  approach,
  frameDelta,
  particleCountFor,
  stepRotation,
} from '../../lib/orb-motion';
import { useAppStore } from '../../lib/store';

export type OrbState = 'idle' | 'listening' | 'speaking';

// Single source of truth for the orb's state, shared by every component
// that renders one (the empty-state hero orb, the persistent composer orb)
// so they never drift out of sync with each other.
export function useOrbState(): OrbState {
  const activeId = useAppStore((s) => s.activeId);
  const streamState = useAppStore((s) => s.streamState);
  const voiceState = useAppStore((s) => s.voiceState);
  const audioPlaying = useAppStore((s) => s.audioPlaying);
  const isCurrentChatStreaming = streamState.isStreaming && streamState.conversationId === activeId;
  // "speaking" is reserved for actually speaking. Generating text used to
  // claim it too, so the orb looked identical whether Sage was thinking or
  // talking — and since text now streams well ahead of speech, that covered
  // most of a turn.
  if (audioPlaying) return 'speaking';
  if (
    isCurrentChatStreaming ||
    voiceState === 'recording' ||
    voiceState === 'transcribing'
  ) {
    return 'listening';
  }
  return 'idle';
}

interface Particle {
  x: number;
  y: number;
  z: number;
  rFrac: number;
  size: number;
  seed: number;
}

function makeParticles(n: number): Particle[] {
  const arr: Particle[] = [];
  for (let i = 0; i < n; i++) {
    const u = Math.random() * 2 - 1;
    const phi = Math.random() * Math.PI * 2;
    const fuzzy = Math.random() < 0.18;
    const rFrac = fuzzy ? 1.0 + Math.random() * 0.4 : 0.25 + 0.75 * Math.pow(Math.random(), 0.7);
    const s = Math.sqrt(Math.max(0, 1 - u * u));
    arr.push({
      x: rFrac * s * Math.cos(phi),
      z: rFrac * s * Math.sin(phi),
      y: rFrac * u,
      rFrac,
      size: 0.5 + Math.random() * 1.6,
      seed: Math.random() * 1000,
    });
  }
  return arr;
}

// Rotation rate per 60Hz frame. Tuned by eye after the frame-rate fix made
// the orb honour these numbers literally: raised across two rounds of live
// tuning to about 1.44x idle and 1.69x listening/speaking of the original.
const SPEED_MAP: Record<OrbState, number> = {
  idle: 0.0045,
  listening: 0.01248,
  speaking: 0.023,
};
const BRIGHT_MAP: Record<OrbState, number> = { idle: 0.85, listening: 1.05, speaking: 1.3 };

export function OrbVisual({ state, size = 328 }: { state: OrbState; size?: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef(state);
  const particlesRef = useRef<Particle[] | undefined>(undefined);
  const rafRef = useRef<number | undefined>(undefined);
  const tRef = useRef(0);
  const angleRef = useRef(0);
  const scaleRef = useRef(1);
  const speedRef = useRef(SPEED_MAP.idle);
  const lastFrameRef = useRef(0);

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    particlesRef.current = makeParticles(particleCountFor(size));

    // Every motion term below is expressed per 60Hz frame, so without this
    // the orb's speed is whatever the display's refresh rate happens to be —
    // a 144Hz monitor spins it 2.4x faster than the rate these constants
    // were tuned against. Clamped so returning to a backgrounded tab eases
    // back in rather than jumping a second of rotation in one frame.
    const draw = (now: number) => {
      const previous = lastFrameRef.current || now;
      lastFrameRef.current = now;
      const dt = frameDelta(now, previous);
      tRef.current += dt;
      drawOrb(
        ctx,
        canvas,
        particlesRef.current!,
        stateRef.current,
        tRef,
        angleRef,
        scaleRef,
        speedRef,
        dt,
      );
      rafRef.current = requestAnimationFrame(draw);
    };
    rafRef.current = requestAnimationFrame(draw);

    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size]);

  return (
    <div style={{ position: 'relative', width: size, height: size, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
      <div
        style={{
          position: 'absolute',
          width: '160%',
          height: '160%',
          left: '-30%',
          top: '-30%',
          background:
            'radial-gradient(circle, rgba(34,211,238,0.18) 0%, rgba(20,60,68,0.08) 35%, rgba(10,10,11,0) 70%)',
          pointerEvents: 'none',
        }}
      />
      <canvas ref={canvasRef} width={size} height={size} style={{ position: 'absolute', inset: 0 }} />
    </div>
  );
}

function drawOrb(
  ctx: CanvasRenderingContext2D,
  canvas: HTMLCanvasElement,
  particles: Particle[],
  orbState: OrbState,
  tRef: React.MutableRefObject<number>,
  angleRef: React.MutableRefObject<number>,
  scaleRef: React.MutableRefObject<number>,
  speedRef: React.MutableRefObject<number>,
  dt: number,
) {
  const w = canvas.width;
  const h = canvas.height;
  const cx = w / 2;
  const cy = h / 2;
  const R = w / 2 - 12;
  const t = tRef.current;

  ctx.clearRect(0, 0, w, h);

  let targetScale = orbState === 'idle' ? 0.88 : 1.0;
  if (orbState === 'speaking') {
    const talk = Math.sin(t * 0.06) * 0.5 + Math.sin(t * 0.13 + 1.3) * 0.3 + Math.sin(t * 0.22 + 2.1) * 0.2;
    targetScale = 1.1 + talk * 0.16;
  }
  scaleRef.current = approach(
    scaleRef.current,
    targetScale,
    orbState === 'speaking' ? 0.12 : 0.08,
    dt,
  );
  const stepped = stepRotation(
    angleRef.current,
    speedRef.current,
    SPEED_MAP[orbState] || 0.0035,
    dt,
  );
  angleRef.current = stepped.angle;
  speedRef.current = stepped.speed;

  const brightBoost =
    (BRIGHT_MAP[orbState] || 0.85) * (orbState === 'speaking' ? 0.9 + (scaleRef.current - 1.08) * 1.5 : 1);
  const flickerSpeed = orbState === 'speaking' ? 0.09 : orbState === 'listening' ? 0.06 : 0.035;
  const breathe = 0.75 + 0.25 * Math.sin(t * 0.02);
  const scaledR = R * scaleRef.current;
  const cosA = Math.cos(angleRef.current);
  const sinA = Math.sin(angleRef.current);

  ctx.save();
  ctx.shadowColor = 'rgba(34,211,238,0.9)';
  ctx.shadowBlur = 2.2;

  for (const p of particles) {
    const rx = p.x * cosA + p.z * sinA;
    const rz = -p.x * sinA + p.z * cosA;
    const px = cx + rx * scaledR;
    const py = cy + p.y * scaledR;
    const depth = (rz + 1) / 2;
    const flicker = 0.4 + 0.6 * Math.sin(p.seed + t * flickerSpeed);
    const edgeFade = p.rFrac > 1 ? Math.max(0, 1 - (p.rFrac - 1) * 2.4) : 1;
    const alpha = Math.min(1, (0.18 + 0.82 * depth) * brightBoost * flicker * edgeFade * breathe);
    if (alpha <= 0.02) continue;
    ctx.fillStyle = `rgba(56,224,247,${alpha.toFixed(3)})`;
    const size = p.size * (0.55 + 0.7 * depth) * scaleRef.current;
    ctx.fillRect(px, py, size, size);
  }
  ctx.restore();

  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  const ringAlpha = (orbState === 'idle' ? 0.1 : orbState === 'listening' ? 0.2 : 0.32) * breathe;
  const grad = ctx.createRadialGradient(cx, cy, scaledR * 0.15, cx, cy, scaledR * 1.1);
  grad.addColorStop(0, `rgba(34,211,238,${ringAlpha})`);
  grad.addColorStop(1, 'rgba(34,211,238,0)');
  ctx.fillStyle = grad;
  ctx.beginPath();
  ctx.arc(cx, cy, scaledR * 1.1, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();

  ctx.save();
  ctx.globalCompositeOperation = 'destination-in';
  const mask = ctx.createRadialGradient(cx, cy, 0, cx, cy, w / 2);
  mask.addColorStop(0, 'rgba(255,255,255,1)');
  mask.addColorStop(0.72, 'rgba(255,255,255,1)');
  mask.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = mask;
  ctx.fillRect(0, 0, w, h);
  ctx.restore();
}
