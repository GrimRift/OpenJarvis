const DIGEST_PROMPT = /\b(?:morning digest|daily briefing|morning briefing)\b/i;

/** Decide whether the browser should synthesize audio after text is visible. */
export function shouldSynthesizeReplyAudio(
  wasVoice: boolean,
  prompt: string,
  hasBuiltInAudio: boolean,
  response: string,
): boolean {
  return Boolean(
    response &&
      !hasBuiltInAudio &&
      (wasVoice || DIGEST_PROMPT.test(prompt)),
  );
}
