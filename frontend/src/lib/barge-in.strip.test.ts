import { describe, expect, it } from 'vitest';
import { cleanOverSage, stripEcho, type FluxWord } from './barge-in';

const w = (text: string): FluxWord[] =>
  text.split(' ').map((word) => ({ word, confidence: 0.95 }));

const SAID =
  'The weather in Manila today is sunny with a high of thirty-two degrees, and a light breeze.';

describe('stripEcho', () => {
  it("cuts Sage's words out of the user's sentence", () => {
    const { kept, removed } = stripEcho(w('sunny with a stop what about tomorrow?'), SAID);
    expect(kept.map((x) => x.word).join(' ')).toBe('stop what about tomorrow?');
    expect(removed).toHaveLength(3);
  });

  it('cuts a run wherever it falls, including after the user', () => {
    const { kept } = stripEcho(w('what about Cebu high of thirty two degrees'), SAID);
    expect(kept.map((x) => x.word).join(' ')).toBe('what about Cebu');
  });

  it('leaves two shared words alone: people say "the weather" too', () => {
    const { removed } = stripEcho(w("what's the weather in Cebu"), 'It is sunny. The weather is fine.');
    expect(removed).toEqual([]);
  });

  it('matches through punctuation, case and inflection', () => {
    const { kept } = stripEcho(w('Sunny, with a... wait'), SAID);
    expect(kept.map((x) => x.word)).toEqual(['wait']);
  });

  it('removes everything when the whole turn was Sage', () => {
    expect(stripEcho(w('today is sunny with a high'), SAID).kept).toEqual([]);
  });

  it('does nothing without anything Sage said', () => {
    expect(stripEcho(w('sunny with a'), '').removed).toEqual([]);
  });
});

describe('cleanOverSage', () => {
  it('returns the cleaned text and how much was cut', () => {
    expect(cleanOverSage('sunny with a stop', w('sunny with a stop'), SAID)).toEqual({
      text: 'stop',
      removed: 3,
    });
  });

  it('only looks at what Sage said recently', () => {
    const long = `${SAID} ${'Other words follow here. '.repeat(30)}`;
    expect(cleanOverSage('sunny with a stop', undefined, long).removed).toBe(0);
  });

  it("keeps the user's words when their voice repeated Sage's", () => {
    expect(cleanOverSage('sunny with a high', undefined, SAID, 'user')).toEqual({
      text: 'sunny with a high',
      removed: 0,
    });
    expect(cleanOverSage('sunny with a high', undefined, SAID).text).toBe('');
  });
});
