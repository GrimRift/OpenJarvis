import { describe, expect, it } from 'vitest';
import { hastText, keepOnOneLine } from './table-cell';

describe('keepOnOneLine', () => {
  it('keeps a header label and a problem list whole', () => {
    expect(keepOnOneLine('Problem(s)')).toBe(true);
    expect(keepOnOneLine('7, 12, 31, 36')).toBe(true);
  });

  it('lets prose wrap', () => {
    expect(keepOnOneLine('Enter the expression with variables, then substitute.')).toBe(false);
    expect(keepOnOneLine('Calculator shortcut to remember')).toBe(false);
  });

  it('leaves an empty cell alone', () => {
    expect(keepOnOneLine('   ')).toBe(false);
  });
});

describe('hastText', () => {
  it('joins text through inline markup', () => {
    const cell = {
      type: 'element',
      children: [
        { type: 'element', children: [{ type: 'text', value: 'STAT' }] },
        { type: 'text', value: ' → Lin' },
      ],
    };
    expect(hastText(cell)).toBe('STAT → Lin');
  });
});
