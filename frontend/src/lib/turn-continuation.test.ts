import { describe, expect, it } from 'vitest';
import {
  CONTINUATION_WINDOW_MS,
  isContinuation,
  mergeTurns,
} from './turn-continuation';

/**
 * "What do you think about that idea we talked about" [pause to remember]
 * "— do you think it's possible?" was answered twice: once for each half.
 * The policy decides whether speech that starts after a submitted turn is
 * the rest of that sentence. It is pure so it can be tested without a mic.
 */

const base = { submittedAt: 10_000, submittedText: 'first half', sageSpeaking: false };

describe('isContinuation', () => {
  it('treats speech shortly after a submitted turn as the same sentence', () => {
    expect(isContinuation(base, 10_000 + 1500)).toBe(true);
  });

  it('accepts speech right up to the window', () => {
    expect(isContinuation(base, 10_000 + CONTINUATION_WINDOW_MS)).toBe(true);
  });

  it('treats speech after the window as a new turn', () => {
    expect(isContinuation(base, 10_000 + CONTINUATION_WINDOW_MS + 1)).toBe(false);
  });

  it('is never a continuation once Sage is audibly speaking', () => {
    // The mic is closed during playback anyway; this is belt and braces so
    // barge-in, which is out of scope, can never be misread as a continuation.
    expect(isContinuation({ ...base, sageSpeaking: true }, 10_000 + 500)).toBe(false);
  });

  it('is never a continuation when nothing was submitted', () => {
    expect(isContinuation({ ...base, submittedAt: null }, 10_000 + 500)).toBe(false);
  });
});

describe('mergeTurns', () => {
  it('joins the two halves into one sentence', () => {
    expect(
      mergeTurns('What do you think about that idea we talked about', 'do you think it is possible?'),
    ).toBe('What do you think about that idea we talked about do you think it is possible?');
  });

  it('drops a dangling dash or comma left by the cut', () => {
    expect(mergeTurns('the idea we talked about —', 'is it possible?')).toBe(
      'the idea we talked about is it possible?',
    );
    expect(mergeTurns('the idea,', 'is it possible?')).toBe('the idea is it possible?');
  });

  it('keeps sentence-ending punctuation on the first half', () => {
    // A full stop means the first half really was a sentence; the merge
    // still sends both as one turn, but does not pretend they were one line.
    expect(mergeTurns('Tell me about it.', 'Is it possible?')).toBe(
      'Tell me about it. Is it possible?',
    );
  });

  it('survives an empty half', () => {
    expect(mergeTurns('', 'only this')).toBe('only this');
    expect(mergeTurns('only this', '   ')).toBe('only this');
  });
});
