/**
 * The orb's state, as one pure decision so every orb agrees and the rule
 * can be tested. "Speaking" is reserved for actual audio; "listening"
 * covers the mic and generation; "away" is the desk being empty (M36) --
 * the orb dims and slows for nobody, and brightens as the user sits down.
 */

export type OrbState = 'idle' | 'listening' | 'speaking' | 'away';

export interface OrbInputs {
  audioPlaying: boolean;
  /** The server is speaking aloud: a reminder, a schedule notice, a moment. */
  serverSpeaking?: boolean;
  streamingHere: boolean;
  voiceState: string;
  presenceState: string;
}

export function resolveOrbState(inputs: OrbInputs): OrbState {
  if (inputs.audioPlaying || inputs.serverSpeaking) return 'speaking';
  if (
    inputs.streamingHere ||
    inputs.voiceState === 'recording' ||
    inputs.voiceState === 'transcribing'
  ) {
    return 'listening';
  }
  if (inputs.presenceState === 'away') return 'away';
  return 'idle';
}

/** Listening, but no longer to the user: the mic is done and Sage is
 * writing its reply. The same orb state; only the label says so. */
export function isGenerating(inputs: OrbInputs): boolean {
  return resolveOrbState(inputs) === 'listening' && inputs.voiceState !== 'recording';
}

export function orbStateLabel(state: OrbState, generating = false): string {
  switch (state) {
    case 'listening':
      return generating ? 'Generating' : 'Listening';
    case 'speaking':
      return 'Speaking';
    case 'away':
      return 'Away';
    default:
      return 'Standing by';
  }
}
