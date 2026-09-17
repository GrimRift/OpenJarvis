/**
 * One-breath wake word: "Hey Sage, any news on AI?" without waiting.
 *
 * The wake-word stream keeps the last moments of microphone audio. When
 * the wake word is confirmed, that audio -- from before the firing to now,
 * which is the tail of the phrase and whatever followed it -- is handed to
 * the Flux turn as pre-roll, so nothing said during the transcript check
 * or the greeting is lost. The greeting itself plays only if the user then
 * pauses. Deepgram hears the pre-roll too, so the transcript starts with
 * the wake phrase; that is stripped before the text becomes a message.
 */

/** Audio kept from before the confirmation: the detector fires mid-phrase and
 * the transcript check runs up to ~0.9 s, so this covers the whole phrase
 * and the first words after it. */
export const PRE_ROLL_MS = 1500;

/** Nothing heard this long after the wake word: say "Yes, Sir?" as before. */
export const GREETING_PAUSE_MS = 1000;

/** How the wake phrase comes out of Deepgram, as leading tokens. */
const LEAD = new Set(['hey', 'hi', 'hay', 'he', 'a', 'ok', 'okay', 'yo', 'oh']);
const NAME = new Set(['sage', 'stage', 'sayge', 'saige', 'sages', 'says', 'siege', 'seige']);

/**
 * The name by shape, for the spellings no list has: "acage", "usage",
 * "hazage" (all heard for "hey sage" on 17 September). Up to three letters
 * of lead, an s-sound, a vowel run, a soft tail -- and at least four
 * letters, so "say" or "see" as the first word of a real question is not
 * mistaken for it. Only ever applied to a turn opened by the wake word,
 * where the phrase is what the audio starts with.
 */
const NAME_SHAPE = /^[a-z]{0,3}[scz][aeiy]+(?:[gjdnmzv]+e?|ch)?$/;

function isNameToken(token: string): boolean {
  return NAME.has(token) || (token.length >= 4 && NAME_SHAPE.test(token));
}

/**
 * Remove "hey sage" (or how it was heard) from the front of a transcript.
 * "Hey Sage, any news on AI?" -> "any news on AI?"; "Hey Sage." -> "".
 * A transcript that does not start with the phrase is returned as it is.
 */
export function stripWakePhrase(transcript: string): string {
  const text = transcript.trim();
  const tokens = text.split(/\s+/);
  const norm = (t: string) => t.toLowerCase().replace(/[^\p{L}\p{N}']/gu, '');
  let i = 0;
  if (i < tokens.length && LEAD.has(norm(tokens[i]))) i += 1;
  if (i < tokens.length && isNameToken(norm(tokens[i]))) {
    i += 1;
    // "Peace Sage", "Peace in Sage": the name heard twice over.
    if (i < tokens.length && isNameToken(norm(tokens[i]))) i += 1;
  } else {
    // No name: not the phrase (a bare "hey" is the user's own word).
    return text;
  }
  const rest = tokens.slice(i).join(' ').replace(/^[\s,.;:!?—-]+/, '');
  return rest.trim();
}

/** Whether a turn's transcript was only the wake phrase (the user paused). */
export function isOnlyWakePhrase(transcript: string): boolean {
  return transcript.trim() !== '' && stripWakePhrase(transcript) === '';
}
