import { describe, expect, it } from 'vitest';
import { isOnlyWakePhrase, stripWakePhrase } from './wake-follow';

describe('stripWakePhrase', () => {
  it('takes the phrase off the front, however Deepgram spelt it', () => {
    expect(stripWakePhrase('Hey Sage, any news on AI?')).toBe('any news on AI?');
    expect(stripWakePhrase('hey stage tell me when it is 1 pm')).toBe('tell me when it is 1 pm');
    expect(stripWakePhrase('Sage, what time is it?')).toBe('what time is it?');
    expect(stripWakePhrase('  Hey Sage.  ')).toBe('');
    expect(stripWakePhrase('Sage')).toBe('');
  });

  it('knows the name by shape when Deepgram spells it oddly', () => {
    // Heard for a pre-rolled "Hey Sage" on 17 September, and sent as messages.
    expect(stripWakePhrase('acage.')).toBe('');
    expect(stripWakePhrase('Usage.')).toBe('');
    expect(stripWakePhrase('hazage any news')).toBe('any news');
    expect(stripWakePhrase('Hey acage, what time is it')).toBe('what time is it');
    expect(stripWakePhrase('Peace Sage.')).toBe('');
    // Debris with no s-sound at all, heard live with the longer pre-roll.
    expect(stripWakePhrase("ACGE. What's up?")).toBe("What's up?");
    expect(stripWakePhrase('His age.')).toBe('');
    expect(stripWakePhrase('ACG.')).toBe('');
    expect(stripWakePhrase('He said, tell me a joke')).toBe('tell me a joke');
  });

  it('does not take a short real word for the name', () => {
    expect(stripWakePhrase('say hello to mom')).toBe('say hello to mom');
    expect(stripWakePhrase('what time is it')).toBe('what time is it');
    expect(stripWakePhrase('open youtube')).toBe('open youtube');
    expect(stripWakePhrase('play some music')).toBe('play some music');
    expect(stripWakePhrase('yes')).toBe('yes');
  });

  it('leaves a transcript that is not the phrase alone', () => {
    expect(stripWakePhrase('what time is it')).toBe('what time is it');
    // A bare "hey" in a wake-opened turn is the phrase with the name dropped.
    expect(stripWakePhrase('hey what is this')).toBe('what is this');
    expect(stripWakePhrase('')).toBe('');
  });
});

describe('isOnlyWakePhrase', () => {
  it('is true for the phrase alone and false otherwise', () => {
    expect(isOnlyWakePhrase('Hey Sage.')).toBe(true);
    expect(isOnlyWakePhrase('hey sage any news')).toBe(false);
    expect(isOnlyWakePhrase('')).toBe(false);
  });
});

describe('the pause', () => {
  it('a short turn that ends right after the wake word is the phrase, however spelt', async () => {
    const { isOnlyWakePhrase: only, PAUSE_TURN_MS } = await import('./wake-follow');
    expect(only('ACG.', 900)).toBe(true);
    expect(only('age.', 1200)).toBe(true);
    // A real short answer right after the wake word is still the pause...
    expect(only('yes', 900)).toBe(true);
    // ...but not once the window has passed, and never for a whole question.
    expect(only('yes', PAUSE_TURN_MS + 1)).toBe(false);
    expect(only('what time is it', 900)).toBe(false);
  });

  it('the greeting timer counts from the end of the phrase', async () => {
    const { greetingDelayMs, GREETING_MIN_DELAY_MS } = await import('./wake-follow');
    expect(greetingDelayMs(0)).toBe(1000);
    expect(greetingDelayMs(700)).toBe(300);
    expect(greetingDelayMs(1500)).toBe(GREETING_MIN_DELAY_MS);
  });

  it('only words beyond the phrase cancel the greeting', async () => {
    const { continuesPastWakePhrase } = await import('./wake-follow');
    expect(continuesPastWakePhrase('Hey Sage')).toBe(false);
    expect(continuesPastWakePhrase('Usage.')).toBe(false);
    expect(continuesPastWakePhrase('Hey Sage any')).toBe(true);
  });
});

describe('a word that arrives after Sage has already greeted', () => {
  it('is the phrase misheard, however late and however spelt', async () => {
    const { isOnlyWakePhrase: only, PAUSE_TURN_MS } = await import('./wake-follow');
    // 18 September: "Hey Sage" came back as "addition." and the turn ended
    // 1849 ms in -- 49 ms past the window -- so it was sent as a message.
    // The greeting had already played, which means the room was silent.
    expect(only('addition.', 1849, true)).toBe(true);
    expect(only('addition.', 1849, false)).toBe(false);
    expect(only('Usage.', PAUSE_TURN_MS + 4000, true)).toBe(true);
  });

  it('still never swallows a real question', async () => {
    const { isOnlyWakePhrase: only } = await import('./wake-follow');
    expect(only('what time is it', 900, true)).toBe(false);
    expect(only('any news on AI', 400, true)).toBe(false);
  });

  it('does not swallow a two-word answer to the greeting', async () => {
    const { isOnlyWakePhrase: only, PAUSE_TURN_MS } = await import('./wake-follow');
    // "Yes, Sir?" is a question, and the answer to it is often two words.
    // Every one of these did nothing at all, while the same thing said in
    // three words went through.
    for (const said of ["what's up", 'hello there', 'never mind', 'go on', 'thank you']) {
      expect(only(said, PAUSE_TURN_MS + 4000, true), said).toBe(false);
    }
  });

  it('past the greeting the clock no longer matters', async () => {
    const { isOnlyWakePhrase: only } = await import('./wake-follow');
    // A short answer that happens to land inside the pause window is still
    // an answer: the greeting already proved the user had stopped.
    expect(only("what's up", 400, true)).toBe(false);
  });

  it('a two-word turn that begins like a request is one, even inside the window', async () => {
    const { isOnlyWakePhrase: only } = await import('./wake-follow');
    // Eager end-of-turn closes "Hey Sage" alone, then "what's up" arrives
    // as its own turn a second later -- inside the window, two tokens.
    expect(only("what's up", 900, false)).toBe(false);
    expect(only('Whats up?', 900, false)).toBe(false);
    expect(only('open Obsidian', 900, false)).toBe(false);
    // Phrase debris still is the phrase: neither token starts a request.
    expect(only('ACG age', 900, false)).toBe(true);
    expect(only('his age', 900, false)).toBe(true);
    expect(only('yes', 900, false)).toBe(true);
  });
});
