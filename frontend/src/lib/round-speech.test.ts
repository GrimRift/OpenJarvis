import { describe, expect, it } from 'vitest';
import { MAX_HOLD_MS, RoundSpeech, SPEAK_AHEAD_CHARS } from './round-speech';

function gate() {
  const said: string[] = [];
  return { speech: new RoundSpeech((d) => said.push(d)), said: () => said.join('') };
}

const OPENING = 'A'.repeat(SPEAK_AHEAD_CHARS - 10);

describe('RoundSpeech long answers (13 October: 14 s of silence in a story)', () => {
  it("says a cloud model's round as it streams, never holding", () => {
    const said: string[] = [];
    const speech = new RoundSpeech((d) => said.push(d), { holdDrafts: false });
    speech.push(`${OPENING} and more words. Then the story`);
    speech.push(' goes on.');
    expect(said.join('')).toBe(`${OPENING} and more words. Then the story goes on.`);
  });

  it('a held round still writing after MAX_HOLD_MS is released as the answer', () => {
    const said: string[] = [];
    let clock = 1000;
    const speech = new RoundSpeech((d) => said.push(d), { now: () => clock });
    speech.push(`${OPENING} and more words. First held.`);
    expect(said.join('')).toBe(`${OPENING} and more words.`);
    clock += MAX_HOLD_MS - 1;
    speech.push(' Still held.');
    expect(said.join('')).toBe(`${OPENING} and more words.`);
    clock += 1;
    speech.push(' Released.');
    speech.push(' Then streamed.');
    expect(said.join('')).toBe(
      `${OPENING} and more words. First held. Still held. Released. Then streamed.`,
    );
  });

  it('a retract within MAX_HOLD_MS still drops the held draft', () => {
    const said: string[] = [];
    let clock = 0;
    const speech = new RoundSpeech((d) => said.push(d), { now: () => clock });
    speech.push(`${OPENING} and more words. Draft answer.`);
    clock += MAX_HOLD_MS / 2;
    speech.retract();
    expect(said.join('')).toBe(`${OPENING} and more words.`);
  });
});

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

  it('a restored short preamble is not said again', () => {
    // 13 October: the server gives the preamble back as the answer when the
    // model says nothing after its tool; it had been said in full.
    const { speech, said } = gate();
    speech.push("I'll remind you at 12:19, Sir.");
    speech.retract();
    speech.restore("I'll remind you at 12:19, Sir.");
    speech.push("I'll remind you at 12:19, Sir.");
    speech.release();
    expect(said()).toBe("I'll remind you at 12:19, Sir.");
  });

  it('a restored long draft says only the part it held', () => {
    const { speech, said } = gate();
    const draft = `${OPENING} and more words. Hamilton needs 106 points. He can still win.`;
    speech.push(draft.slice(0, 120));
    speech.push(draft.slice(120));
    speech.retract();
    speech.restore(draft);
    speech.push(draft.slice(0, 50));
    speech.push(draft.slice(50));
    expect(said()).toBe(draft);
  });

  it('a restore that does not start with what was said is said whole', () => {
    const { speech, said } = gate();
    speech.push('Let me check, Sir.');
    speech.retract();
    speech.restore('Something else entirely.');
    speech.push('Something else entirely.');
    expect(said()).toBe('Let me check, Sir.Something else entirely.');
  });

  it('a new round after a restore-free retract is said as before', () => {
    const { speech, said } = gate();
    speech.push('Let me check, Sir.');
    speech.retract();
    speech.push('Let me check, Sir. It is sunny.');
    speech.release();
    expect(said()).toBe('Let me check, Sir.Let me check, Sir. It is sunny.');
  });

  it('finishes the sentence it is in before holding', () => {
    const { speech, said } = gate();
    speech.push(`${OPENING} still going`);
    speech.push(' on and on');
    expect(said()).toBe(`${OPENING} still going on and on`);
  });
});
