/**
 * Particles that act out the weather behind the panel (M41 design, user's
 * pick "S3"): rain falls as cyan streaks, a thunderstorm flashes a web of
 * links now and then, a clear day drifts slow motes (stars at night), cloud
 * and fog drift as haze, snow falls slowly.
 *
 * Kept quiet on purpose: few particles, low alpha, one canvas. It stops when
 * the panel closes (the effect's cleanup) and draws nothing for users who
 * ask the OS for reduced motion.
 */

import { useEffect, useRef } from 'react';
import type { WeatherIcon } from '../../lib/weather-report';

type Mode = 'rain' | 'storm' | 'motes' | 'stars' | 'haze' | 'snow';

function particleMode(icon: WeatherIcon, isDay: boolean): Mode {
  switch (icon) {
    case 'thunder':
      return 'storm';
    case 'rain':
    case 'showers':
    case 'drizzle':
      return 'rain';
    case 'snow':
      return 'snow';
    case 'cloudy':
    case 'fog':
      return 'haze';
    default:
      return isDay ? 'motes' : 'stars';
  }
}

interface Drop { x: number; y: number; v: number; l: number; a: number }

export function WeatherParticles({ icon, isDay, heavy }: { icon: WeatherIcon; isDay: boolean; heavy: boolean }) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const mode = particleMode(icon, isDay);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let W = 0;
    let H = 0;
    const size = () => {
      const r = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      W = r.width;
      H = r.height;
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    size();
    const observer = new ResizeObserver(size);
    observer.observe(canvas);

    const falling = mode === 'rain' || mode === 'storm' || mode === 'snow';
    const count = mode === 'rain' || mode === 'storm' ? (heavy ? 120 : 70) : mode === 'snow' ? 60 : 40;
    const parts: Drop[] = Array.from({ length: count }, () => ({
      x: Math.random() * W,
      y: Math.random() * H,
      v: mode === 'snow' ? 0.4 + Math.random() * 0.6 : falling ? 2 + Math.random() * 3 : 0.08 + Math.random() * 0.12,
      l: 8 + Math.random() * 14,
      a: Math.random() * Math.PI * 2,
    }));
    const web = Array.from({ length: 34 }, () => ({ x: Math.random() * W, y: Math.random() * H * 0.55 }));
    let flash = 0;
    let frame = 0;

    const draw = () => {
      ctx.clearRect(0, 0, W, H);
      ctx.globalCompositeOperation = 'lighter';

      if (mode === 'storm') {
        if (Math.random() < 0.005) flash = 1;
        if (flash > 0) {
          for (let i = 0; i < web.length; i++) {
            for (let j = i + 1; j < web.length; j++) {
              const d = Math.hypot(web[i].x - web[j].x, web[i].y - web[j].y);
              if (d < 140) {
                ctx.strokeStyle = `rgba(183,148,255,${flash * (1 - d / 140) * 0.5})`;
                ctx.beginPath();
                ctx.moveTo(web[i].x, web[i].y);
                ctx.lineTo(web[j].x, web[j].y);
                ctx.stroke();
              }
            }
          }
          ctx.fillStyle = `rgba(183,148,255,${flash * 0.05})`;
          ctx.fillRect(0, 0, W, H);
          flash = flash * 0.9 < 0.02 ? 0 : flash * 0.9;
        }
      }

      for (const p of parts) {
        if (mode === 'rain' || mode === 'storm') {
          p.y += p.v;
          p.x -= p.v * 0.18;
          if (p.y > H) {
            p.y = -p.l;
            p.x = Math.random() * W * 1.1;
          }
          const g = ctx.createLinearGradient(p.x, p.y, p.x - p.l * 0.18, p.y + p.l);
          g.addColorStop(0, 'rgba(34,211,238,0)');
          g.addColorStop(1, 'rgba(103,232,249,0.32)');
          ctx.strokeStyle = g;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(p.x - p.l * 0.18, p.y + p.l);
          ctx.stroke();
        } else if (mode === 'snow') {
          p.y += p.v;
          p.a += 0.01;
          p.x += Math.sin(p.a) * 0.3;
          if (p.y > H) p.y = -4;
          ctx.fillStyle = 'rgba(224,242,254,0.45)';
          ctx.beginPath();
          ctx.arc(p.x, p.y, 1.6, 0, Math.PI * 2);
          ctx.fill();
        } else {
          // motes, stars, haze: slow drift and a gentle shimmer
          p.a += 0.01;
          p.x += Math.cos(p.a) * p.v;
          p.y += Math.sin(p.a * 0.7) * p.v - (mode === 'motes' ? 0.05 : 0);
          if (p.y < -6) p.y = H + 6;
          if (p.x < -6) p.x = W + 6;
          if (p.x > W + 6) p.x = -6;
          const tw = 0.5 + 0.5 * Math.sin(p.a * 3);
          const color =
            mode === 'motes' ? `rgba(251,191,36,${0.12 + tw * 0.18})`
              : mode === 'stars' ? `rgba(207,250,254,${0.15 + tw * 0.35})`
                : `rgba(148,163,184,${0.05 + tw * 0.06})`;
          ctx.fillStyle = color;
          ctx.beginPath();
          ctx.arc(p.x, p.y, mode === 'haze' ? 18 : mode === 'stars' ? 1.1 : 1.8, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [mode, heavy]);

  return (
    <canvas
      ref={ref}
      aria-hidden
      style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}
    />
  );
}
