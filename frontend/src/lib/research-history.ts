/**
 * The recent conversation, for Deep Research.
 *
 * Deep Research used to get the new question alone. On 9 October, after two
 * answers about running local AI models on an RTX 5060 Ti and 4060 Ti, "so
 * the 4060ti is twice slower than 5060ti?" was answered with a gaming
 * benchmark -- "16% faster in 21 games" -- because nothing said what "twice"
 * referred to. The last few questions and answers go with it now, trimmed:
 * they say what a follow-up is about, the research itself finds the facts.
 */
export const RESEARCH_HISTORY_MESSAGES = 6;
export const RESEARCH_HISTORY_CHARS = 1500;

export interface HistoryTurn {
  role: 'user' | 'assistant';
  content: string;
}

export function researchHistory(
  messages: ReadonlyArray<{ role: string; content: string; moment?: unknown }>,
): HistoryTurn[] {
  const turns = messages
    .filter(
      (m) =>
        (m.role === 'user' || m.role === 'assistant') &&
        !m.moment &&
        typeof m.content === 'string' &&
        m.content.trim(),
    )
    .map((m) => ({
      role: m.role as 'user' | 'assistant',
      content:
        m.content.length > RESEARCH_HISTORY_CHARS
          ? `${m.content.slice(0, RESEARCH_HISTORY_CHARS)}...`
          : m.content,
    }));
  return turns.slice(-RESEARCH_HISTORY_MESSAGES);
}
