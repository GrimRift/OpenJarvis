export type VoiceProvider = 'cartesia' | 'chatterbox';

export interface VoiceProfile {
  id: string;
  key: string;
  name: string;
  speed: number;
  volume: number;
  provider: VoiceProvider;
}

/** Local (Chatterbox) voices are named, not UUIDs: `chatterbox:<name>`. */
export const CHATTERBOX_VOICE_PREFIX = 'chatterbox:';

export const VOICE_PROFILES: readonly VoiceProfile[] = [
  {
    id: '78a05d7d-268b-4a18-aad7-7a96902a95ee',
    key: 'jarvis',
    name: 'Jarvis',
    speed: 1.0,
    volume: 1.9,
    provider: 'cartesia',
  },
  {
    id: 'e23c9ecf-e002-4f7a-8e39-13d18d09923f',
    key: 'frieren',
    name: 'Frieren',
    speed: 0.9,
    volume: 1.9,
    provider: 'cartesia',
  },
  {
    // The cloned voice from the user's reference recording, spoken by the
    // local sidecar. Speed has no meaning there; volume is the player's.
    id: 'chatterbox:jarvis',
    key: 'jarvis-local',
    name: 'Jarvis (local)',
    speed: 1.0,
    volume: 1.9,
    provider: 'chatterbox',
  },
] as const;

export const DEFAULT_VOICE_PROFILE = VOICE_PROFILES[0];
export const DEFAULT_LOCAL_VOICE_PROFILE = VOICE_PROFILES[2];

export function isChatterboxVoiceId(id: unknown): id is string {
  return typeof id === 'string' && id.startsWith(CHATTERBOX_VOICE_PREFIX);
}

/** A profile for a local voice the server reported (not in the static list). */
export function chatterboxProfile(name: string): VoiceProfile {
  const id = CHATTERBOX_VOICE_PREFIX + name;
  return (
    VOICE_PROFILES.find((p) => p.id === id) ?? {
      id,
      key: `${name}-local`,
      name: `${name.charAt(0).toUpperCase()}${name.slice(1)} (local)`,
      speed: 1.0,
      volume: 1.9,
      provider: 'chatterbox',
    }
  );
}

/**
 * The voice to speak with, given the provider in force. A stored voice from
 * the other provider is not an error -- the user switched providers -- so
 * it falls back to that provider's default rather than the wrong engine.
 */
export function getVoiceProfile(id: string, provider: VoiceProvider = 'cartesia'): VoiceProfile {
  if (provider === 'chatterbox') {
    if (isChatterboxVoiceId(id)) return chatterboxProfile(id.slice(CHATTERBOX_VOICE_PREFIX.length));
    return DEFAULT_LOCAL_VOICE_PROFILE;
  }
  const found = VOICE_PROFILES.find((profile) => profile.id === id);
  return found && found.provider === 'cartesia' ? found : DEFAULT_VOICE_PROFILE;
}

export function isKnownVoiceId(id: unknown): id is string {
  if (isChatterboxVoiceId(id)) return id.length > CHATTERBOX_VOICE_PREFIX.length;
  return typeof id === 'string' && VOICE_PROFILES.some((profile) => profile.id === id);
}

export function profilesFor(provider: VoiceProvider, localVoices: readonly string[] = []): VoiceProfile[] {
  if (provider === 'chatterbox') {
    const names = localVoices.length ? localVoices : ['jarvis'];
    return names.map(chatterboxProfile);
  }
  return VOICE_PROFILES.filter((p) => p.provider === 'cartesia');
}
