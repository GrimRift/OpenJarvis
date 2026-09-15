const DIGEST_PROMPT = /\b(?:morning digest|daily briefing|morning briefing)\b/i;

export function isDigestPrompt(prompt: string): boolean {
  return DIGEST_PROMPT.test(prompt);
}

/**
 * Decide whether the browser should synthesize audio after text is visible.
 *
 * `speakTyped` is the opt-in that lets a typed turn be spoken as well. It is
 * deliberately the last consideration: a voice turn and a digest are spoken
 * whatever it says, and the caller's mute toggle still gates all of this.
 */
export function shouldSynthesizeReplyAudio(
  wasVoice: boolean,
  prompt: string,
  hasBuiltInAudio: boolean,
  response: string,
  speakTyped = false,
): boolean {
  return Boolean(
    response &&
      !hasBuiltInAudio &&
      (wasVoice || isDigestPrompt(prompt) || speakTyped),
  );
}

/**
 * Whether a reply's speech should start while the reply is still being
 * generated. Voice questions always stream; a typed question streams when
 * Speak Typed Replies is on. Before this, typed replies went through the
 * batch path and waited for the whole answer, which read as "the voice got
 * slower" the first time a long answer was asked for by keyboard.
 */
export function shouldStreamReplySpeech(
  wasVoice: boolean,
  prompt: string,
  speakTyped: boolean,
): boolean {
  if (isDigestPrompt(prompt)) return false;
  return wasVoice || speakTyped;
}

/** Returned by pushSpokenDelta once the stream has been finished by the cap. */
export const SPEECH_CAPPED = -1;

/**
 * Feed model deltas to the speech stream, stopping at the spoken limit for
 * typed replies -- at the end of the sentence the limit falls in, never
 * mid-word: a briefing that stopped at "Steam reports Lies of" sounded like
 * a fault, not a choice. Pure over a running count so the cut-off is
 * testable; returns the chars spoken so far, or SPEECH_CAPPED once done.
 */
export function pushSpokenDelta(
  spokenChars: number,
  delta: string,
  capped: boolean,
  stream: { push: (delta: string) => unknown; finish: () => unknown },
  limit: number = SPOKEN_REPLY_LIMIT,
): number {
  if (spokenChars === SPEECH_CAPPED) return SPEECH_CAPPED;
  if (!capped || spokenChars + delta.length < limit) {
    stream.push(delta);
    return spokenChars + delta.length;
  }
  // Past the limit: speak up to and including the next sentence end, then
  // stop. Everything before it in this delta is spoken so the sentence is
  // whole; anything after it is dropped.
  const overshoot = Math.max(0, limit - spokenChars);
  const end = sentenceEndAfter(delta, overshoot);
  if (end === -1) {
    stream.push(delta);
    return spokenChars + delta.length;
  }
  stream.push(delta.slice(0, end));
  stream.finish();
  return SPEECH_CAPPED;
}

/** Index just past the first sentence end at or after *from*, or -1. */
function sentenceEndAfter(text: string, from: number): number {
  for (let i = from; i < text.length; i++) {
    const c = text[i];
    if (c === '\n') return i + 1;
    const next = i + 1 === text.length ? ' ' : text[i + 1];
    if ((c === '.' || c === '!' || c === '?') && /\s/.test(next)) {
      return i + 1;
    }
  }
  return -1;
}

/**
 * How much of a reply is worth reading out before it stops being useful.
 * Long enough for a real answer, short enough not to trap the listener.
 */
export const SPOKEN_REPLY_LIMIT = 3000;

/**
 * Strip a reply down to what is worth hearing.
 *
 * Typed questions get typed answers: fenced code, tables and link targets,
 * none of which survive being read aloud. Speaking a code block character by
 * character is not a degraded experience, it is an unusable one, so those are
 * dropped rather than flattened. The cut is silent -- announcing "reply
 * truncated" would be a second thing to listen to.
 */
export function speakableText(
  markdown: string,
  limit: number = SPOKEN_REPLY_LIMIT,
): string {
  if (!markdown) return '';

  const withoutCode = markdown
    // Fenced blocks, including unterminated ones at the end of a stream.
    .replace(/```[\s\S]*?(?:```|$)/g, ' ')
    // Markdown tables: a row of pipes reads as noise.
    .replace(/^\s*\|.*\|\s*$/gm, ' ')
    // Inline code keeps its content; it is usually a word, not a program.
    .replace(/`([^`]*)`/g, '$1')
    // Links: say the text, never the URL.
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    // Bare URLs left in prose.
    .replace(/https?:\/\/\S+/g, ' ')
    // Emphasis and heading marks are punctuation to the ear.
    .replace(/[*_#>]+/g, ' ')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n{2,}/g, '\n')
    .trim();

  if (withoutCode.length <= limit) return withoutCode;

  // Cut on a sentence if one is near the limit, so the speech ends on a
  // thought rather than mid-word.
  const clipped = withoutCode.slice(0, limit);
  const lastStop = Math.max(
    clipped.lastIndexOf('. '),
    clipped.lastIndexOf('! '),
    clipped.lastIndexOf('? '),
  );
  return (lastStop > limit * 0.6 ? clipped.slice(0, lastStop + 1) : clipped).trim();
}
