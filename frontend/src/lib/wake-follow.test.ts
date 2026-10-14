import { describe, expect, it } from 'vitest';
import {
  PHRASE_LOUDNESS_SHARE,
  PRE_DETECTION_AUDIO_MS,
  isGreetingEchoFragment,
  isOnlyWakePhrase,
  phraseLoudness,
  restartsPause,
  stripLeadingWakePhrase,
  stripWakePhrase,
} from './wake-follow';

describe('first words the user keeps (14 October)', () => {
  it('keeps a short first word that begins a sentence', () => {
    expect(stripWakePhrase("I'll do that. Thanks.")).toBe("I'll do that. Thanks.");
    expect(stripWakePhrase("We should leave at six.")).toBe('We should leave at six.');
    expect(stripWakePhrase("It's too loud.")).toBe("It's too loud.");
    expect(stripWakePhrase('Yeah go ahead and play it')).toBe('Yeah go ahead and play it');
    expect(stripWakePhrase("Hey Sage, I'll be late")).toBe("I'll be late");
  });

  it('still drops what is left of the phrase', () => {
    expect(stripWakePhrase('ACGE what time is it')).toBe('what time is it');
    expect(stripWakePhrase('his age what time is it')).toBe('what time is it');
  });

  it('after a phrase-only turn strips only a said-again "hey sage"', () => {
    expect(stripLeadingWakePhrase("I'll do that. Thanks.")).toBe("I'll do that. Thanks.");
    expect(stripLeadingWakePhrase('ACGE what time is it')).toBe('ACGE what time is it');
    expect(stripLeadingWakePhrase('Hey Sage, what time is it')).toBe('what time is it');
    expect(stripLeadingWakePhrase('Sage. Play music')).toBe('Play music');
  });

  it('does not take "I\'ll check" after the greeting for the phrase', () => {
    expect(isOnlyWakePhrase("I'll check", 3000, true)).toBe(false);
  });
});

describe('isGreetingEchoFragment ("Yes. And" was sent, 14 October)', () => {
  it('drops a piece of the greeting heard back soon after it', () => {
    expect(isGreetingEchoFragment('Yes. And', 1800, 'unsure')).toBe(true);
    expect(isGreetingEchoFragment("Yes, I'm", 1200, undefined)).toBe(true);
    expect(isGreetingEchoFragment('Mm. What is it?', 900, 'sage')).toBe(false); // 4 words
    expect(isGreetingEchoFragment('What is it?', 900, 'sage')).toBe(true);
    // The Frieren greetings since 14 October.
    expect(isGreetingEchoFragment('Hmm?', 1200, 'unsure')).toBe(true);
    expect(isGreetingEchoFragment('Mmm.', 1200, 'sage')).toBe(true);
    expect(isGreetingEchoFragment("I'm here.", 1500, 'unsure')).toBe(true);
    expect(isGreetingEchoFragment('Go on.', 1500, 'sage')).toBe(true);
    expect(isGreetingEchoFragment('Go on YouTube', 1500, 'unsure')).toBe(false);
    // 13:28: the clip's end plus Deepgram's end of turn came 2.77 s in.
    expect(isGreetingEchoFragment('What is it?', 2770, 'sage')).toBe(true);
  });

  it('keeps the user', () => {
    expect(isGreetingEchoFragment('Yes. And', 1800, 'user')).toBe(false);
    expect(isGreetingEchoFragment('Yes, play it', 1800, 'unsure')).toBe(false);
    expect(isGreetingEchoFragment("I'll do that. Thank you.", 1800, 'unsure')).toBe(false);
    expect(isGreetingEchoFragment('Yes. And', 3200, 'unsure')).toBe(false);
  });
});

describe('stripWakePhrase', () => {
  it('drops sound from before the phrase, such as a video playing', () => {
    expect(
      stripWakePhrase("We're on the way and racing almost -- Hey, Sage. Can you close that YouTube tab?"),
    ).toBe('Can you close that YouTube tab?');
    expect(stripWakePhrase('good launch off the line hey sage pause the video')).toBe('pause the video');
  });

  it('keeps a request that only mentions the name later on', () => {
    expect(stripWakePhrase('Hey Sage, tell my brother to say hey Sage to you')).toBe(
      'tell my brother to say hey Sage to you',
    );
    expect(stripWakePhrase('what does usage mean')).toBe('what does usage mean');
  });

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
    // A lead word and one more that begins no request (heard 22 September
    // as a partial, and it cancelled the pause greeting).
    expect(stripWakePhrase('Hey, Steve.')).toBe('');
    expect(stripWakePhrase('Hey Sam')).toBe('');
    // ...but a request word after the lead is a request.
    expect(stripWakePhrase('hey stop')).toBe('stop');
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
    const { greetingDelayMs, GREETING_MIN_DELAY_MS, GREETING_PAUSE_MS } = await import('./wake-follow');
    expect(greetingDelayMs(0)).toBe(GREETING_PAUSE_MS);
    expect(greetingDelayMs(500)).toBe(GREETING_PAUSE_MS - 500);
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
    // And after the greeting, two non-request words are the phrase too.
    expect(only('Eight inch.', 3000, true)).toBe(true);
    expect(only("what's up", 3000, true)).toBe(false);
  });
});

