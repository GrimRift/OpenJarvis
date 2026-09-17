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
  mark?: DiagramMark;
  icon?: string;
  side?: 0 | 1;
}

export interface Diagram {
  shape: DiagramShape;
  title: string;
  subject?: string;
  sides?: [string, string];
  nodes: DiagramNode[];
}

/** Beyond this a diagram is a list wearing boxes; the prose says the rest. */
export const MAX_NODES = 8;
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
    // Marks past the cap are dropped rather than the diagram rejected: the
    // shape is still right, it was only over-coloured.
    if (MARKS.includes(item.mark as DiagramMark) && marks < MAX_MARKS) {
      node.mark = item.mark as DiagramMark;
      marks += 1;
    }
    node.side = item.side === 1 ? 1 : 0;
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

  if (shape === 'comparison') {
    const sides = Array.isArray(data.sides) ? data.sides : [];
    const left = text(sides[0], 28);
    const right = text(sides[1], 28);
    if (!left || !right) return { ...diagram, shape: 'flow' };
    diagram.sides = [left, right];
    // Both columns must actually have something in them.
    if (!nodes.some((n) => n.side === 0) || !nodes.some((n) => n.side === 1)) {
      return { shape: 'flow', title, nodes };
    }
  }

  return diagram;
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

/** What to tell the server this turn, from the two Settings switches. */
export function diagramMode(enabled: boolean, automatic: boolean): 'auto' | 'on-request' | 'off' {
  if (!enabled) return 'off';
  return automatic ? 'auto' : 'on-request';
}
