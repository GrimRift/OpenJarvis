import { describe, expect, it } from 'vitest';
import {
  isDigestPrompt,
  shouldSynthesizeReplyAudio,
  speakableText,
  SPOKEN_REPLY_LIMIT,
} from './audio-policy';

describe('shouldSynthesizeReplyAudio', () => {
  it('defers voice replies and morning digests to the browser TTS path', () => {
    expect(shouldSynthesizeReplyAudio(true, 'Who are you?', false, 'I am Sage.')).toBe(true);
    expect(shouldSynthesizeReplyAudio(false, 'Show me my morning digest.', false, 'Briefing.')).toBe(true);
  });

  it('does not duplicate built-in audio or synthesize unrelated typed replies', () => {
    expect(shouldSynthesizeReplyAudio(true, 'Hello', true, 'Hello, sir.')).toBe(false);
    expect(shouldSynthesizeReplyAudio(false, 'Hello', false, 'Hello, sir.')).toBe(false);
    expect(shouldSynthesizeReplyAudio(true, 'Hello', false, '')).toBe(false);
  });
});

describe('isDigestPrompt', () => {
  it('identifies prompts that may return their own built-in audio', () => {
    expect(isDigestPrompt('Give me my morning digest')).toBe(true);
    expect(isDigestPrompt('Who are you?')).toBe(false);
  });
});

describe('shouldSynthesizeReplyAudio with typed replies enabled', () => {
  it('speaks a typed reply only when the setting is on', () => {
    expect(shouldSynthesizeReplyAudio(false, 'Hello', false, 'Hi.', false)).toBe(false);
    expect(shouldSynthesizeReplyAudio(false, 'Hello', false, 'Hi.', true)).toBe(true);
  });

  it('still never duplicates audio the reply already carries', () => {
    // The digest returns its own clip; speaking it again would double it.
    expect(shouldSynthesizeReplyAudio(false, 'Hello', true, 'Hi.', true)).toBe(false);
  });

  it('has nothing to say about an empty reply', () => {
    expect(shouldSynthesizeReplyAudio(false, 'Hello', false, '', true)).toBe(false);
  });

  it('leaves voice turns unchanged when the setting is off', () => {
    expect(shouldSynthesizeReplyAudio(true, 'Hello', false, 'Hi.', false)).toBe(true);
  });
});

describe('speakableText', () => {
  it('drops fenced code rather than reading it aloud', () => {
    const spoken = speakableText('Try this:\n```python\nprint("x")\n```\nThat works.');
    expect(spoken).not.toContain('print');
    expect(spoken).toContain('Try this');
    expect(spoken).toContain('That works');
  });

  it('drops an unterminated block from a stream that was cut off', () => {
    const spoken = speakableText('Here:\n```js\nconst a = 1;');
    expect(spoken).not.toContain('const');
  });

  it('keeps inline code, which is usually a word', () => {
    expect(speakableText('Run `pytest` now.')).toContain('pytest');
  });

  it('says link text and never the URL', () => {
    const spoken = speakableText('See [the docs](https://example.com/a/b).');
    expect(spoken).toContain('the docs');
    expect(spoken).not.toContain('example.com');
  });

  it('removes bare URLs', () => {
    expect(speakableText('Go to https://example.com now.')).not.toContain('example.com');
  });

  it('strips emphasis and heading marks', () => {
    const spoken = speakableText('## Title\n**bold** and _italic_');
    expect(spoken).not.toContain('#');
    expect(spoken).not.toContain('**');
    expect(spoken).toContain('bold');
  });

  it('drops table rows', () => {
    const spoken = speakableText('Results:\n| a | b |\n| 1 | 2 |\nDone.');
    expect(spoken).toContain('Results');
    expect(spoken).toContain('Done');
    expect(spoken).not.toContain('|');
  });

  it('caps a long reply', () => {
    const spoken = speakableText('word '.repeat(2000));
    expect(spoken.length).toBeLessThanOrEqual(SPOKEN_REPLY_LIMIT);
  });

  it('ends a cut reply on a sentence when one is close to the limit', () => {
    const long = `${'a'.repeat(1000)}. ${'b'.repeat(500)}`;
    const spoken = speakableText(long);
    expect(spoken.endsWith('.')).toBe(true);
  });

  it('returns nothing for nothing', () => {
    expect(speakableText('')).toBe('');
  });

  it('leaves ordinary prose alone', () => {
    expect(speakableText('The weather is fine today.')).toBe(
      'The weather is fine today.',
    );
  });
});
