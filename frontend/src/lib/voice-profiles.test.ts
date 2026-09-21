import { describe, expect, it } from 'vitest';
import {
  DEFAULT_LOCAL_VOICE_PROFILE,
  DEFAULT_VOICE_PROFILE,
  getVoiceProfile,
  isKnownVoiceId,
  profilesFor,
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
      provider: 'cartesia',
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

  it('resolves a voice for the engine in force, never the other one', () => {
    // A stored Cartesia voice under the local engine speaks the local
    // default, and vice versa: the engine decides, the id only picks
    // among that engine's voices.
    expect(getVoiceProfile(DEFAULT_VOICE_PROFILE.id, 'chatterbox')).toBe(DEFAULT_LOCAL_VOICE_PROFILE);
    expect(getVoiceProfile('chatterbox:jarvis', 'cartesia')).toBe(DEFAULT_VOICE_PROFILE);
    expect(getVoiceProfile('chatterbox:jarvis', 'chatterbox').provider).toBe('chatterbox');
    // Any named local voice the server reports is acceptable.
    const custom = getVoiceProfile('chatterbox:butler', 'chatterbox');
    expect(custom).toMatchObject({ id: 'chatterbox:butler', provider: 'chatterbox', name: 'Butler (local)' });
    expect(isKnownVoiceId('chatterbox:butler')).toBe(true);
    expect(isKnownVoiceId('chatterbox:')).toBe(false);
  });

  it('lists only the voices the chosen engine can speak', () => {
    expect(profilesFor('cartesia').every((p) => p.provider === 'cartesia')).toBe(true);
    expect(profilesFor('chatterbox', ['jarvis', 'butler']).map((p) => p.id)).toEqual([
      'chatterbox:jarvis',
      'chatterbox:butler',
    ]);
    // With nothing reported yet the default local voice is still offered.
    expect(profilesFor('chatterbox').map((p) => p.id)).toEqual(['chatterbox:jarvis']);
  });
});
