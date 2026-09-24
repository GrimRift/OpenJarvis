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
    name: 'Jarvis (Local)',
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

/**
 * The two local models (voice_sidecar/engine.py MODELS). Each local voice
 * belongs to one, and choosing the voice is choosing its model: the server
 * loads it when the voice is picked.
 */
export type LocalEngine = 'nano' | 'turbo';
export const LOCAL_ENGINES: readonly LocalEngine[] = ['nano', 'turbo'];

/** What the server reports for a local voice, as far as naming goes. */
export interface LocalVoiceMeta {
  name: string;
  label?: string;
  engine?: string;
}

export function localEngineOf(voice: LocalVoiceMeta | undefined): LocalEngine {
  return voice?.engine === 'turbo' ? 'turbo' : 'nano';
}

/** A profile for a local voice the server reported (not in the static list). */
export function chatterboxProfile(voice: string | LocalVoiceMeta): VoiceProfile {
  const meta = typeof voice === 'string' ? { name: voice } : voice;
  const id = CHATTERBOX_VOICE_PREFIX + meta.name;
  const known = VOICE_PROFILES.find((p) => p.id === id);
  // "J.A.R.V.I.S." exists on both models, so the display name comes from the
  // voice, and the id (its folder) keeps them apart.
  const shown = meta.label || `${meta.name.charAt(0).toUpperCase()}${meta.name.slice(1)}`;
  if (known && !meta.label) return known;
  return {
    id,
    key: `${meta.name}-local`,
    name: `${shown} (Local)`,
    speed: 1.0,
    volume: 1.9,
    provider: 'chatterbox',
  };
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

/**
 * The voices the picker offers. For the local provider, only the voices of
 * *engine* when one is given.
 */
export function profilesFor(
  provider: VoiceProvider,
  localVoices: readonly (string | LocalVoiceMeta)[] = [],
  engine?: LocalEngine,
): VoiceProfile[] {
  if (provider === 'chatterbox') {
    const metas = localVoices.map((v) => (typeof v === 'string' ? { name: v } : v));
    const shown = engine ? metas.filter((m) => localEngineOf(m) === engine) : metas;
    if (shown.length) return shown.map(chatterboxProfile);
    return engine === 'turbo' ? [] : [chatterboxProfile('jarvis')];
  }
  return VOICE_PROFILES.filter((p) => p.provider === 'cartesia');
}

/** The voice to switch to when *engine* is chosen: the current one if it is
 * that engine's, else that engine's first. */
export function voiceForEngine(
  engine: LocalEngine,
  currentId: string,
  localVoices: readonly LocalVoiceMeta[],
): string | undefined {
  const mine = localVoices.filter((v) => localEngineOf(v) === engine);
  const current = mine.find((v) => CHATTERBOX_VOICE_PREFIX + v.name === currentId);
  const pick = current ?? mine.find((v) => v.name === 'jarvis') ?? mine[0];
  return pick ? CHATTERBOX_VOICE_PREFIX + pick.name : undefined;
}
