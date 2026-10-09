import { describe, expect, it } from 'vitest';
import { RESEARCH_HISTORY_CHARS, researchHistory } from './research-history';

describe('researchHistory', () => {
  it('keeps the last three questions and answers, oldest first', () => {
    const messages = Array.from({ length: 10 }, (_, i) => ({
      role: i % 2 === 0 ? 'user' : 'assistant',
      content: `m${i}`,
    }));
    expect(researchHistory(messages).map((m) => m.content)).toEqual([
      'm4', 'm5', 'm6', 'm7', 'm8', 'm9',
    ]);
  });

  it('trims a long answer', () => {
    const long = 'x'.repeat(RESEARCH_HISTORY_CHARS + 500);
    const [turn] = researchHistory([{ role: 'assistant', content: long }]);
    expect(turn.content.length).toBe(RESEARCH_HISTORY_CHARS + 3);
  });

  it('leaves out tool results, empty turns and things Sage said on its own', () => {
    const turns = researchHistory([
      { role: 'tool', content: 'raw search text' },
      { role: 'assistant', content: '' },
      { role: 'assistant', content: 'Your 9 AM class starts soon.', moment: true },
      { role: 'user', content: 'how fast is the 5060 ti for local models?' },
    ]);
    expect(turns).toEqual([
      { role: 'user', content: 'how fast is the 5060 ti for local models?' },
    ]);
  });
});
