/**
 * The orb's state, as one pure decision so every orb agrees and the rule
 * can be tested. "Speaking" is reserved for actual audio; "listening"
 * covers the mic and generation; "away" is the desk being empty (M36) --
 * the orb dims and slows for nobody, and brightens as the user sits down.
 */

export type OrbState = 'idle' | 'listening' | 'speaking' | 'away';

export interface OrbInputs {
  audioPlaying: boolean;
  streamingHere: boolean;
  voiceState: string;
  presenceState: string;
}

export function resolveOrbState(inputs: OrbInputs): OrbState {
  if (inputs.audioPlaying) return 'speaking';
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

export function orbStateLabel(state: OrbState): string {
  switch (state) {
    case 'listening':
      return 'Listening';
    case 'speaking':
      return 'Speaking';
    case 'away':
      return 'Away';
    default:
      return 'Standing by';
  }
}
