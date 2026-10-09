import { describe, expect, it } from 'vitest';
import { comparisonTablesToGrids } from './comparison-table';
import { parseDiagram } from './diagram';

const AIRPODS = [
  'Get the AirPods 5.',
  '',
  '| | AirPods 4 | AirPods 5 |',
  '|---|---|---|',
  '| **Price** | ₱10,990 | $129 |',
  '| **Battery with ANC** | Up to 4 hours | Up to 5 hours |',
  '| Fit | Open fit | Open fit |',
  '',
  'That is all.',
].join('\n');

function gridOf(markdown: string) {
  const block = markdown.split('```sage-diagram\n')[1]?.split('\n```')[0];
  return block ? parseDiagram(block) : null;
}

describe('comparisonTablesToGrids', () => {
  it('draws a comparison table as the grid', () => {
    const out = comparisonTablesToGrids(AIRPODS, true);
    const grid = gridOf(out);
    expect(grid?.shape).toBe('comparison');
    expect(grid?.columns).toEqual(['AirPods 4', 'AirPods 5']);
    expect(grid?.rows?.map((r) => r.label)).toEqual(['Price', 'Battery with ANC', 'Fit']);
    expect(grid?.rows?.[0].cells).toEqual(['₱10,990', '$129']);
    expect(out).toContain('Get the AirPods 5.');
    expect(out).toContain('That is all.');
    expect(out).not.toContain('| AirPods 4 |');
  });

  it('leaves a table that is still streaming in alone', () => {
    const partial = AIRPODS.split('\n').slice(0, 5).join('\n');
    expect(comparisonTablesToGrids(partial, false)).toBe(partial);
  });

  it('keeps a table the grid would cut short', () => {
    const long = AIRPODS.replace(
      'Up to 5 hours',
      'Up to 5 hours with the wireless-charging case, or 4 with the USB-C one',
    );
    expect(comparisonTablesToGrids(long, true)).toBe(long);
  });

  it('keeps a list with columns as a table', () => {
    const schedule = '| Day | Time | Room |\n|---|---|---|\n| Mon | 9:40 | 502 |\n| Tue | 1:00 | 301 |';
    expect(comparisonTablesToGrids(schedule, true)).toBe(schedule);
  });

  it('leaves tables inside code blocks alone', () => {
    const code = '```\n| | A | B |\n|---|---|---|\n| x | 1 | 2 |\n| y | 3 | 4 |\n```';
    expect(comparisonTablesToGrids(code, true)).toBe(code);
  });
});
