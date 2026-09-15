import { describe, expect, it } from 'vitest';
import { orbStateLabel, resolveOrbState } from './orb-state';

const base = { audioPlaying: false, streamingHere: false, voiceState: 'idle', presenceState: 'present' };

describe('resolveOrbState', () => {
  it('dims for an empty desk only when nothing else is happening', () => {
    expect(resolveOrbState({ ...base, presenceState: 'away' })).toBe('away');
    expect(resolveOrbState({ ...base, presenceState: 'away', streamingHere: true })).toBe('listening');
    expect(resolveOrbState({ ...base, presenceState: 'away', audioPlaying: true })).toBe('speaking');
  });

  it('is idle when presence is off or unknown', () => {
    expect(resolveOrbState({ ...base, presenceState: 'disabled' })).toBe('idle');
    expect(resolveOrbState({ ...base, presenceState: 'unknown' })).toBe('idle');
  });

  it('labels every state', () => {
    expect(orbStateLabel('away')).toBe('Away');
    expect(orbStateLabel('idle')).toBe('Standing by');
  });
});
