import { describe, expect, it } from 'vitest';
import { isGenerating, orbStateLabel, resolveOrbState } from './orb-state';

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

  it('says generating once the mic is done, without a new orb state', () => {
    const replying = { ...base, streamingHere: true };
    expect(resolveOrbState(replying)).toBe('listening');
    expect(isGenerating(replying)).toBe(true);
    // The mic stays open while the answer is prepared; still generating.
    expect(isGenerating({ ...replying, voiceState: 'recording' })).toBe(true);
    expect(isGenerating({ ...base, voiceState: 'recording' })).toBe(false);
    expect(isGenerating({ ...replying, audioPlaying: true })).toBe(false);
    expect(orbStateLabel('listening', true)).toBe('Generating');
    expect(orbStateLabel('listening')).toBe('Listening');
  });
});
