const DIGEST_PROMPT = /\b(?:morning digest|daily briefing|morning briefing)\b/i;

export function isDigestPrompt(prompt: string): boolean {
  return DIGEST_PROMPT.test(prompt);
}

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
      (wasVoice || isDigestPrompt(prompt)),
  );
}
