import { describe, expect, it } from 'vitest';
import {
  countedWords,
  describeVerdict,
  hasConfidentStopWord,
  isEchoOf,
  isEchoTurn,
  isGarbled,
  isStopCommand,
  judge,
  shouldInterrupt,
  wordCount,
} from './barge-in';

const live = { enabled: true, sageSpeaking: true, voiceReply: true, triggered: false };
const w = (word: string, confidence = 0.95) => ({ word, confidence });
const say = (text: string, confidence = 0.95) => text.split(' ').map((t) => w(t, confidence));
const REPLY = 'The concrete needs to cure for seven days. Wait for it to reach strength before loading.';

describe('judge: conservative by default', () => {
  it('waits for the third confident word, then cuts', () => {
    expect(judge(say('actually I'), REPLY)).toEqual({ decision: 'wait', reason: 'too-few' });
    expect(judge(say('actually I meant'), REPLY)).toEqual({ decision: 'cut', reason: 'words' });
  });

  it('cuts on one confident stop word', () => {
    for (const said of ['stop', 'Wait.', 'hold on', 'no hang on']) {
      expect(judge(say(said), ''), said).toEqual({ decision: 'cut', reason: 'stop-word' });
    }
  });

  it('does not trust an unsure stop word, nor the name alone', () => {
    expect(judge([w('stop', 0.6)], '')).toEqual({ decision: 'wait', reason: 'too-few' });
    expect(judge([w('sage')], '')).toEqual({ decision: 'wait', reason: 'too-few' });
  });

  it('rejects a run of what Sage said, even a long one', () => {
    expect(judge(say('cure for seven days'), REPLY)).toEqual({ decision: 'reject', reason: 'echo' });
    // A stop word Sage itself said does not stop it.
    expect(judge(say('wait for it'), REPLY)).toEqual({ decision: 'reject', reason: 'echo' });
  });

  it('is not echo once a word of the user\'s own is added', () => {
    expect(judge(say('why seven days'), REPLY)).toEqual({ decision: 'cut', reason: 'words' });
  });

  it('rejects enough words Deepgram is not sure of', () => {
    expect(judge(say('what about the', 0.6), '')).toEqual({ decision: 'reject', reason: 'low-confidence' });
  });

  it('does not count words under half confidence, and waits on none', () => {
    expect(judge(say('mumble mumble mumble', 0.3), '')).toEqual({ decision: 'wait', reason: 'no-words' });
    expect(judge([w('the', 0.4), w('weather'), w('tomorrow')], '')).toEqual({ decision: 'wait', reason: 'too-few' });
  });

  it('rejects the recogniser stuttering', () => {
    expect(judge(say('the the the the'), '')).toEqual({ decision: 'reject', reason: 'garbled' });
  });
});

describe('judge: modes', () => {
  it('balanced cuts on two words at a lower confidence', () => {
    expect(judge(say('actually I', 0.65), REPLY, 'balanced')).toEqual({ decision: 'cut', reason: 'words' });
    expect(judge(say('actually I', 0.65), REPLY, 'conservative')).toEqual({ decision: 'wait', reason: 'too-few' });
  });

  it('sensitive cuts on one very sure word', () => {
    expect(judge([w('why', 0.9)], REPLY, 'sensitive')).toEqual({ decision: 'cut', reason: 'single-word' });
    expect(judge([w('why', 0.7)], REPLY, 'sensitive')).toEqual({ decision: 'wait', reason: 'too-few' });
    expect(judge([w('why', 0.9)], REPLY, 'balanced')).toEqual({ decision: 'wait', reason: 'too-few' });
  });

  it('echo and stop words are mode-independent', () => {
    expect(judge(say('seven days'), REPLY, 'sensitive').decision).toBe('reject');
    expect(judge(say('stop'), '', 'conservative').decision).toBe('cut');
  });
});

