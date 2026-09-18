/**
 * The diagram Sage draws, as the browser understands it.
 *
 * Sage emits a `sage-diagram` fenced block holding JSON (the prompt side is
 * `openjarvis/prompt/diagrams.py`). Everything here is defensive: the block
 * arrives a character at a time while the answer streams, and a model can
 * always send a shape nobody asked for. A diagram that cannot be read is not
 * an error -- the card simply does not appear.
 */

export type DiagramShape = 'flow' | 'parts' | 'comparison';
export type DiagramMark = 'trigger' | 'result';

export interface DiagramNode {
  label: string;
  note?: string;
  /** A few words of hard detail: "2.0L gasoline", "about 5 years". */
  fact?: string;
  mark?: DiagramMark;
  icon?: string;
}

/** One line of a comparison: what is being compared, then one cell each. */
export interface DiagramRow {
  label: string;
  cells: string[];
  mark?: DiagramMark;
}

export interface Diagram {
  shape: DiagramShape;
  title: string;
  subject?: string;
  /** comparison: the things being weighed against each other. */
  columns?: string[];
  /** comparison: one row per dimension that matters. */
  rows?: DiagramRow[];
  nodes: DiagramNode[];
}

/** Beyond this a diagram is a list wearing boxes; the prose says the rest. */
export const MAX_NODES = 8;
/** Three cars did not fit in two columns, which is what made a comparison
 * read as thin: "Vios / Civic" was crushed into one side. */
export const MAX_COLUMNS = 4;
/** Enough dimensions to be worth reading, few enough to stay a glance. */
export const MAX_ROWS = 6;
/** Colour only means something while it is rare (the user's rule). */
export const MAX_MARKS = 2;

const SHAPES: DiagramShape[] = ['flow', 'parts', 'comparison'];
const MARKS: DiagramMark[] = ['trigger', 'result'];

function text(value: unknown, limit: number): string {
  return typeof value === 'string' ? value.trim().slice(0, limit) : '';
}

/**
 * Read a `sage-diagram` block. Returns null for anything unusable -- still
 * streaming, not JSON, wrong shape, too few nodes.
 */
export function parseDiagram(source: string): Diagram | null {
  let raw: unknown;
  try {
    raw = JSON.parse(source);
  } catch {
    return null;
  }
  if (!raw || typeof raw !== 'object') return null;
  const data = raw as Record<string, unknown>;

  const shape = SHAPES.includes(data.shape as DiagramShape)
    ? (data.shape as DiagramShape)
    : 'flow';
  const title = text(data.title, 90);
  // A comparison carries a grid instead of boxes, so it is read first and
  // does not need `nodes` at all.
  if (shape === 'comparison') {
    const grid = readGrid(data, title);
    if (grid) return grid;
  }
  if (!Array.isArray(data.nodes)) return null;

  let marks = 0;
  const nodes: DiagramNode[] = [];
  for (const entry of data.nodes) {
    if (!entry || typeof entry !== 'object') continue;
    const item = entry as Record<string, unknown>;
    const label = text(item.label, 42);
    if (!label) continue;
    const node: DiagramNode = { label };
    const note = text(item.note, 90);
    // A note that only repeats the label is noise in a box that small.
    if (note && note.toLowerCase() !== label.toLowerCase()) node.note = note;
    const icon = text(item.icon, 20).toLowerCase();
    if (icon) node.icon = icon;
    const fact = text(item.fact, 40);
    if (fact) node.fact = fact;
    // Marks past the cap are dropped rather than the diagram rejected: the
    // shape is still right, it was only over-coloured.
    if (MARKS.includes(item.mark as DiagramMark) && marks < MAX_MARKS) {
      node.mark = item.mark as DiagramMark;
      marks += 1;
    }
    nodes.push(node);
    if (nodes.length >= MAX_NODES) break;
  }
  if (nodes.length < 2 || !title) return null;

  const diagram: Diagram = { shape, title, nodes };

  if (shape === 'parts') {
    const subject = text(data.subject, 42);
    // Parts of nothing is a flow that mislabelled itself.
    if (!subject) return { ...diagram, shape: 'flow' };
    diagram.subject = subject;
  }

  // A comparison whose grid could not be read is still worth drawing as the
  // points it made, so it falls back rather than vanishing.
  if (shape === 'comparison') return { ...diagram, shape: 'flow' };

  return diagram;
}

/**
 * The grid of a comparison: columns are the things weighed up, rows the
 * dimensions that matter.
 *
 * Three cars into two columns is what made the first version read as thin --
 * "Vios / Civic" ended up sharing a heading. Up to four columns now, and a
 * row is padded or trimmed to match them so a short row cannot shift the
 * grid sideways.
 */
