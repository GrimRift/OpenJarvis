import { describe, expect, it } from 'vitest';
import { countdown, digitsOf, settingsForPhone, spacedCode } from './pairing';

describe('pairing helpers', () => {
  it('keeps only the six digits typed', () => {
    expect(digitsOf('123 456')).toBe('123456');
    expect(digitsOf('12-34-56-7')).toBe('123456');
    expect(digitsOf('abc')).toBe('');
  });

  it('shows the code in two halves', () => {
    expect(spacedCode('123456')).toBe('123 456');
    expect(spacedCode('12')).toBe('12');
  });

  it('counts down in minutes and seconds', () => {
    expect(countdown(300)).toBe('5:00');
    expect(countdown(65.2)).toBe('1:06');
    expect(countdown(-3)).toBe('0:00');
  });
});

describe('settingsForPhone', () => {
  it("takes the PC's voice setup and leaves its mics, key and address", () => {
    const phone = settingsForPhone({
      speechEnabled: true,
      ttsVoiceId: 'frieren',
      sttProvider: 'flux',
      speakTypedReplies: true,
      apiKey: 'secret',
      apiUrl: 'http://localhost:8000',
      micDeviceId: 'pd100x',
      micBoost: 2,
      noiseSuppression: 'strong',
    });
    expect(phone).toEqual({
      speechEnabled: true,
      ttsVoiceId: 'frieren',
      sttProvider: 'flux',
      speakTypedReplies: true,
    });
  });
});
