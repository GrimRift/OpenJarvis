/**
 * Barge-in: the user talks over Sage, and Sage stops to listen.
 *
 * The microphone stays open while a voice reply plays, so Deepgram hears
 * two things: the user, and whatever of Sage's own voice survives the
 * browser's echo cancellation. The policy therefore never acts on "speech
 * started" -- an echo starts speech too -- but on words: once Deepgram has
 * transcribed the second real word while Sage is audibly speaking, the
 * user is talking and the reply is cut. A cough, a chair, or a syllable of
 * echo does not reach two words; a real interruption does within about
 * half a second, which is how people interrupt each other anyway.
 *
 * Pure, so the decision is tested without a microphone.
 *
 * Phase 1 of barge-in v2 (docs/barge-in-v2.md) adds one thing to that
 * rule: a confident stop word on its own -- "stop", "wait", "hold on" --
 * cuts at once, because one such word is what people say when they want
 * the talking to end. Nothing here reacts to speech *starting*: Deepgram
 * opens a turn for the fan, the keyboard and Sage's own voice, so only
 * words, weighed by Deepgram's confidence in them, ever change playback.
 */

export const BARGE_IN_WORDS = 2;

/** A stop word below this confidence is not trusted on its own. */
export const STOP_WORD_CONFIDENCE = 0.8;

/** One transcribed word with Deepgram's confidence in it. */
export interface FluxWord {
  word: string;
  confidence: number;
}

/** The words that cut a reply alone; "sage" is not one, by decision. */
const FAST_STOP_PHRASES = ['stop', 'wait', 'hold on', 'hang on', 'pause', 'enough'];

function normalise(word: string): string {
  return word.toLowerCase().replace(/[^\p{L}\p{N}']+/gu, '');
}

/**
 * Whether the transcript so far contains a stop phrase every word of which
 * Deepgram is confident about. Two-word phrases need both words confident
 * and adjacent.
 */
export function hasConfidentStopWord(
  words: readonly FluxWord[],
  minConfidence = STOP_WORD_CONFIDENCE,
): boolean {
  const norm = words.map((w) => ({ word: normalise(w.word), confidence: w.confidence }));
  return FAST_STOP_PHRASES.some((phrase) => {
    const parts = phrase.split(' ');
    for (let i = 0; i + parts.length <= norm.length; i++) {
      const run = norm.slice(i, i + parts.length);
      if (run.every((w, j) => w.word === parts[j] && w.confidence >= minConfidence)) {
        return true;
      }
    }
    return false;
  });
}

export interface BargeState {
  /** The Settings switch. */
  enabled: boolean;
  /** Whether Sage's reply audio is playing right now. */
  sageSpeaking: boolean;
  /** Whether this reply was to a spoken question (typed ones are not cut). */
  voiceReply: boolean;
  /** Whether this turn already interrupted the reply. */
  triggered: boolean;
}

export function wordCount(transcript: string): number {
  return transcript.trim().split(/\s+/).filter((w) => /[\p{L}\p{N}]/u.test(w)).length;
}

/**
 * Whether this partial transcript should cut the reply now: two real words,
 * or one confident stop word.
 */
export function shouldInterrupt(
  state: BargeState,
  transcript: string,
  words: readonly FluxWord[] = [],
): boolean {
  if (!state.enabled || !state.sageSpeaking || !state.voiceReply || state.triggered) {
    return false;
  }
  return wordCount(transcript) >= BARGE_IN_WORDS || hasConfidentStopWord(words);
}

/** Why a cut happened, for the trace and the Voice log. */
export function interruptReason(words: readonly FluxWord[]): 'stop-word' | 'words' {
  return hasConfidentStopWord(words) ? 'stop-word' : 'words';
}

/**
 * Whether an end-of-turn that arrives while Sage is still speaking is echo
 * rather than the user: the turn never reached the interruption threshold,
 * so nothing in it was ever judged to be a person.
 */
export function isEchoTurn(state: BargeState): boolean {
  return state.enabled && state.sageSpeaking && !state.triggered;
}

/** Text appended to a reply that was cut, so the transcript and the model both know. */
export const INTERRUPTED_MARK = '\n\n_(interrupted)_';

/**
 * Whether an interruption was only "stop talking", not a new question.
 * "Okay, you can stop. No." went to the model as a message and came back
 * as a thirty-minute quiet; the user meant the sentence, not the day. A
 * short utterance made of stop-words is consumed by the cut itself.
 */
const STOP_PHRASES = [
  'stop',
  'wait',
  'hold on',
  'hang on',
  'pause',
  'enough',
  "that's enough",
  'shut up',
  'be quiet',
  'quiet',
  'okay',
  'ok',
  'alright',
  'thanks',
  'thank you',
  'got it',
  'no',
  'yes',
  'fine',
  'sage',
  'you can',
  'can',
  'now',
  'please',
];

export function isStopCommand(transcript: string): boolean {
  const words = transcript
    .toLowerCase()
    .replace(/[^\p{L}\p{N}' ]+/gu, ' ')
    .split(/\s+/)
    .filter(Boolean);
  if (words.length === 0 || words.length > 8) return false;
  const text = words.join(' ');
  if (!/\b(stop|wait|hold on|hang on|pause|enough|shut up|quiet)\b/.test(text)) return false;
  // Every word must belong to the stop vocabulary: "stop and tell me the
  // weather" is a question, not a stop.
  const vocab = new Set(STOP_PHRASES.flatMap((p) => p.split(' ')));
  return words.every((w) => vocab.has(w));
}
