import { describe, expect, it } from 'vitest';
import { rehypeQuotes, splitQuotes } from './rehype-quotes';

const quoted = (nodes: ReturnType<typeof splitQuotes>) =>
  (nodes ?? []).filter((n) => n.tagName === 'span').map((n) => n.children?.[0].value);

describe('splitQuotes', () => {
  it('wraps straight and curly quoted phrases, marks included', () => {
    expect(quoted(splitQuotes('uses the "dark matter of history" as a metaphor'))).toEqual([
      '"dark matter of history"',
    ]);
    expect(quoted(splitQuotes('The name means “in a nutshell” in German.'))).toEqual([
      '“in a nutshell”',
    ]);
  });

  it('leaves inch marks and unquoted text alone', () => {
    expect(splitQuotes('a 27" monitor and a 32" one')).toBeNull();
    expect(splitQuotes('no quotes here')).toBeNull();
  });

  it('keeps the surrounding text intact', () => {
    const nodes = splitQuotes('say "hi", then go')!;
    const text = nodes.map((n) => n.value ?? n.children?.[0].value).join('');
    expect(text).toBe('say "hi", then go');
  });
});

describe('rehypeQuotes', () => {
  it('skips code and bold', () => {
    const tree = {
      type: 'root',
      children: [
        { type: 'element', tagName: 'code', children: [{ type: 'text', value: 'x = "a"' }] },
        { type: 'element', tagName: 'strong', children: [{ type: 'text', value: '"Title"' }] },
      ],
    };
    rehypeQuotes()(tree);
    expect(JSON.stringify(tree)).not.toContain('sage-quote');
  });
});
