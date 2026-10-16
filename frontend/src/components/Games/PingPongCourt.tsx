/**
 * Ping pong in the game panel (mockup A look): your paddle on the left
 * follows the mouse, Sage's on the right; click (or Space) to serve.
 *
 * The rally runs here (`lib/pong-physics`); each point goes to the server,
 * which keeps the score and the serve and answers with Sage's line for the
 * big moments (game point, deuce, a long rally, a record, the end), said
 * aloud. "Pause" / "resume" by voice arrive as a new panel with `paused`.
 */

import { useEffect, useRef, useState } from 'react';
import { apiFetch } from '../../lib/api';
import type { GamePanel, PingPongPanel } from '../../lib/game-panel';
import {
  BALL_RADIUS,
  COURT,
  PADDLE,
  newRally,
  sagePaddleAt,
  serve,
  skillFor,
  step,
  type Rally,
} from '../../lib/pong-physics';

type Phase = 'serve' | 'play' | 'between' | 'over';

/** Sage waits to serve until the mouse has moved within this long. */
const IDLE_MS = 4000;

interface Props {
  panel: PingPongPanel;
  /** Paused by the panel's button (a voice "pause" comes in on `panel`). */
  paused: boolean;
  onPanel: (panel: GamePanel) => void;
  onLine: (line: string) => void;
}

