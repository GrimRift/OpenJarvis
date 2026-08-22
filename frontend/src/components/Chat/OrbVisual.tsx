import { useEffect, useRef } from 'react';
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
  if (isCurrentChatStreaming || audioPlaying) return 'speaking';
  if (voiceState === 'recording' || voiceState === 'transcribing') return 'listening';
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

const SPEED_MAP: Record<OrbState, number> = { idle: 0.0035, listening: 0.008, speaking: 0.013 };
const BRIGHT_MAP: Record<OrbState, number> = { idle: 0.85, listening: 1.05, speaking: 1.3 };

export function OrbVisual({ state, size = 285 }: { state: OrbState; size?: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef(state);
  const particlesRef = useRef<Particle[] | undefined>(undefined);
  const rafRef = useRef<number | undefined>(undefined);
  const tRef = useRef(0);
  const angleRef = useRef(0);
  const scaleRef = useRef(1);

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    particlesRef.current = makeParticles(2200);

    const draw = () => {
      tRef.current += 1;
      drawOrb(ctx, canvas, particlesRef.current!, stateRef.current, tRef, angleRef, scaleRef);
      rafRef.current = requestAnimationFrame(draw);
    };
    draw();

    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
  scaleRef.current += (targetScale - scaleRef.current) * (orbState === 'speaking' ? 0.12 : 0.08);
  angleRef.current += SPEED_MAP[orbState] || 0.0035;

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
