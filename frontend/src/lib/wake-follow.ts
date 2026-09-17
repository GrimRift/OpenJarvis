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
export const PRE_ROLL_MS = 2500;

/** Nothing heard this long after the wake word: say "Yes, Sir?" as before. */
export const GREETING_PAUSE_MS = 1000;
/** The timer never fires sooner than this after the browser hears of the
 * detection, so a first partial has a chance to arrive and cancel it. */
export const GREETING_MIN_DELAY_MS = 150;
/** A turn that ends this soon after the wake word, with this few tokens,
 * is the phrase itself however it was spelt ("ACG.", "age."). */
export const PAUSE_TURN_MS = 1800;
export const PAUSE_TURN_MAX_TOKENS = 2;

/** How long to wait for a pause greeting, given how long ago the phrase
 * ended (the server reports the frames gathered and checks run). */
export function greetingDelayMs(sinceFiringMs: number): number {
  return Math.max(GREETING_MIN_DELAY_MS, GREETING_PAUSE_MS - Math.max(0, sinceFiringMs));
}

/**
 * Whether a fast-follow turn's transcript is the user carrying on past the
 * wake phrase: at least one real word beyond it. Used on partials to cancel
 * the pause greeting, so it cancels only for speech, never for the phrase's
 * own fragments.
 */
export function continuesPastWakePhrase(transcript: string): boolean {
  return /[\p{L}\p{N}]/u.test(stripWakePhrase(transcript));
}

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
 * Words a request can begin with. In a turn opened by the wake word, a
 * short leading token that is none of these is debris of the phrase
 * ("ACGE", "his", "age" -- all heard live), whatever it looks like.
 */
const STARTERS = new Set([
  'what', 'whats', "what's", 'how', 'when', 'where', 'why', 'who', 'which',
  'can', 'could', 'will', 'would', 'is', 'are', 'do', 'did', 'does', 'am',
  'tell', 'set', 'play', 'open', 'close', 'stop', 'show', 'find', 'read',
  'send', 'turn', 'call', 'remind', 'any', 'give', 'get', 'add', 'make',
  'put', 'run', 'say', 'go', 'let', 'i', "i'm", 'im', 'my', 'the', 'a',
  'an', 'in', 'on', 'to', 'good', 'hi', 'hello', 'yes', 'no', 'ok', 'okay',
  'quit', 'pause', 'skip', 'next', 'mute', 'search', 'look', 'check', 'note',
  'list', 'start', 'wake', 'time', 'date', 'news', 'help', 'thanks', 'thank',
  'never', 'not', 'nothing', 'just', 'so', 'and', 'but', 'please', 'be',
]);

function isDebris(token: string): boolean {
  return token.length > 0 && token.length <= 4 && !STARTERS.has(token);
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
  } else if (isDebris(norm(tokens[0])) && (tokens.length === 1 || isDebris(norm(tokens[1])) || STARTERS.has(norm(tokens[1])) || tokens[1].length > 4)) {
    // No recognisable name, but the turn starts with what is left of it:
    // up to two short non-words ("ACGE", "his age") before the request.
    i = 1;
    if (i < tokens.length && isDebris(norm(tokens[i]))) i += 1;
  } else {
    // No name: not the phrase (a bare "hey" is the user's own word).
    return text;
  }
  const rest = tokens.slice(i).join(' ').replace(/^[\s,.;:!?—-]+/, '');
  return rest.trim();
}

/**
 * Whether a turn's transcript was only the wake phrase (the user paused):
 * nothing left once the phrase is stripped, or -- because Deepgram spells a
 * fragment of the phrase any way it likes -- a turn that ended within
 * PAUSE_TURN_MS of the wake word holding no more than two tokens.
 */
export function isOnlyWakePhrase(transcript: string, elapsedMs = Infinity): boolean {
  const text = transcript.trim();
  if (text === '') return false;
  if (stripWakePhrase(text) === '') return true;
  const tokens = text.split(/\s+/).filter((t) => /[\p{L}\p{N}]/u.test(t));
  return elapsedMs < PAUSE_TURN_MS && tokens.length <= PAUSE_TURN_MAX_TOKENS;
}
