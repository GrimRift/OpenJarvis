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
    expect(stripWakePhrase('He said, tell me a joke')).toBe('tell me a joke');
  });

  it('does not take a short real word for the name', () => {
    expect(stripWakePhrase('say hello to mom')).toBe('say hello to mom');
    expect(stripWakePhrase('see you')).toBe('see you');
  });

  it('leaves a transcript that is not the phrase alone', () => {
    expect(stripWakePhrase('what time is it')).toBe('what time is it');
    expect(stripWakePhrase('hey what is this')).toBe('hey what is this');
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
    expect(only('age.', PAUSE_TURN_MS + 1)).toBe(false);
    // Two real words within the window are still a question later on.
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
