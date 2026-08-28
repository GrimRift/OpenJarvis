import { describe, expect, it } from 'vitest';
import { shouldSynthesizeReplyAudio } from './audio-policy';

describe('shouldSynthesizeReplyAudio', () => {
  it('defers voice replies and morning digests to the browser TTS path', () => {
    expect(shouldSynthesizeReplyAudio(true, 'Who are you?', false, 'I am Sage.')).toBe(true);
    expect(shouldSynthesizeReplyAudio(false, 'Show me my morning digest.', false, 'Briefing.')).toBe(true);
  });

  it('does not duplicate built-in audio or synthesize unrelated typed replies', () => {
    expect(shouldSynthesizeReplyAudio(true, 'Hello', true, 'Hello, sir.')).toBe(false);
    expect(shouldSynthesizeReplyAudio(false, 'Hello', false, 'Hello, sir.')).toBe(false);
    expect(shouldSynthesizeReplyAudio(true, 'Hello', false, '')).toBe(false);
  });
});
