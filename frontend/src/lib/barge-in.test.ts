import { describe, expect, it } from 'vitest';
import { isEchoTurn, shouldInterrupt, wordCount } from './barge-in';

const live = { enabled: true, sageSpeaking: true, voiceReply: true, triggered: false };

describe('shouldInterrupt', () => {
  it('cuts on the second real word, not the first', () => {
    expect(shouldInterrupt(live, 'wait')).toBe(false);
    expect(shouldInterrupt(live, 'wait no')).toBe(true);
  });

  it('does not count punctuation or noise tokens as words', () => {
    expect(shouldInterrupt(live, '... -')).toBe(false);
    expect(wordCount('um, ...')).toBe(1);
  });

  it('never when Sage is not speaking, the switch is off, or the reply was typed', () => {
    expect(shouldInterrupt({ ...live, sageSpeaking: false }, 'wait no stop')).toBe(false);
    expect(shouldInterrupt({ ...live, enabled: false }, 'wait no stop')).toBe(false);
    expect(shouldInterrupt({ ...live, voiceReply: false }, 'wait no stop')).toBe(false);
  });

  it('only once per turn', () => {
    expect(shouldInterrupt({ ...live, triggered: true }, 'wait no stop')).toBe(false);
  });
});

describe('isEchoTurn', () => {
  it('treats a turn that ended under playback without ever triggering as echo', () => {
    expect(isEchoTurn(live)).toBe(true);
  });

  it('is a real turn once it triggered, or once Sage has stopped', () => {
    expect(isEchoTurn({ ...live, triggered: true })).toBe(false);
    expect(isEchoTurn({ ...live, sageSpeaking: false })).toBe(false);
  });
});