describe('shouldInterrupt', () => {
  it('never when Sage is not speaking, the switch is off, the reply was typed, or already cut', () => {
    const said = say('wait no stop');
    expect(shouldInterrupt(live, said, '')).toBe(true);
    expect(shouldInterrupt({ ...live, sageSpeaking: false }, said, '')).toBe(false);
    expect(shouldInterrupt({ ...live, enabled: false }, said, '')).toBe(false);
    expect(shouldInterrupt({ ...live, voiceReply: false }, said, '')).toBe(false);
    expect(shouldInterrupt({ ...live, triggered: true }, said, '')).toBe(false);
  });
});

describe('rules on their own', () => {
  it('wordCount ignores punctuation and noise tokens', () => {
    expect(wordCount('um, ...')).toBe(1);
    expect(wordCount('... -')).toBe(0);
  });

  it('countedWords drops noise tokens and unsure words', () => {
    expect(countedWords([w('...'), w('um', 0.2), w('yes')])).toEqual([w('yes')]);
  });

  it('hasConfidentStopWord needs both words of a phrase confident and adjacent', () => {
    expect(hasConfidentStopWord([w('hold'), w('on', 0.5)])).toBe(false);
    expect(hasConfidentStopWord([w('hold'), w('it'), w('on')])).toBe(false);
    expect(hasConfidentStopWord([w('stopwatch')])).toBe(false);
    expect(hasConfidentStopWord([w('waiter')])).toBe(false);
  });

  it('isEchoOf matches a run regardless of case and punctuation', () => {
    expect(isEchoOf(say('Seven days.'), REPLY)).toBe(true);
    expect(isEchoOf(say('seven strength'), REPLY)).toBe(false);
    expect(isEchoOf([], REPLY)).toBe(false);
    expect(isEchoOf(say('anything'), '')).toBe(false);
  });

  it('isEchoOf survives a dropped word, a numeral, and a small gap', () => {
    expect(isEchoOf(say('cure for 7 days'), REPLY)).toBe(true);
    expect(isEchoOf(say('needs cure for seven days'), REPLY)).toBe(true);
    expect(isEchoOf(say('reach strength loading'), REPLY)).toBe(true);
    // Half the words being Sage's is a person using Sage's words.
    expect(isEchoOf(say('seven days is too long'), REPLY)).toBe(false);
    expect(isEchoOf(say('what about the strength'), REPLY)).toBe(false);
  });

  it('isGarbled needs three of the same token', () => {
    expect(isGarbled(say('no no'))).toBe(false);
    expect(isGarbled(say('no no no'))).toBe(true);
  });
});

describe('isEchoTurn', () => {
  it('treats a turn that ended under playback without ever cutting as echo', () => {
    expect(isEchoTurn(live)).toBe(true);
    expect(isEchoTurn({ ...live, triggered: true })).toBe(false);
    expect(isEchoTurn({ ...live, sageSpeaking: false })).toBe(false);
  });
});

describe('describeVerdict', () => {
  it('names a cut, explains a rejection with words, and says nothing for noise', () => {
    expect(describeVerdict({ decision: 'cut', reason: 'stop-word' }, 'stop')).toBe('You stopped Sage: "stop"');
    expect(describeVerdict({ decision: 'cut', reason: 'words' }, 'why seven days')).toBe(
      'You interrupted Sage: "why seven days"',
    );
    expect(describeVerdict({ decision: 'reject', reason: 'low-confidence' }, 'what about')).toBe(
      'Ignored while Sage spoke: "what about" (low confidence)',
    );
    expect(describeVerdict({ decision: 'reject', reason: 'echo' }, '  ')).toBeNull();
    expect(describeVerdict({ decision: 'wait', reason: 'too-few' }, 'why')).toBeNull();
  });
});

describe('isStopCommand', () => {
  it('recognises a bare stop, however it is dressed', () => {
    for (const said of ['stop', 'Okay. You can stop. No.', "that's enough, Sage", 'shut up', 'okay okay stop stop', 'wait', 'hold on', 'okay pause']) {
      expect(isStopCommand(said), said).toBe(true);
    }
  });

  it('is not a stop when there is a question in it', () => {
    for (const said of ['stop and tell me the weather', 'wait, make it shorter', 'no, the other one', 'stop, what about tomorrow']) {
      expect(isStopCommand(said), said).toBe(false);
    }
  });
});
