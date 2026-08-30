import { describe, expect, it } from 'vitest';
import {
  DEFAULT_VOICE_PROFILE,
  getVoiceProfile,
  VOICE_PROFILES,
} from './voice-profiles';

describe('Sage voice profiles', () => {
  it('uses Jarvis as the primary Sonic 3.6 profile', () => {
    expect(DEFAULT_VOICE_PROFILE).toEqual({
      id: '78a05d7d-268b-4a18-aad7-7a96902a95ee',
      key: 'jarvis',
      name: 'Jarvis',
      speed: 1,
      volume: 1.9,
    });
  });

  it('uses the requested Frieren tuning', () => {
    expect(VOICE_PROFILES[1]).toMatchObject({
      id: 'e23c9ecf-e002-4f7a-8e39-13d18d09923f',
      name: 'Frieren',
      speed: 0.9,
      volume: 1.9,
    });
  });

  it('fails closed to Jarvis for a stale stored voice', () => {
    expect(getVoiceProfile('removed-voice')).toBe(DEFAULT_VOICE_PROFILE);
  });
});
