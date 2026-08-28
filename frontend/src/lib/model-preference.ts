/**
 * Which model a new chat should start on.
 *
 * Cloud is not just smarter here, it is dramatically faster at the prompt
 * sizes Sage actually sends. Measured on the real setup, same question,
 * ~6,200 input tokens: qwen3.5:4b took 11.7s, gpt-5.6-luna took 1.9s. Local
 * pays per token of input while cloud stays flat, and every real turn carries
 * the system prompt, tool schemas and injected memory — 5,000 tokens at the
 * very least. Local only wins below roughly 1,500 tokens, which never happens.
 */

export const DEFAULT_CLOUD_MODEL = 'gpt-5.6-luna';
export const DEFAULT_LOCAL_MODEL = 'qwen3.5:4b';

export interface ModelPreference {
  preferCloudModel: boolean;
  cloudModel: string;
  localModel: string;
}

/**
 * Pick a starting model from what the server actually offers.
 *
 * Falls through to local whenever the cloud model is absent — no API key, no
 * credit, or offline — so preferring cloud can never leave Sage with nothing
 * to answer on.
 */
export function preferredModelId(
  chatModelIds: readonly string[],
  pref: ModelPreference,
): string {
  const has = (id: string) => Boolean(id) && chatModelIds.includes(id);

  if (pref.preferCloudModel && has(pref.cloudModel)) return pref.cloudModel;
  if (has(pref.localModel)) return pref.localModel;
  return chatModelIds[0] ?? '';
}

/** The model the preference toggle should switch to right now. */
export function modelForToggle(
  chatModelIds: readonly string[],
  pref: ModelPreference,
  preferCloud: boolean,
): string {
  return preferredModelId(chatModelIds, {
    ...pref,
    preferCloudModel: preferCloud,
  });
}
