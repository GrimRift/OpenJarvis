import { describe, it, expect } from 'vitest';
import { shouldSynthesizeReplyAudio } from './audio-policy';

/**
 * Stopping a reply mid-generation should leave nothing behind to play.
 *
 * Reported from real use: pressing Stop briefly showed a player with a
 * slider that then vanished. The turn ended as an AbortError, the streamed
 * speech reported that nothing had been spoken, and the batch fallback
 * synthesised the partial answer anyway -- mounting AudioPlayer for a clip
 * that then failed to load, which is what made it disappear again.
 *
 * These tests pin why the fix could not live in this policy function: it
 * cannot see that a turn was stopped, and a partial answer is
 * indistinguishable from a short one. The guard is a `userStopped` flag in
 * InputArea, set when the stream ends in an AbortError and checked before
 * either synthesis path runs.
 */
describe('the reply-audio policy cannot see a stopped turn', () => {
  it('would speak the stop placeholder if asked', () => {
    // Not a bug in this function -- it is the evidence that the decision
    // has to be made where the abort is known.
    expect(
      shouldSynthesizeReplyAudio(true, '', false, '(Generation stopped)'),
    ).toBe(true);
  });

  it('would speak a half-finished answer, which reads as a short one', () => {
    expect(shouldSynthesizeReplyAudio(true, '', false, 'Partial ans')).toBe(
      true,
    );
  });
});

describe('what the policy does decide', () => {
  it('a reply that already carries agent audio is never re-synthesised', () => {
    expect(shouldSynthesizeReplyAudio(true, '', true, 'The briefing')).toBe(
      false,
    );
  });

  it('a typed turn is not spoken', () => {
    expect(shouldSynthesizeReplyAudio(false, '', false, 'An answer')).toBe(
      false,
    );
  });

  it('an empty response is not spoken', () => {
    expect(shouldSynthesizeReplyAudio(true, '', false, '')).toBe(false);
  });
});
