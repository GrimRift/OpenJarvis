import { describe, expect, it } from 'vitest';
import { clipsForVoice } from './greeting';

const manifest = {
  default_voice_id: 'jarvis',
  voices: {
    jarvis: ['greetings/jarvis/hello-sir-sonic36.mp3'],
    frieren: ['greetings/frieren/hello-sir-sonic36.mp3'],
  },
};

describe('wake greeting voice selection', () => {
  it('selects the active voice recordings', () => {
    expect(clipsForVoice(manifest, 'frieren')).toEqual(manifest.voices.frieren);
  });

  it('falls back to Jarvis when a removed voice was stored', () => {
    expect(clipsForVoice(manifest, 'removed')).toEqual(manifest.voices.jarvis);
  });
});
