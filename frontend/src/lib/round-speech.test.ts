import { describe, expect, it } from 'vitest';
import { RoundSpeech, SPEAK_AHEAD_CHARS } from './round-speech';

function gate() {
  const said: string[] = [];
  return { speech: new RoundSpeech((d) => said.push(d)), said: () => said.join('') };
}

const OPENING = 'A'.repeat(SPEAK_AHEAD_CHARS - 10);

describe('RoundSpeech', () => {
  it('says a short lead-in whole, as it streams', () => {
    const { speech, said } = gate();
    speech.push("Let me check the standings, Sir.");
    expect(said()).toBe("Let me check the standings, Sir.");
  });

  it('holds a long round after the first sentence end past the limit', () => {
    const { speech, said } = gate();
    speech.push(OPENING);
    speech.push(' and more words. Then the rest');
    speech.push(' of the answer.');
    expect(said()).toBe(`${OPENING} and more words.`);
  });

  it('drops the held part when the round turns out to be a preamble', () => {
    // 10 October: qwen3.5-4b wrote the answer, then searched, then wrote
    // "Updated Analysis"; both were read out.
    const { speech, said } = gate();
    speech.push(`${OPENING} and more words. Hamilton needs 106 points.`);
    speech.retract();
    speech.push('Updated analysis.');
    expect(said()).toBe(`${OPENING} and more words.Updated analysis.`);
  });

  it('says everything once the round is the answer', () => {
    const { speech, said } = gate();
    const text = `${OPENING} and more words. Then the rest. And more.`;
    speech.push(text.slice(0, 170));
    speech.push(text.slice(170));
    speech.release();
    speech.push(' After release.');
    expect(said()).toBe(`${text} After release.`);
  });

  it('a sentence end at the very end of a delta still counts', () => {
    const { speech, said } = gate();
    speech.push(`${OPENING} more words.`);
    speech.push(' Held.');
    expect(said()).toBe(`${OPENING} more words.`);
  });

  it('finishes the sentence it is in before holding', () => {
    const { speech, said } = gate();
    speech.push(`${OPENING} still going`);
    speech.push(' on and on');
    expect(said()).toBe(`${OPENING} still going on and on`);
  });
});
