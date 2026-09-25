import { describe, expect, it } from 'vitest';
import {
  BREATH_DEPTH,
  BREATH_PERIOD,
  PLEXUS_LOOK,
  PLEXUS_PATCHES,
  PLEXUS_STATES,
  breathScale,
  MIN_DRAW_GAP_MS,
  MORPH_FRAMES,
  SPEAKING_MORPH_FRAMES,
  easeIntoSpeaking,
  easeMorph,
  speakingScale,
  syllableRise,
} from './orb-plexus';

describe('breathing', () => {
  it('holds an unwatched orb perfectly still', () => {
    // Nobody is there to see it; an orb that keeps inflating at an empty
    // desk reads as restless, not resting.
    for (const t of [0, 40, 120, 199]) {
      expect(breathScale('away', t, 0)).toBe(1);
    }
  });

  it('breathes standing by faintly and listening a little more', () => {
    // "Standing by" is the idle state: Sage with nothing to do and someone
    // there. It is the state a user sees nearly all the time, and it is the
    // one that has to look alive.
    const swing = (state: 'idle' | 'listening') => {
      let lo = Infinity;
      let hi = -Infinity;
      for (let t = 0; t < BREATH_PERIOD; t++) {
        const v = breathScale(state, t, 0);
        lo = Math.min(lo, v);
        hi = Math.max(hi, v);
      }
      return hi - lo;
    };
    expect(swing('idle')).toBeCloseTo(BREATH_DEPTH.idle, 2);
    expect(swing('listening')).toBeCloseTo(BREATH_DEPTH.listening, 2);
    expect(swing('idle')).toBeLessThan(swing('listening'));
  });
});

describe('speaking follows the voice, not a cycle', () => {
  it('rests at standing-by size in a silence and reaches full on a word', () => {
    const rest = speakingScale(0) * PLEXUS_STATES.speaking.r;
    const full = speakingScale(1) * PLEXUS_STATES.speaking.r;
    expect(rest).toBeCloseTo(PLEXUS_STATES.idle.r, 5);
    expect(full).toBeCloseTo(PLEXUS_STATES.speaking.r, 5);
  });

  it('ignores the breathing cycle entirely', () => {
    // Two different points in the cycle, same voice: the same size.
    expect(breathScale('speaking', 0, 0.5)).toBe(breathScale('speaking', 120, 0.5));
  });
});

describe('syllables', () => {
  it('reads the onset, not the level', () => {
    // A steady level only inflates the body; the rise is what a word is.
    expect(syllableRise(0.8, 0.8)).toBe(0);
    expect(syllableRise(0.8, 0.2)).toBeCloseTo(0.6, 5);
    expect(syllableRise(0.2, 0.8)).toBe(0);
  });
});

describe('a state is never dimmer than a calmer one', () => {
  it('orders the calm states: an empty desk, standing by, listening', () => {
    // A first-order model: marks scale with brightness, wiring, the share
    // of lines drawn and patch level, the finished frame with exposure.
    const level = (state: 'idle' | 'away' | 'listening') => {
      const cfg = PLEXUS_STATES[state];
      const look = PLEXUS_LOOK.states[state];
      return cfg.bright * cfg.links * look.wires * PLEXUS_PATCHES[state].base * look.exposure;
    };
    expect(level('away')).toBeLessThan(level('idle'));
    expect(level('idle')).toBeLessThan(level('listening'));
  });

  // Not asserted here: that a pause mid-sentence is at least as bright as
  // standing by. It once was the dimmest thing on screen (median 37 against
  // 53), and it is why speaking's exposure answers its lit patches. But the two
  // states now reach their brightness differently -- standing by through a
  // high exposure that clips, speaking through contrast on stronger marks --
  // and no linear model of that tracks the pixels: the last one said a
  // pause was 25% under standing by when it rendered clearly over it
  // (median 54 / p90 206 against 38 / 126). Checking it needs a real
  // canvas, which these tests do not have; it was measured in the browser.
});

describe('rotation', () => {
  it('turns standing by slowly, but never stops it', () => {
    // Seconds per full turn at 60 frames a second.
    const period = (state: 'idle' | 'away' | 'listening') => (2 * Math.PI) / PLEXUS_STATES[state].spin / 60;
    expect(period('idle')).toBeGreaterThan(60);
    expect(period('idle')).toBeLessThan(120);
    expect(period('away')).toBeGreaterThan(period('idle'));
    expect(period('listening')).toBeLessThan(period('idle'));
  });
});

describe('sizes', () => {
  it('grows as Sage engages, with an empty desk the smallest', () => {
    expect(PLEXUS_STATES.away.r).toBeLessThan(PLEXUS_STATES.idle.r);
    expect(PLEXUS_STATES.idle.r).toBeLessThan(PLEXUS_STATES.listening.r);
    expect(PLEXUS_STATES.listening.r).toBeLessThan(PLEXUS_STATES.speaking.r);
  });
});

describe('frame rate', () => {
  it('draws every state at 60 a second or better on common displays', () => {
    // Refresh rates in Hz: how often a draw lands when each frame is only
    // drawn once MIN_DRAW_GAP_MS has passed since the last.
    const rate = (hz: number) => {
      const frame = 1000 / hz;
      let every = 1;
      while (every * frame < MIN_DRAW_GAP_MS) every++;
      return hz / every;
    };
    for (const hz of [60, 75, 120, 144, 165, 180, 240]) {
      expect(rate(hz)).toBeGreaterThanOrEqual(60);
    }
    // And no faster than it needs to: a 180Hz display is not drawn at 90.
    expect(rate(180)).toBe(60);
  });
});

describe('a state change', () => {
  it('eases in and out rather than lurching', () => {
    // The first and last frames of the morph move least; the middle most.
    const step = (x: number) => easeMorph(x + 1 / MORPH_FRAMES) - easeMorph(x);
    expect(step(0)).toBeLessThan(step(0.45) / 10);
    expect(step(1 - 1 / MORPH_FRAMES)).toBeLessThan(step(0.45) / 10);
    expect(easeMorph(0)).toBe(0);
    expect(easeMorph(1)).toBe(1);
  });

  it('takes about a second', () => {
    expect(MORPH_FRAMES / 60).toBeGreaterThan(0.8);
    expect(MORPH_FRAMES / 60).toBeLessThan(1.4);
  });

  it('into speaking, is there with the first word', () => {
    // A reminder's words start 170 ms after the orb hears of it; the gentle
    // morph was only half way there at 480 ms (25 September).
    const at = (ms: number) => easeIntoSpeaking((ms / (1000 / 60)) / SPEAKING_MORPH_FRAMES);
    expect(at(330)).toBeGreaterThanOrEqual(0.8);
    expect(easeMorph(330 / (1000 / 60) / MORPH_FRAMES)).toBeLessThan(0.5);
    // Still no lurch: no single frame moves it more than 7% of the way.
    expect(easeIntoSpeaking(1 / SPEAKING_MORPH_FRAMES)).toBeLessThan(0.07);
    expect(easeIntoSpeaking(0)).toBe(0);
    expect(easeIntoSpeaking(1)).toBe(1);
  });
});
