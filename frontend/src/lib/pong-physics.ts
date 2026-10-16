/**
 * Ping pong's rally, played in the game panel (16 October: a top-down paddle
 * game, you on the left, Sage on the right, mouse moves your paddle).
 *
 * Pure: the court component calls `step` each frame and reports a point to
 * the server, which keeps the score and the serve (games/ping_pong.py).
 *
 * The ball leaves a paddle at an angle set by where it hit (the edge sends
 * it steep), picks up some of the paddle's movement as spin, and gets a
 * little faster each hit, so a rally builds. Sage's paddle reads the ball's
 * path with an error and a reaction time set by the difficulty; "adapt"
 * plays harder when the user leads and easier when they trail.
 */

export const COURT = { width: 720, height: 420 } as const;
export const PADDLE = { width: 10, height: 84, inset: 24 } as const;
export const BALL_RADIUS = 7;
const SERVE_SPEED = 380;
const SPEED_UP = 1.045;
const MAX_SPEED = 1050;
const MAX_ANGLE = (55 * Math.PI) / 180;
/** How much of the paddle's own speed the ball takes as spin, and the most
 *  spin can add: a mouse jump is thousands of px/s in one frame. */
const SPIN = 0.15;
const MAX_SPIN = 220;

export interface Rally {
  ball: { x: number; y: number; vx: number; vy: number };
  /** Paddle centres (y), and how fast each moved last frame (px/s). */
  user: number;
  sage: number;
  userSpeed: number;
  sageSpeed: number;
  hits: number;
  /** Who hit last: the same paddle cannot hit twice in a row. */
  lastHit: 'user' | 'sage' | '';
  /** Where Sage's paddle is heading, and when it may look again. */
  sageTarget: number;
  sageLookIn: number;
  /** Sage's misjudgement this time the ball comes its way (px). */
  sageError: number;
}

export interface SagePaddle {
  /** Top speed, px/s. */
  speed: number;
  /** Most it misjudges where the ball arrives, px. */
  error: number;
  /** Seconds between looks at the ball. */
  reaction: number;
}

/** Sage's paddle at a skill from 0 (hopeless) to 1 (near perfect). */
export function sagePaddleAt(skill: number): SagePaddle {
  const t = Math.min(1, Math.max(0, skill));
  return { speed: 260 + 520 * t, error: 46 - 40 * t, reaction: 0.28 - 0.2 * t };
}

/** The skill for a difficulty; "adapt" follows the score. */
export function skillFor(difficulty: string, userScore: number, sageScore: number): number {
  if (difficulty === 'easy') return 0.15;
  if (difficulty === 'hard') return 0.9;
  if (difficulty === 'adapt') return Math.min(0.95, Math.max(0.1, 0.5 + 0.12 * (userScore - sageScore)));
  return 0.5;
}

const USER_X = PADDLE.inset;
const SAGE_X = COURT.width - PADDLE.inset - PADDLE.width;

/** A new rally with the ball on the server's paddle, not yet moving. */
export function newRally(server: 'user' | 'sage', user = COURT.height / 2): Rally {
  const sage = COURT.height / 2;
  return {
    ball: {
      x: server === 'user' ? USER_X + PADDLE.width + BALL_RADIUS + 2 : SAGE_X - BALL_RADIUS - 2,
      y: server === 'user' ? user : sage,
      vx: 0,
      vy: 0,
    },
    user,
    sage,
    userSpeed: 0,
    sageSpeed: 0,
    hits: 0,
    lastHit: '',
    sageTarget: sage,
    sageLookIn: 0,
    sageError: 0,
  };
}

/** Send the ball off from the server's side. `random` is for tests. */
export function serve(rally: Rally, server: 'user' | 'sage', random = Math.random): void {
  const angle = (random() - 0.5) * 0.6;
  const direction = server === 'user' ? 1 : -1;
  rally.ball.vx = direction * SERVE_SPEED * Math.cos(angle);
  rally.ball.vy = SERVE_SPEED * Math.sin(angle);
}

