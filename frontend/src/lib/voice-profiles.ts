export interface VoiceProfile {
  id: string;
  key: 'jarvis' | 'frieren';
  name: string;
  speed: number;
  volume: number;
}

export const VOICE_PROFILES: readonly VoiceProfile[] = [
  {
    id: '78a05d7d-268b-4a18-aad7-7a96902a95ee',
    key: 'jarvis',
    name: 'Jarvis',
    speed: 1.0,
    volume: 1.9,
  },
  {
    id: 'e23c9ecf-e002-4f7a-8e39-13d18d09923f',
    key: 'frieren',
    name: 'Frieren',
    speed: 0.9,
    volume: 1.9,
  },
] as const;

export const DEFAULT_VOICE_PROFILE = VOICE_PROFILES[0];

export function getVoiceProfile(id: string): VoiceProfile {
  return VOICE_PROFILES.find((profile) => profile.id === id) ?? DEFAULT_VOICE_PROFILE;
}

export function isKnownVoiceId(id: unknown): id is string {
  return typeof id === 'string' && VOICE_PROFILES.some((profile) => profile.id === id);
}
