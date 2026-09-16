import { describe, expect, it } from 'vitest';
import {
  hasConfidentStopWord,
  interruptReason,
  isEchoTurn,
  isStopCommand,
  shouldInterrupt,
  wordCount,
} from './barge-in';

const w = (word: string, confidence = 0.95) => ({ word, confidence });

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

  it('cuts on one confident stop word, and says so', () => {
    expect(shouldInterrupt(live, 'stop', [w('stop')])).toBe(true);
    expect(shouldInterrupt(live, 'Wait.', [w('Wait.')])).toBe(true);
    expect(shouldInterrupt(live, 'hold on', [w('hold'), w('on')])).toBe(true);
    expect(interruptReason([w('stop')])).toBe('stop-word');
    expect(interruptReason([w('the'), w('weather')])).toBe('words');
  });

  it('does not trust an unsure stop word alone, nor the name alone', () => {
    expect(shouldInterrupt(live, 'stop', [w('stop', 0.6)])).toBe(false);
    expect(shouldInterrupt(live, 'sage', [w('sage')])).toBe(false);
    // Without word data there is nothing to be confident about.
    expect(shouldInterrupt(live, 'stop')).toBe(false);
  });
});

describe('hasConfidentStopWord', () => {
  it('needs both words of a two-word phrase confident and adjacent', () => {
    expect(hasConfidentStopWord([w('hold'), w('on', 0.5)])).toBe(false);
    expect(hasConfidentStopWord([w('hold'), w('it'), w('on')])).toBe(false);
    expect(hasConfidentStopWord([w('no'), w('hang'), w('on')])).toBe(true);
  });

  it('ignores stop words inside other words', () => {
    expect(hasConfidentStopWord([w('stopwatch')])).toBe(false);
    expect(hasConfidentStopWord([w('waiter')])).toBe(false);
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