export function PingPongCourt({ panel, paused, onPanel, onLine }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rallyRef = useRef<Rally>(newRally(panel.server));
  const phaseRef = useRef<Phase>(panel.over ? 'over' : 'serve');
  const mouseRef = useRef(COURT.height / 2);
  // When the user last moved the mouse: Sage serves only to someone playing
  // (16 October: left alone, it served and scored 4-0 by itself).
  const activeAtRef = useRef(0);
  const panelRef = useRef(panel);
  const pausedRef = useRef(paused);
  const [phase, setPhase] = useState<Phase>(phaseRef.current);
  panelRef.current = panel;
  pausedRef.current = paused || panel.paused;

  const setPhaseBoth = (next: Phase) => {
    phaseRef.current = next;
    setPhase(next);
  };

  useEffect(() => {
    if (panel.over) setPhaseBoth('over');
  }, [panel.over]);

  // Sage serves on its own after a beat; the user serves with a click.
  useEffect(() => {
    if (phase !== 'serve' || panel.server !== 'sage' || panel.over) return;
    const timer = window.setInterval(() => {
      if (phaseRef.current !== 'serve' || pausedRef.current) return;
      if (performance.now() - activeAtRef.current > IDLE_MS) return;
      serve(rallyRef.current, 'sage');
      setPhaseBoth('play');
    }, 900);
    return () => window.clearInterval(timer);
  }, [phase, panel.server, panel.over, paused, panel.paused]);

  const serveNow = () => {
    if (phaseRef.current !== 'serve' || pausedRef.current) return;
    if (panelRef.current.server !== 'user') return;
    serve(rallyRef.current, 'user');
    setPhaseBoth('play');
  };

  useEffect(() => {
    const onMove = (event: PointerEvent) => {
      const rect = canvasRef.current?.getBoundingClientRect();
      if (!rect) return;
      mouseRef.current = ((event.clientY - rect.top) / rect.height) * COURT.height;
      activeAtRef.current = performance.now();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.code === 'Space') {
        event.preventDefault();
        serveNow();
      }
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('keydown', onKey);
    };
    // serveNow reads refs only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const reportPoint = async (winner: 'user' | 'sage', hits: number) => {
    try {
      const res = await apiFetch('/v1/games/pong/point', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ winner, rally: hits }),
      });
      if (!res.ok) return;
      const body = await res.json();
      const next = body?.game_panel as PingPongPanel | undefined;
      if (body?.line) onLine(String(body.line));
      if (next) {
        // A voice "pause" is applied when it comes in; a point keeps the
        // pause the panel already has.
        onPanel({ ...next, paused: panelRef.current.paused });
        rallyRef.current = newRally(next.server, rallyRef.current.user);
        setPhaseBoth(next.over ? 'over' : 'serve');
      }
    } catch {
      setPhaseBoth('serve');
    }
  };

  // The frame loop.
  useEffect(() => {
    let frame = 0;
    let last = performance.now();
    const tick = (now: number) => {
      frame = requestAnimationFrame(tick);
      const dt = Math.min(0.033, (now - last) / 1000);
      last = now;
      const rally = rallyRef.current;
      const current = panelRef.current;
      if (!pausedRef.current) {
        if (phaseRef.current === 'play') {
          const skill = skillFor(current.difficulty, current.user_score, current.sage_score);
          const winner = step(rally, dt, mouseRef.current, sagePaddleAt(skill));
          if (winner) {
            setPhaseBoth('between');
            void reportPoint(winner, rally.hits);
          }
        } else if (phaseRef.current === 'serve') {
          // The paddles move while waiting; the ball sits on the server's.
          step(rally, 0, mouseRef.current, sagePaddleAt(0.5));
          const fresh = newRally(current.server, rally.user);
          rally.ball = fresh.ball;
          if (current.server === 'user') rally.ball.y = rally.user;
        }
      }
      draw(canvasRef.current, rally, current, phaseRef.current, pausedRef.current);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <canvas
      ref={canvasRef}
      width={COURT.width}
      height={COURT.height}
      onClick={serveNow}
      aria-label={`Ping pong court. You ${panel.user_score}, Sage ${panel.sage_score}.`}
      style={{
        width: COURT.width,
        maxWidth: '100%',
        borderRadius: 12,
        border: '1px solid rgba(34,211,238,0.35)',
        background: '#06121e',
        boxShadow: '0 0 24px rgba(34,211,238,0.12)',
        cursor: 'none',
        display: 'block',
        flex: 'none',
      }}
    />
  );
}

function draw(
  canvas: HTMLCanvasElement | null,
  rally: Rally,
  panel: PingPongPanel,
  phase: Phase,
  paused: boolean,
) {
  const ctx = canvas?.getContext('2d');
  if (!ctx) return;
  const { width, height } = COURT;
  ctx.clearRect(0, 0, width, height);

  ctx.strokeStyle = 'rgba(34,211,238,0.3)';
  ctx.setLineDash([8, 10]);
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(width / 2, 12);
  ctx.lineTo(width / 2, height - 12);
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.fillStyle = '#a5f3fc';
  ctx.font = '700 40px "Segoe UI", system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  ctx.globalAlpha = 0.8;
  ctx.fillText(String(panel.user_score), width / 2 - 60, 18);
  ctx.fillText(String(panel.sage_score), width / 2 + 60, 18);
  ctx.globalAlpha = 1;

  const paddle = (x: number, y: number, colour: string) => {
    ctx.shadowColor = '#67e8f9';
    ctx.shadowBlur = 14;
    ctx.fillStyle = colour;
    ctx.beginPath();
    ctx.roundRect(x, y - PADDLE.height / 2, PADDLE.width, PADDLE.height, 5);
    ctx.fill();
    ctx.shadowBlur = 0;
  };
  paddle(PADDLE.inset, rally.user, '#e8fbff');
  paddle(width - PADDLE.inset - PADDLE.width, rally.sage, '#22d3ee');

  if (phase !== 'over') {
    ctx.shadowColor = '#67e8f9';
    ctx.shadowBlur = 16;
    ctx.fillStyle = '#ffffff';
    ctx.beginPath();
    ctx.arc(rally.ball.x, rally.ball.y, BALL_RADIUS, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0;
  }

  const banner =
    phase === 'over'
      ? panel.winner === 'user'
        ? 'You win!'
        : panel.winner === 'sage'
          ? 'Sage wins'
          : 'Game over'
      : paused
        ? 'Paused'
        : phase === 'serve' && panel.server === 'user'
          ? 'Click to serve'
          : phase === 'serve'
            ? 'Move your paddle: Sage serves'
            : '';
  if (banner) {
    ctx.fillStyle = 'rgba(6,18,30,0.6)';
    ctx.fillRect(0, height / 2 - 30, width, 60);
    ctx.fillStyle = '#cffafe';
    ctx.font = '600 22px "Segoe UI", system-ui, sans-serif';
    ctx.textBaseline = 'middle';
    ctx.fillText(banner, width / 2, height / 2);
  }
  if (phase === 'play' && rally.hits >= 4) {
    ctx.fillStyle = 'rgba(165,243,252,0.6)';
    ctx.font = '600 13px "Segoe UI", system-ui, sans-serif';
    ctx.textBaseline = 'bottom';
    ctx.fillText(`rally ${rally.hits}`, width / 2, height - 10);
  }
}
