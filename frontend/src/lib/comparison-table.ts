/**
 * A comparison table, drawn as the comparison grid.
 *
 * Comparisons used to be asked for as a `sage-diagram` JSON block "instead
 * of a markdown table". The model writes tables by habit, so on 9 October it
 * started one, remembered the rule, and argued with itself in the reply:
 * "Wait no diagram required JSON comparison. Must comply..." -- and never
 * finished the answer. Now it writes the table it wants to write, and the
 * screen draws a comparison-shaped one as the grid.
 *
 * Only a table the grid can hold without losing anything is drawn: an empty
 * (or "Feature"-like) top-left cell, 2-4 options, 2-6 rows, short cells.
 * Anything bigger stays a table. Speech reads the table either way
 * (speech/spoken_text.py, differences only).
 */
import { MAX_COLUMNS, MAX_ROWS } from './diagram';

const CORNERS = new Set([
  '', 'feature', 'features', 'spec', 'specs', 'specification', 'specifications',
  'category', 'aspect', 'criteria', 'criterion', 'metric', 'attribute',
  'dimension', 'factor', 'detail', 'details',
]);
const ROW = /^\s*\|.*\|\s*$/;
const DIVIDER = /^\s*\|?[\s:|-]*\|[\s:|-]*$/;
/** The grid's own limits on a column heading, a row label and a cell. */
const HEADING_CHARS = 28;
const CELL_CHARS = 48;

function cells(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.replace(/\*\*|__/g, '').trim());
}

function asGrid(lines: string[]): string | null {
  if (lines.length < 4 || !DIVIDER.test(lines[1])) return null;
  const header = cells(lines[0]);
  const options = header.slice(1);
  if (!CORNERS.has(header[0].toLowerCase())) return null;
  if (options.length < 2 || options.length > MAX_COLUMNS) return null;
  if (options.some((name) => !name || name.length > HEADING_CHARS)) return null;
  const body = lines.slice(2).map(cells);
  if (body.length < 2 || body.length > MAX_ROWS) return null;
  const rows = [];
  for (const row of body) {
    const [label, ...values] = row;
    if (!label || label.length > HEADING_CHARS) return null;
    if (values.length !== options.length) return null;
    if (values.some((value) => value.length > CELL_CHARS)) return null;
    rows.push({ label, cells: values });
  }
  const diagram = {
    shape: 'comparison',
    title: options.join(' versus '),
    columns: options,
    rows,
  };
  return '```sage-diagram\n' + JSON.stringify(diagram) + '\n```';
}

/**
 * *markdown* with each comparison table replaced by a grid block. While a
 * reply is still streaming (*finished* false), a table at the very end may
 * still be growing, so it is left as a table until the reply ends.
 */
export function comparisonTablesToGrids(markdown: string, finished: boolean): string {
  if (!markdown.includes('|')) return markdown;
  const lines = markdown.split('\n');
  const out: string[] = [];
  let fence = false;
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*(```|~~~)/.test(line)) fence = !fence;
    if (fence || !ROW.test(line)) {
      out.push(line);
      i += 1;
      continue;
    }
    let end = i;
    while (end < lines.length && (ROW.test(lines[end]) || DIVIDER.test(lines[end]) && lines[end].includes('|'))) {
      end += 1;
    }
    const table = lines.slice(i, end);
    const complete = end < lines.length || finished;
    const grid = complete ? asGrid(table) : null;
    out.push(...(grid ? [grid] : table));
    i = end;
  }
  return out.join('\n');
}
