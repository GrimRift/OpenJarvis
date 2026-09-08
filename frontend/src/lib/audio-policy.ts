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
 * How much of a reply is worth reading out before it stops being useful.
 * Long enough for a real answer, short enough not to trap the listener.
 */
export const SPOKEN_REPLY_LIMIT = 1200;

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