function readGrid(data: Record<string, unknown>, title: string): Diagram | null {
  if (!title) return null;
  const rawColumns = Array.isArray(data.columns) ? data.columns : [];
  const columns = rawColumns
    .map((column) => text(column, 28))
    .filter(Boolean)
    .slice(0, MAX_COLUMNS);
  if (columns.length < 2) return null;

  const rawRows = Array.isArray(data.rows) ? data.rows : [];
  let marks = 0;
  const rows: DiagramRow[] = [];
  for (const entry of rawRows) {
    if (!entry || typeof entry !== 'object') continue;
    const item = entry as Record<string, unknown>;
    const label = text(item.label, 28);
    const rawCells = Array.isArray(item.cells) ? item.cells : [];
    if (!label || rawCells.length === 0) continue;
    const cells = columns.map((_, i) => text(rawCells[i], 48));
    const row: DiagramRow = { label, cells };
    if (MARKS.includes(item.mark as DiagramMark) && marks < MAX_MARKS) {
      row.mark = item.mark as DiagramMark;
      marks += 1;
    }
    rows.push(row);
    if (rows.length >= MAX_ROWS) break;
  }
  if (rows.length < 2) return null;

  return { shape: 'comparison', title, columns, rows, nodes: [] };
}

/** Words too common to identify anything. */
const STOP = new Set([
  'the', 'and', 'for', 'with', 'from', 'into', 'that', 'this', 'your', 'gets',
  'forms', 'form', 'make', 'makes', 'made', 'step', 'first', 'then', 'next',
  'what', 'when', 'where', 'which', 'onto', 'over', 'under', 'about', 'more',
]);

function keywords(label: string): string[] {
  return label
    .toLowerCase()
    .split(/[^\p{L}\p{N}]+/u)
    .filter((word) => word.length >= 4 && !STOP.has(word));
}

/**
 * Which node Sage is talking about right now, or -1 when it cannot be told.
 *
 * The user asked for a highlight only when it is confident, so a node is
 * chosen only on a keyword that belongs to it ALONE -- a word two steps share
 * proves nothing -- and the latest such word spoken wins. Everything else
 * leaves the diagram unlit, which reads as "Sage is explaining" rather than
 * lighting the wrong box.
 */
export function activeNodeIndex(spoken: string, nodes: DiagramNode[]): number {
  const said = (spoken || '').toLowerCase();
  if (!said) return -1;

  const perNode = nodes.map((node) => keywords(node.label));
  const seen = new Map<string, number>();
  for (const words of perNode) {
    for (const word of new Set(words)) seen.set(word, (seen.get(word) ?? 0) + 1);
  }

  let best = -1;
  let bestAt = -1;
  perNode.forEach((words, index) => {
    for (const word of words) {
      if (seen.get(word) !== 1) continue;
      const at = said.lastIndexOf(word);
      if (at > bestAt) {
        bestAt = at;
        best = index;
      }
    }
  });
  return best;
}

/**
 * Which end of a serpentine row the flow leaves from.
 *
 * Rows alternate direction, so row 0 runs left-to-right and hands down on the
 * right, row 1 runs back and hands down on the left. The wrap arrow was drawn
 * centred, which pointed down out of the gap between two boxes and read as a
 * step that wasn't there.
 */
export function rowEndsOn(rowIndex: number): 'left' | 'right' {
  return rowIndex % 2 === 0 ? 'right' : 'left';
}

/**
 * "Close the diagram", said out loud while Sage is still explaining.
 *
 * This is a command to the screen, not an interruption of the answer, so it
 * must be recognised BEFORE barge-in judges the same words -- otherwise
 * asking for the picture to go away also cuts Sage off mid-sentence, which is
 * the opposite of what was asked for.
 *
 * Deliberately narrow: a real command is short, so anything past six words is
 * someone talking. "Close the deal" names no diagram and does not match.
 */
const CLOSE_VERB = /\b(?:close|hide|dismiss|remove|get rid of)\b/;
const CLOSE_TARGET =
  /\b(?:diagram|illustration|drawing|chart|graphic|overlay|picture|it|that|this)\b/;

export const CLOSE_COMMAND_MAX_WORDS = 6;

function heard(text: string): string {
  return (text || '')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

export function isCloseDiagramCommand(text: string): boolean {
  const said = heard(text);
  if (!said) return false;
  if (said.split(' ').length > CLOSE_COMMAND_MAX_WORDS) return false;
  const verb = CLOSE_VERB.exec(said);
  if (!verb) return false;
  // Anything may sit between the verb and its target. Deepgram put a phantom
  // "to" in the middle -- "Close to the diagram" -- and an exact-article rule
  // missed it, which let barge-in cut Sage off instead (18 September trace).
  // The six-word cap is what keeps this from matching ordinary speech.
  return CLOSE_TARGET.test(said.slice(verb.index + verb[0].length));
}

/**
 * Whether this partial could still turn into "close the diagram".
 *
 * Barge-in judges every partial, so "close the" alone can reach the cutting
 * threshold before the word "diagram" has even arrived -- the command would
 * interrupt Sage on its way to asking not to. While a diagram is open, a
 * partial that still might be the command is left unjudged until the turn
 * finishes.
 */
export function mayBecomeCloseDiagramCommand(text: string): boolean {
  const said = heard(text);
  if (!said) return false;
  if (said.split(' ').length > CLOSE_COMMAND_MAX_WORDS) return false;
  return CLOSE_VERB.test(said);
}

/** What to tell the server this turn, from the two Settings switches. */
export function diagramMode(enabled: boolean, automatic: boolean): 'auto' | 'on-request' | 'off' {
  if (!enabled) return 'off';
  return automatic ? 'auto' : 'on-request';
}