/** Where the ball will cross x, bouncing off the side walls. */
export function predictY(ball: Rally['ball'], x: number): number {
  if (ball.vx === 0) return ball.y;
  const time = (x - ball.x) / ball.vx;
  if (time < 0) return ball.y;
  const top = BALL_RADIUS;
  const span = COURT.height - 2 * BALL_RADIUS;
  let y = ball.y + ball.vy * time - top;
  y = ((y % (2 * span)) + 2 * span) % (2 * span);
  return top + (y > span ? 2 * span - y : y);
}

const clampPaddle = (y: number) =>
  Math.min(COURT.height - PADDLE.height / 2, Math.max(PADDLE.height / 2, y));

function bounce(rally: Rally, paddleY: number, paddleSpeed: number, direction: 1 | -1): void {
  const offset = Math.max(-1, Math.min(1, (rally.ball.y - paddleY) / (PADDLE.height / 2)));
  const speed = Math.min(MAX_SPEED, Math.hypot(rally.ball.vx, rally.ball.vy) * SPEED_UP);
  const angle = offset * MAX_ANGLE;
  rally.ball.vx = direction * speed * Math.cos(angle);
  const spin = Math.max(-MAX_SPIN, Math.min(MAX_SPIN, paddleSpeed * SPIN));
  rally.ball.vy = speed * Math.sin(angle) + spin;
  rally.hits += 1;
  rally.lastHit = direction === 1 ? 'user' : 'sage';
}

/**
 * One frame: move Sage's paddle and the ball. `userY` is where the mouse
 * puts the user's paddle. Returns who won the point, or null mid-rally.
 */
export function step(
  rally: Rally,
  dt: number,
  userY: number,
  sage: SagePaddle,
  random = Math.random,
): 'user' | 'sage' | null {
  const nextUser = clampPaddle(userY);
  rally.userSpeed = dt > 0 ? (nextUser - rally.user) / dt : 0;
  rally.user = nextUser;

  // Sage looks at the ball every `reaction` seconds, and heads for where it
  // thinks the ball will arrive (or the middle, while it is going away).
  rally.sageLookIn -= dt;
  if (rally.sageLookIn <= 0) {
    rally.sageLookIn = sage.reaction;
    rally.sageTarget =
      rally.ball.vx > 0 ? predictY(rally.ball, SAGE_X) + rally.sageError : COURT.height / 2;
  }
  const gap = rally.sageTarget - rally.sage;
  const move = Math.sign(gap) * Math.min(Math.abs(gap), sage.speed * dt);
  const nextSage = clampPaddle(rally.sage + move);
  rally.sageSpeed = dt > 0 ? (nextSage - rally.sage) / dt : 0;
  rally.sage = nextSage;

  const ball = rally.ball;
  ball.x += ball.vx * dt;
  ball.y += ball.vy * dt;
  if (ball.y < BALL_RADIUS) {
    ball.y = 2 * BALL_RADIUS - ball.y;
    ball.vy = Math.abs(ball.vy);
  } else if (ball.y > COURT.height - BALL_RADIUS) {
    ball.y = 2 * (COURT.height - BALL_RADIUS) - ball.y;
    ball.vy = -Math.abs(ball.vy);
  }

  const reaches = (paddleY: number) => Math.abs(ball.y - paddleY) <= PADDLE.height / 2 + BALL_RADIUS;
  if (ball.vx < 0 && ball.x - BALL_RADIUS <= USER_X + PADDLE.width && ball.x > USER_X - BALL_RADIUS) {
    if (rally.lastHit !== 'user' && reaches(rally.user)) {
      ball.x = USER_X + PADDLE.width + BALL_RADIUS;
      bounce(rally, rally.user, rally.userSpeed, 1);
      // Each time the ball comes back, Sage misjudges it afresh.
      rally.sageError = (random() * 2 - 1) * sage.error;
      rally.sageLookIn = Math.min(rally.sageLookIn, sage.reaction);
    }
  } else if (ball.vx > 0 && ball.x + BALL_RADIUS >= SAGE_X && ball.x < SAGE_X + PADDLE.width + BALL_RADIUS) {
    if (rally.lastHit !== 'sage' && reaches(rally.sage)) {
      ball.x = SAGE_X - BALL_RADIUS;
      bounce(rally, rally.sage, rally.sageSpeed, -1);
    }
  }

  if (ball.x < -BALL_RADIUS * 3) return 'sage';
  if (ball.x > COURT.width + BALL_RADIUS * 3) return 'user';
  return null;
}
