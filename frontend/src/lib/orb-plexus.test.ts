import { describe, expect, it } from 'vitest';
import {
  BREATH_DEPTH,
  BREATH_PERIOD,
  PLEXUS_PATCHES,
  PLEXUS_STATES,
  breathScale,
  framesPerDraw,
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
  it('keeps silent speaking above standing by', () => {
    // Speaking rests at standing-by size, so the dim-on-contraction rule
    // fires with it. With its patch base set low for syllable contrast,
    // three multipliers stacked and a mid-sentence pause became the
    // dimmest thing on screen.
    const level = (state: 'idle' | 'away' | 'listening' | 'speaking') => {
      const breath = state === 'speaking' ? speakingScale(0) : 1;
      return PLEXUS_STATES[state].bright * Math.pow(breath, 1.35) * PLEXUS_PATCHES[state].base;
    };
    expect(level('away')).toBeLessThan(level('idle'));
    expect(level('idle')).toBeLessThan(level('listening'));
    expect(level('listening')).toBeLessThan(level('speaking'));
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
  it('halves the rate only in the two calm states', () => {
    expect(framesPerDraw('idle')).toBe(2);
    expect(framesPerDraw('away')).toBe(2);
    expect(framesPerDraw('listening')).toBe(1);
    expect(framesPerDraw('speaking')).toBe(1);
  });
});