describe('the phrase as a sentence of its own', () => {
  it('is dropped from the front of the request', () => {
    // 20 September: "Hey Sage" was heard as "Eight inch." and sent on as
    // part of the message.
    expect(stripWakePhrase('Eight inch. Give me my daily briefing.')).toBe('Give me my daily briefing.');
    expect(stripWakePhrase('Usage. any news?')).toBe('any news?');
  });

  it('never eats a short first sentence that is a request', () => {
    expect(stripWakePhrase("What's up. Anything new?")).toBe("What's up. Anything new?");
    expect(stripWakePhrase('Yes. Give me my briefing.')).toBe('Yes. Give me my briefing.');
    expect(stripWakePhrase('Open Obsidian. Then play music.')).toBe('Open Obsidian. Then play music.');
  });

  it('leaves a turn that is only that sentence alone', () => {
    // isOnlyWakePhrase decides that case; stripping it here would return
    // an empty string and greet twice.
    expect(stripWakePhrase('Eight inch.')).toBe('Eight inch.');
  });
});

describe('the wake phrase heard as one word', () => {
  it('drops "Change." -- how "Hey Sage" alone was once transcribed', async () => {
    const { stripWakePhrase, isOnlyWakePhrase } = await import('./wake-follow');
    expect(stripWakePhrase('Change.')).toBe('');
    expect(isOnlyWakePhrase('Change.')).toBe(true);
    expect(stripWakePhrase('Sage.')).toBe('');
  });

  it('keeps real requests that begin with the same word', async () => {
    const { stripWakePhrase } = await import('./wake-follow');
    expect(stripWakePhrase('Change the song.')).toBe('Change the song.');
    expect(stripWakePhrase('Hey Sage, change the volume.')).toBe('change the volume.');
  });

  it('keeps one-word commands and one-word questions', async () => {
    const { stripWakePhrase } = await import('./wake-follow');
    for (const word of ['Stop.', 'Next.', 'Mute.', 'Weather?', 'Thanks.']) {
      expect(stripWakePhrase(word)).toBe(word);
    }
  });
});

describe('stripGreetingEcho (24 September: a question said over the greeting was lost)', () => {
  it('takes the greeting out wherever it landed', async () => {
    const { stripGreetingEcho } = await import('./wake-follow');
    expect(stripGreetingEcho('Yes, sir? What time is my class tomorrow')).toBe(
      'What time is my class tomorrow',
    );
    expect(stripGreetingEcho('What time hello sir is my class')).toBe('What time is my class');
    expect(stripGreetingEcho('Sir. Play some music')).toBe('Play some music');
  });

  it('leaves nothing when the turn was only the greeting', async () => {
    const { stripGreetingEcho } = await import('./wake-follow');
    expect(stripGreetingEcho('Yes, sir?')).toBe('');
    expect(stripGreetingEcho('Sir.')).toBe('');
  });

  it('takes out the Frieren greetings, which have no "sir"', async () => {
    const { stripGreetingEcho } = await import('./wake-follow');
    expect(stripGreetingEcho("Yes? I'm listening. What's the weather")).toBe("What's the weather");
    expect(stripGreetingEcho("Yes, I'm listening.")).toBe('');
    expect(stripGreetingEcho("I'm here. Go ahead. Set a timer")).toBe('Set a timer');
    expect(stripGreetingEcho('Mm. What is it? Play some music')).toBe('Play some music');
    expect(stripGreetingEcho('Hmm, what is it?')).toBe('');
  });

  it('keeps questions that only share words with the Frieren greetings', async () => {
    const { stripGreetingEcho } = await import('./wake-follow');
    expect(stripGreetingEcho('What is it like in Tokyo')).toBe('What is it like in Tokyo');
    expect(stripGreetingEcho('Go ahead and play the next song')).toBe(
      'Go ahead and play the next song',
    );
  });

  it('keeps a question that merely contains the word', async () => {
    const { stripGreetingEcho } = await import('./wake-follow');
    expect(stripGreetingEcho('Tell me about sir Isaac Newton')).toBe(
      'Tell me about sir Isaac Newton',
    );
  });
});

describe('the pause after the wake word', () => {
  const tone = (amplitude: number, samples = 1600) =>
    Int16Array.from({ length: samples }, (_, i) => Math.round(amplitude * Math.sin(i / 4)));

  it('takes the wake phrase level from the loudest 100 ms of the pre-roll', () => {
    const preRoll = new Int16Array([...tone(1000, 8000), ...tone(8000), ...tone(1000, 8000)]);
    expect(phraseLoudness(preRoll)).toBeGreaterThan(5000);
    expect(phraseLoudness(undefined)).toBe(0);
  });

  it('ignores what the turn hears before the detection could reach it', () => {
    // The tail of "Sage", still in the microphone buffer.
    expect(restartsPause(7000, 300, 7500, 100)).toBe(false);
    expect(restartsPause(7000, 300, 7500, PRE_DETECTION_AUDIO_MS + 1)).toBe(true);
  });

  it('does not take fan residue or a breath for the user carrying on', () => {
    // Fan ~1,400 against a phrase of 7,500: under a quarter of it.
    expect(restartsPause(1400, 300, 7500, 600)).toBe(false);
    // A follow-on question at speaking level does restart it.
    expect(restartsPause(4000, 300, 7500, 600)).toBe(true);
    expect(PHRASE_LOUDNESS_SHARE * 7500).toBeGreaterThan(1500);
  });

  it('leaves turns that are not wake turns as they were', () => {
    expect(restartsPause(400, 350, 0, 0)).toBe(true);
    expect(restartsPause(300, 350, 0, 5000)).toBe(false);
  });
});
