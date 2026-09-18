/**
 * Barge-in: the user talks over Sage, and Sage stops to listen.
 *
 * The microphone stays open while a voice reply plays, so Deepgram hears
 * two things: the user, and whatever of Sage's own voice survives the
 * browser's echo cancellation -- plus the desk fan and the keyboard, for
 * which it opens turns too. So nothing here reacts to speech *starting*.
 * The only input is transcribed words, weighed by Deepgram's confidence
 * in each, and the decision is one of three: cut the reply, keep
 * listening, or reject the turn outright.
 *
 * A turn is rejected when its words are a run of what Sage itself has
 * said in this reply (echo), when Deepgram is not sure of them, or when
 * they are garbage. It cuts on one confident stop word -- "stop", "wait",
 * "hold on" -- or on enough confident words for the chosen mode. Anything
 * else waits for the next partial.
 *
 * Pure, so every rule is tested without a microphone (docs/barge-in-v2.md).
 */

export type BargeMode = 'conservative' | 'balanced' | 'sensitive';

export const BARGE_MODES: readonly BargeMode[] = ['conservative', 'balanced', 'sensitive'];
export const DEFAULT_BARGE_MODE: BargeMode = 'conservative';

export interface ModeThresholds {
  /** Confident words needed to cut. */
  words: number;
  /** Mean confidence over the counted words. */
  confidence: number;
  /** A single word this sure cuts alone (sensitive only). */
  singleWordConfidence: number | null;
}

export const MODE_THRESHOLDS: Record<BargeMode, ModeThresholds> = {
  conservative: { words: 3, confidence: 0.7, singleWordConfidence: null },
  balanced: { words: 2, confidence: 0.6, singleWordConfidence: null },
  sensitive: { words: 2, confidence: 0.5, singleWordConfidence: 0.85 },
};

/** A stop word below this confidence is not trusted on its own. */
export const STOP_WORD_CONFIDENCE = 0.8;
/** Words Deepgram is less sure of than this are not counted at all. */
export const COUNTED_WORD_CONFIDENCE = 0.5;

