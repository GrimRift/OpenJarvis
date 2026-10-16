import { describe, expect, it } from 'vitest';
import {
  BALL_RADIUS,
  COURT,
  PADDLE,
  newRally,
  predictY,
  sagePaddleAt,
  serve,
  skillFor,
  step,
} from './pong-physics';

const still = () => 0.5;

describe('pong rally', () => {
  it('a ball the user meets comes back faster', () => {
    const rally = newRally('sage');
    serve(rally, 'sage', still);
    const speed = Math.abs(rally.ball.vx);
    let winner = null;
    for (let i = 0; i < 400 && rally.hits === 0 && !winner; i++) {
      winner = step(rally, 1 / 120, rally.ball.y, sagePaddleAt(1), still);
    }
    expect(winner).toBeNull();
    expect(rally.hits).toBe(1);
    expect(rally.ball.vx).toBeGreaterThan(speed);
  });

  it('a ball the user misses is Sage\'s point', () => {
    const rally = newRally('sage');
    serve(rally, 'sage', still);
    let winner = null;
    for (let i = 0; i < 600 && !winner; i++) {
      winner = step(rally, 1 / 120, PADDLE.height / 2, sagePaddleAt(1), still);
    }
    expect(winner).toBe('sage');
  });

  it('the edge of the paddle sends the ball steep', () => {
    const centre = newRally('sage');
    const edge = newRally('sage');
    for (const [rally, offset] of [
      [centre, 0],
      [edge, PADDLE.height / 2 - 2],
    ] as const) {
      serve(rally, 'sage', still);
      for (let i = 0; i < 400 && rally.hits === 0; i++) {
        step(rally, 1 / 120, rally.ball.y - offset, sagePaddleAt(1), still);
      }
    }
    expect(Math.abs(edge.ball.vy)).toBeGreaterThan(Math.abs(centre.ball.vy) + 200);
  });

  it('predicts the arrival across wall bounces', () => {
    const ball = { x: 100, y: 50, vx: 300, vy: -300 };
    const y = predictY(ball, 100 + 300);
    // Up 300 px from y=50: off the top wall at y=BALL_RADIUS, back down.
    expect(y).toBeCloseTo(2 * BALL_RADIUS - (50 - 300), 5);
    expect(y).toBeGreaterThanOrEqual(BALL_RADIUS);
    expect(y).toBeLessThanOrEqual(COURT.height - BALL_RADIUS);
  });

  it('hard Sage returns a straight serve; the ball stays on the court', () => {
    const rally = newRally('user');
    serve(rally, 'user', still);
    let winner = null;
    for (let i = 0; i < 300 && rally.hits === 0 && !winner; i++) {
      winner = step(rally, 1 / 120, rally.user, sagePaddleAt(0.9), still);
    }
    expect(winner).toBeNull();
    expect(rally.hits).toBe(1);
    expect(rally.ball.vx).toBeLessThan(0);
  });

  it('adapt plays harder when the user leads', () => {
    expect(skillFor('adapt', 8, 2)).toBeGreaterThan(skillFor('adapt', 5, 5));
    expect(skillFor('adapt', 2, 8)).toBeLessThan(skillFor('adapt', 5, 5));
    expect(skillFor('hard', 0, 0)).toBeGreaterThan(skillFor('easy', 0, 0));
  });

  it('a mouse jump adds only so much spin', () => {
    const rally = newRally('sage');
    serve(rally, 'sage', still);
    let y = rally.ball.y;
    for (let i = 0; i < 400 && rally.hits === 0; i++) {
      // The paddle jumps 30 px in one frame as the ball arrives.
      y = rally.ball.x < 60 ? rally.ball.y - 30 : rally.ball.y;
      step(rally, 1 / 120, y, sagePaddleAt(1), still);
    }
    expect(rally.hits).toBe(1);
    expect(Math.abs(rally.ball.vy)).toBeLessThan(700);
  });
});
