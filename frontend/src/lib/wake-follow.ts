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
/** "Hey Sage" takes about 0.7 s; the pre-roll reaches back this far past
 * the firing so the whole phrase is in it, and no further. */
export const PHRASE_MS = 1000;

/** Nothing heard this long after the wake word: say "Yes, Sir?" as before. */
export const GREETING_PAUSE_MS = 1000;
/** The timer never fires sooner than this after the browser hears of the
 * detection, so a first partial has a chance to arrive and cancel it. */
export const GREETING_MIN_DELAY_MS = 150;
/** A turn that ends this soon after the wake word, with this few tokens,
 * is the phrase itself however it was spelt ("ACG.", "age."). */
export const PAUSE_TURN_MS = 1800;
/** The pause greeting is no longer considered this long after the phrase.
 * Real speech cancels it sooner, through the first partial with a word
 * beyond the phrase; this is only the backstop for a room that never
 * measures quiet. It was 2 s, and one wake in five then greeted on the
 * end-of-turn fallback instead, a median 2 s after the phrase against 1 s
 * for the timer (164 wakes, 22 September). */
export const GREETING_GIVE_UP_MS = 4000;
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

/**
 * The name heard as a whole turn: the same shape, but the s-sound may come
 * out as "ch" or "j" -- "Hey Sage" alone was sent to Sage as the request
 * "Change." A one-word turn only: "change" also begins real requests
 * ("change the song"), which have more words.
 */
const WHOLE_NAME_SHAPE = /^[a-z]{0,3}(?:[scz]|ch|j)[aeiy]+(?:[gjdnmzv]+e?|ch)?$/;

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
  } else if (i === 1 && tokens.length === 2 && !STARTERS.has(norm(tokens[1]))) {
    // "Hey, Steve." (heard 22 September): a lead word and one more that
    // begins no request is the name in a spelling no shape predicts. As a
    // partial it cancelled the pause greeting, which then waited for the
    // end of the turn -- 3 s instead of 1. A real request after the phrase
    // is a third token.
    i = 2;
  } else if (isDebris(norm(tokens[0])) && (tokens.length === 1 || isDebris(norm(tokens[1])) || STARTERS.has(norm(tokens[1])) || tokens[1].length > 4)) {
    // No recognisable name, but the turn starts with what is left of it:
    // up to two short non-words ("ACGE", "his age") before the request.
    i = 1;
    if (i < tokens.length && isDebris(norm(tokens[i]))) i += 1;
  } else {
    // No name, but Deepgram closed a sentence after one or two words that
    // begin no request: "Eight inch. Give me my daily briefing." (heard 20
    // September). In a turn the wake word opened, that first sentence is
    // the phrase in some spelling no shape can predict.
    if (tokens.length === 1 && !STARTERS.has(norm(tokens[0])) && WHOLE_NAME_SHAPE.test(norm(tokens[0]))) {
      return '';
    }
    const lead = leadingNonRequestSentence(tokens);
    if (lead === 0) return text;
    i = lead;
  }
  const rest = tokens.slice(i).join(' ').replace(/^[\s,.;:!?—-]+/, '');
  return rest.trim();
}

/**
 * How many leading tokens form a sentence of at most two words, ended by a
 * full stop, of which none starts a request -- or 0 when the turn does not
 * begin that way. Only ever applied to a turn the wake word opened.
 */
function leadingNonRequestSentence(tokens: readonly string[]): number {
  const norm = (t: string) => t.toLowerCase().replace(/[^\p{L}\p{N}']/gu, '');
  for (let n = 1; n <= 2 && n < tokens.length; n++) {
    if (!/[.!?]$/.test(tokens[n - 1])) continue;
    const words = tokens.slice(0, n).map(norm);
    if (words.some((w) => STARTERS.has(w))) return 0;
    return n;
  }
  return 0;
}

/**
 * Whether a turn's transcript was only the wake phrase (the user paused):
 * nothing left once the phrase is stripped, or -- because Deepgram spells a
 * fragment of the phrase any way it likes -- a turn that ended within
 * PAUSE_TURN_MS of the wake word holding no more than two tokens.
 */
export function isOnlyWakePhrase(
  transcript: string,
  elapsedMs = Infinity,
  greetedAlready = false,
): boolean {
  const text = transcript.trim();
  if (text === '') return false;
  if (stripWakePhrase(text) === '') return true;
  const tokens = text.split(/\s+/).filter((t) => /[\p{L}\p{N}]/u.test(t));
  if (tokens.length > PAUSE_TURN_MAX_TOKENS) return false;
  // Once Sage has greeted, the microphone has already been quiet for a
  // second since the wake word -- the user said nothing. So a stray word
  // arriving in that turn is the phrase misheard, whatever it was spelt as.
  // "addition." was sent as a message because it landed 1849 ms in, 49 ms
  // past the window; the greeting is the better evidence than the clock.
  //
  // ONE stray word, though, not two: the greeting asked a question, and the
  // answer to it is often short. Two tokens were being discarded whole, so
  // "what's up" after "Yes, Sir?" did nothing at all while "what is up",
  // three tokens, went through. Past the greeting the clock means nothing
  // either -- the user is answering, however long they took.
  // Two tokens that BEGIN like a request are one, greeted or not. Eager
  // end-of-turn can close "Hey Sage" on its own and hand "what's up" over
  // as the next turn a second later; and after "Yes, Sir?" the answer is
  // often two words. "ACG age" and "Eight inch" begin no request.
  const first = tokens[0].toLowerCase().replace(/[^\p{L}\p{N}']/gu, '');
  if (tokens.length === 2 && STARTERS.has(first)) return false;
  return greetedAlready || elapsedMs < PAUSE_TURN_MS;
}

/**
 * The transcript without Sage's own greeting in it. The microphone keeps
 * listening while "Yes, Sir?" plays, so a question said over it is heard --
 * it was discarded, and had to be asked twice (24 September) -- and echo
 * cancellation leaves some of the greeting in the audio. Its words ("Hello,
 * Sir", "Yes, Sir", "Sir") are taken out wherever they landed.
 */
export function stripGreetingEcho(transcript: string): string {
  return transcript
    .replace(/\b(?:hello|hi|yes|yeah)[,.!?]*\s+sir\b[,.!?]*/gi, ' ')
    .replace(/^\s*sir\b[,.!?]*/i, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}