/** One transcribed word with Deepgram's confidence in it. */
export interface FluxWord {
  word: string;
  confidence: number;
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

export type BargeVerdict =
  | { decision: 'cut'; reason: 'stop-word' | 'words' | 'single-word' }
  | { decision: 'reject'; reason: 'echo' | 'low-confidence' | 'garbled' }
  | { decision: 'wait'; reason: 'no-words' | 'too-few' };

/** The words that cut a reply alone; "sage" is not one, by decision. */
const FAST_STOP_PHRASES = ['stop', 'wait', 'hold on', 'hang on', 'pause', 'enough'];

function normalise(word: string): string {
  return word.toLowerCase().replace(/[^\p{L}\p{N}']+/gu, '');
}

export function wordCount(transcript: string): number {
  return transcript.trim().split(/\s+/).filter((w) => /[\p{L}\p{N}]/u.test(w)).length;
}

/** The words worth judging: real tokens Deepgram is at least half sure of. */
export function countedWords(words: readonly FluxWord[]): FluxWord[] {
  return words.filter(
    (w) => /[\p{L}\p{N}]/u.test(w.word) && w.confidence >= COUNTED_WORD_CONFIDENCE,
  );
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

/**
 * Of the heard words, the share that must be found in Sage's text, in
 * order. Three fifths: Deepgram's partials of an echo are mangled at the
 * edges ("before I change" for "before changing anything"), and a verdict
 * of echo is not final -- the next partial is judged again, so a person
 * whose first words happened to be Sage's is cut a word later.
 */
export const ECHO_MATCH_SHARE = 0.6;
/** Consecutive matches may be this many of Sage's words apart. */
const ECHO_MAX_GAP = 2;

// Stopping at twelve made the table inconsistent with itself: hearing Sage's
// own "26%" back as "twelve to sixteen", "twelve" normalised to a digit and
// "sixteen" did not, so neither could line up with anything. The rest of the
// teens and the tens cost nothing and close that gap.
const NUMBER_WORDS: Record<string, string> = {
  zero: '0', one: '1', two: '2', three: '3', four: '4', five: '5', six: '6',
  seven: '7', eight: '8', nine: '9', ten: '10', eleven: '11', twelve: '12',
  thirteen: '13', fourteen: '14', fifteen: '15', sixteen: '16',
  seventeen: '17', eighteen: '18', nineteen: '19', twenty: '20',
  thirty: '30', forty: '40', fifty: '50', sixty: '60', seventy: '70',
  eighty: '80', ninety: '90',
};

/** "changing", "changed", "changes", "change" all compare equal. */
function stem(word: string): string {
  let w = word;
  for (const suffix of ['ing', 'ed', 'es', 's']) {
    if (w.length > suffix.length + 3 && w.endsWith(suffix)) {
      w = w.slice(0, -suffix.length);
      break;
    }
  }
  return w.length > 4 && w.endsWith('e') ? w.slice(0, -1) : w;
}

/**
 * The comparable forms of one written or heard word. Deepgram hears
 * "OpenJarvis" as "open jarvis" and Sage writes "won’t" with a curly
 * apostrophe where Deepgram writes "won't", so camel case is split, every
 * apostrophe dropped, number words made numerals, and inflections stemmed.
 */
export function echoTokens(word: string): string[] {
  return (
    word
      .replace(/([\p{Ll}\p{N}])(\p{Lu})/gu, '$1 $2')
      // Apostrophes vanish rather than split: "won't" is one word, and
      // splitting it gave "won" + "t", which matched nothing.
      .replace(/['’]/g, '')
      // Every other punctuation mark SEPARATES. Stripping it instead turned
      // "11:00" into "1100", so Sage saying "eleven" could never line up with
      // its own "11:00" -- one missed token, and hearing itself back read as
      // the user talking (18 September: Sage cut its own answer off).
      .split(/[^\p{L}\p{N}]+/u)
      .filter(Boolean)
      .map((part) => part.toLowerCase())
      .map((part) => NUMBER_WORDS[part] ?? stem(part))
  );
}

/** A verbatim run this long is Sage's own sentence, not a coincidence. */
export const ECHO_RUN = 5;

/** Whether the heard words contain a run of ECHO_RUN consecutive said words. */
function hasVerbatimRun(heard: readonly string[], said: readonly string[]): boolean {
  if (heard.length < ECHO_RUN) return false;
  for (let start = 0; start + ECHO_RUN <= heard.length; start++) {
    const run = heard.slice(start, start + ECHO_RUN);
    for (let at = 0; at + ECHO_RUN <= said.length; at++) {
      let same = true;
      for (let k = 0; k < ECHO_RUN; k++) {
        if (said[at + k] !== run[k]) {
          same = false;
          break;
        }
      }
      if (same) return true;
    }
  }
  return false;
}

/**
 * Whether the words are what Sage has said in this reply, heard back.
 * Sage's own voice past echo cancellation comes back as its own sentences,
 * give or take a word the recogniser dropped or spelt differently; a
 * person adds words of their own. So most of the heard words must appear
 * in Sage's text, in order, close together -- not all of them, and not
 * adjacent. Deepgram is fully confident in an echo (1.00 on every word
 * in the trace), so confidence is no help here; only the words are.
 */
export function isEchoOf(words: readonly FluxWord[], spokenText: string): boolean {
  const heard = words.flatMap((w) => echoTokens(w.word));
  if (heard.length === 0) return false;
  const said = spokenText.split(/\s+/).flatMap(echoTokens);
  if (said.length === 0) return false;
  // Six words of Sage's own sentence came back in order and it still counted
  // as the user, because two garbled words put the share a single token under
  // the bar. A long verbatim run settles it on its own.
  if (hasVerbatimRun(heard, said)) return true;
  const needed = Math.max(1, Math.ceil(heard.length * ECHO_MATCH_SHARE));
  // From each place Sage's text could start, walk the heard words and
  // count how many land in order within the gap.
  for (let start = 0; start < said.length; start++) {
    let matched = 0;
    let pos = start;
    for (const word of heard) {
      const limit = Math.min(said.length, pos + ECHO_MAX_GAP + 1);
      let found = -1;
      for (let k = pos; k < limit; k++) {
        if (said[k] === word) {
          found = k;
          break;
        }
      }
      if (found === -1) continue;
      matched += 1;
      pos = found + 1;
      if (matched >= needed) return true;
    }
  }
  return false;
}

/**
 * How much of what was heard also appears in what Sage just said.
 *
 * Deliberately weaker than `isEchoOf`: no order, no gap, stop words dropped.
 * It answers a different question. `isEchoOf` guards CUTTING a reply, where
 * being wrong costs one interruption. This guards SENDING the words on as a
 * new question, where being wrong cost a runaway loop -- Sage heard its own
 * answer, replied to it, heard that, and kept going for four turns.
 *
 * The asymmetry is the point: cutting wrongly is cheap and recoverable,
 * answering your own echo is neither.
 */
export function echoOverlap(words: readonly FluxWord[], spokenText: string): number {
  const heard = words
    .flatMap((w) => echoTokens(w.word))
    .filter((t) => t.length >= 4 && !ECHO_COMMON.has(t));
  if (heard.length === 0) return 0;
  const said = new Set(spokenText.split(/\s+/).flatMap(echoTokens));
  const hits = heard.filter((token) => said.has(token)).length;
  return hits / heard.length;
}

/** Words too ordinary for their presence in both texts to mean anything. */
const ECHO_COMMON = new Set([
  'that', 'this', 'with', 'from', 'what', 'when', 'would', 'could', 'have',
  'they', 'them', 'your', 'about', 'there', 'their', 'then', 'than', 'been',
  'will', 'just', 'like', 'know', 'want', 'make', 'more', 'some', 'also',
]);

/** Share of distinctive words that must reappear before a turn is treated as
 * Sage's own voice rather than a question. */
export const ECHO_SEND_SHARE = 0.34;

/**
 * Whether a turn heard while Sage was speaking should be ANSWERED.
 *
 * Only consulted for turns that arrived over Sage's own voice; a turn the
 * user opened themselves is never judged this way.
 */
export function isLoopedBack(words: readonly FluxWord[], spokenText: string): boolean {
  if (!spokenText) return false;
  const distinctive = words
    .flatMap((w) => echoTokens(w.word))
    .filter((t) => t.length >= 4 && !ECHO_COMMON.has(t));
  // One shared word is a coincidence; two is Sage reading its own sentence.
  if (distinctive.length < 2) return false;
  return echoOverlap(words, spokenText) >= ECHO_SEND_SHARE;
}

/** One token said over and over is the recogniser stuttering, not a person. */
export function isGarbled(words: readonly FluxWord[]): boolean {
  const heard = words.map((w) => normalise(w.word)).filter(Boolean);
  if (heard.length < 3) return false;
  return heard.every((w) => w === heard[0]);
}

function mean(values: readonly number[]): number {
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0;
}

/**
 * Judge one partial transcript against what Sage has spoken so far in
 * this reply. `spokenText` is the whole reply's text sent to the
 * synthesiser, not a window of it: an echo can only be of this reply.
 * Echo is checked before the stop words on purpose -- a reply that says
 * "wait for the concrete to cure" must not stop itself.
 */
export function judge(
  words: readonly FluxWord[],
  spokenText: string,
  mode: BargeMode = DEFAULT_BARGE_MODE,
): BargeVerdict {
  const counted = countedWords(words);
  if (counted.length === 0) return { decision: 'wait', reason: 'no-words' };
  if (isGarbled(counted)) return { decision: 'reject', reason: 'garbled' };
  if (isEchoOf(counted, spokenText)) return { decision: 'reject', reason: 'echo' };
  if (hasConfidentStopWord(counted)) return { decision: 'cut', reason: 'stop-word' };
  const t = MODE_THRESHOLDS[mode];
  if (
    t.singleWordConfidence !== null &&
    counted.length === 1 &&
    counted[0].confidence >= t.singleWordConfidence
  ) {
    return { decision: 'cut', reason: 'single-word' };
  }
  if (counted.length < t.words) return { decision: 'wait', reason: 'too-few' };
  if (mean(counted.map((w) => w.confidence)) < t.confidence) {
    return { decision: 'reject', reason: 'low-confidence' };
  }
  return { decision: 'cut', reason: 'words' };
}

/** Whether this partial should cut the reply now. */
export function shouldInterrupt(
  state: BargeState,
  words: readonly FluxWord[],
  spokenText: string,
  mode: BargeMode = DEFAULT_BARGE_MODE,
): boolean {
  if (!state.enabled || !state.sageSpeaking || !state.voiceReply || state.triggered) {
    return false;
  }
  return judge(words, spokenText, mode).decision === 'cut';
}

/**
 * Whether an end-of-turn that arrives while Sage is still speaking is echo
 * or noise rather than the user: the turn never reached a cut, so nothing
 * in it was ever judged to be a person.
 */
export function isEchoTurn(state: BargeState): boolean {
  return state.enabled && state.sageSpeaking && !state.triggered;
}

/** Text appended to a reply that was cut, so the transcript and the model both know. */
export const INTERRUPTED_MARK = '\n\n_(interrupted)_';

/**
 * Voice-log wording for a verdict, or null when nothing is worth a line:
 * the fan and the keyboard produce turns with no words, and those stay in
 * the trace only.
 */
export function describeVerdict(verdict: BargeVerdict, transcript: string): string | null {
  const said = transcript.trim();
  switch (verdict.decision) {
    case 'cut':
      return verdict.reason === 'stop-word'
        ? `You stopped Sage: "${said}"`
        : `You interrupted Sage: "${said}"`;
    case 'reject':
      if (!said) return null;
      return `Ignored while Sage spoke: "${said}" (${verdict.reason.replace('-', ' ')})`;
    case 'wait':
      return null;
  }
}

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
